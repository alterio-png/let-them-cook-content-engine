-- G3A.1 shared upload authority. Private, bounded, transactional; no cloud calls.
-- SECURITY DEFINER is restricted to this non-exposed schema because API logins
-- deliberately have no ledger DML and must retain the existing G3A role boundary.
CREATE SCHEMA g3a1_private;
REVOKE ALL ON SCHEMA g3a1_private FROM PUBLIC,anon,authenticated,engine_worker;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='g3a1_upload_reconciler') THEN
  CREATE ROLE g3a1_upload_reconciler NOLOGIN NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
ALTER ROLE g3a1_upload_reconciler NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA g3a1_private TO authenticated,g3a1_upload_reconciler;

CREATE TABLE g3a1_private.upload_limits (
 organization_id uuid PRIMARY KEY REFERENCES public.organizations(id) ON DELETE RESTRICT,
 max_bytes bigint NOT NULL CHECK (max_bytes>0),
 max_count bigint NOT NULL CHECK (max_count>0),
 max_upload_bytes bigint NOT NULL CHECK (max_upload_bytes>0 AND max_upload_bytes<=max_bytes),
 max_ttl_seconds integer NOT NULL DEFAULT 120 CHECK (max_ttl_seconds BETWEEN 1 AND 86400),
 max_lease_seconds integer NOT NULL DEFAULT 60 CHECK (max_lease_seconds BETWEEN 1 AND 3600),
 used_bytes bigint NOT NULL DEFAULT 0 CHECK (used_bytes>=0 AND used_bytes<=max_bytes),
 used_count bigint NOT NULL DEFAULT 0 CHECK (used_count>=0 AND used_count<=max_count)
);
CREATE TABLE g3a1_private.upload_reservations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
 -- Historical ownership must survive membership/auth-user revocation and deletion.
 owner_user_id uuid NOT NULL,
 request_key text NOT NULL CHECK (request_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
 size_bytes bigint NOT NULL CHECK (size_bytes>0),
 sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
 object_key text NOT NULL UNIQUE,
 state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','publishing','uncertain','uploaded','expired')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 lease_token uuid,
 lease_expires_at timestamptz,
 fence bigint NOT NULL DEFAULT 0 CHECK (fence>=0),
 UNIQUE (organization_id,request_key),
 UNIQUE (id,organization_id),
 UNIQUE (id,organization_id,owner_user_id),
 UNIQUE (id,organization_id,owner_user_id,object_key,size_bytes,sha256),
 CHECK (object_key=organization_id::text||'/uploads/'||id::text||'/unvalidated.bin'),
 CHECK (expires_at>created_at),
 CHECK ((state IN ('reserved','expired') AND fence=0 AND lease_token IS NULL AND lease_expires_at IS NULL)
     OR (state='publishing' AND fence>0 AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
     OR (state IN ('uncertain','uploaded') AND fence>0 AND lease_token IS NULL AND lease_expires_at IS NULL))
);
CREATE INDEX upload_reservations_recovery_idx ON g3a1_private.upload_reservations
 (organization_id,state,lease_expires_at,expires_at,id);
CREATE TABLE g3a1_private.upload_receipts (
 id uuid PRIMARY KEY,
 organization_id uuid NOT NULL,
 owner_user_id uuid NOT NULL,
 object_key text NOT NULL UNIQUE,
 size_bytes bigint NOT NULL CHECK (size_bytes>0),
 sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
 generation text CHECK (generation IS NULL OR (length(generation) BETWEEN 1 AND 128)),
 uploaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (id,organization_id,owner_user_id)
  REFERENCES g3a1_private.upload_reservations(id,organization_id,owner_user_id) ON DELETE RESTRICT,
 FOREIGN KEY (id,organization_id,owner_user_id,object_key,size_bytes,sha256)
  REFERENCES g3a1_private.upload_reservations(id,organization_id,owner_user_id,object_key,size_bytes,sha256) ON DELETE RESTRICT,
 CHECK (object_key=organization_id::text||'/uploads/'||id::text||'/unvalidated.bin')
);
CREATE TABLE g3a1_private.upload_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
 reservation_id uuid NOT NULL,
 event_type text NOT NULL CHECK (event_type IN ('reserved','claimed','heartbeat','uncertain','uploaded','expired')),
 fence bigint NOT NULL CHECK (fence>=0),
 error_code text CHECK (error_code IS NULL OR error_code ~ '^[A-Z0-9_]{1,80}$'),
 happened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (reservation_id,organization_id) REFERENCES g3a1_private.upload_reservations(id,organization_id)
);
CREATE INDEX upload_events_org_idx ON g3a1_private.upload_events(organization_id,id);

-- Historical audit never contains a bearer token, raw body, signing key or DSN.
CREATE FUNCTION g3a1_private.guard_upload_reservation() RETURNS trigger
LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
 IF (NEW.id,NEW.organization_id,NEW.owner_user_id,NEW.request_key,NEW.size_bytes,
     NEW.sha256,NEW.object_key,NEW.created_at,NEW.expires_at)
    IS DISTINCT FROM
    (OLD.id,OLD.organization_id,OLD.owner_user_id,OLD.request_key,OLD.size_bytes,
     OLD.sha256,OLD.object_key,OLD.created_at,OLD.expires_at)
    OR NEW.fence<OLD.fence
    OR (OLD.state IN ('expired','uploaded') AND NEW IS DISTINCT FROM OLD)
    OR (OLD.fence>0 AND NEW.state IN ('reserved','expired'))
    OR (NEW.fence>OLD.fence AND (NEW.fence<>OLD.fence+1 OR NEW.state<>'publishing')) THEN
  RAISE EXCEPTION 'immutable upload identity or invalid transition' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER upload_reservation_immutable BEFORE UPDATE ON g3a1_private.upload_reservations
 FOR EACH ROW EXECUTE FUNCTION g3a1_private.guard_upload_reservation();
CREATE FUNCTION g3a1_private.reject_upload_update() RETURNS trigger
LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
 RAISE EXCEPTION 'immutable upload receipt or audit' USING ERRCODE='23514';
END $$;
CREATE TRIGGER upload_receipt_immutable BEFORE UPDATE ON g3a1_private.upload_receipts
 FOR EACH ROW EXECUTE FUNCTION g3a1_private.reject_upload_update();
CREATE TRIGGER upload_event_immutable BEFORE UPDATE ON g3a1_private.upload_events
 FOR EACH ROW EXECUTE FUNCTION g3a1_private.reject_upload_update();

ALTER TABLE g3a1_private.upload_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE g3a1_private.upload_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE g3a1_private.upload_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE g3a1_private.upload_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA g3a1_private FROM PUBLIC,anon,authenticated,engine_worker,g3a1_upload_reconciler;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA g3a1_private FROM PUBLIC,anon,authenticated,engine_worker,g3a1_upload_reconciler;
GRANT SELECT ON g3a1_private.upload_limits,g3a1_private.upload_reservations,g3a1_private.upload_receipts TO authenticated;
CREATE POLICY upload_limits_read ON g3a1_private.upload_limits FOR SELECT TO authenticated
 USING (public.is_org_member(organization_id));
CREATE POLICY upload_reservations_read ON g3a1_private.upload_reservations FOR SELECT TO authenticated
 USING (owner_user_id=(SELECT auth.uid()) AND public.is_org_member(organization_id));
CREATE POLICY upload_receipts_read ON g3a1_private.upload_receipts FOR SELECT TO authenticated
 USING (owner_user_id=(SELECT auth.uid()) AND public.is_org_member(organization_id));

CREATE FUNCTION g3a1_private._receipt_json(p_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path='' AS $$
 SELECT jsonb_build_object('id',r.id,'organization_id',r.organization_id,'owner_user_id',r.owner_user_id,
  'object_key',r.object_key,'size_bytes',r.size_bytes,'sha256',r.sha256,'generation',r.generation,
  'uploaded_at',r.uploaded_at,'state','uploaded_pending_validation')
 FROM g3a1_private.upload_receipts r WHERE r.id=p_id
$$;
CREATE FUNCTION g3a1_private._reservation_json(p_id uuid) RETURNS jsonb
LANGUAGE sql VOLATILE SET search_path='' AS $$
 SELECT jsonb_build_object('id',r.id,'organization_id',r.organization_id,'owner_user_id',r.owner_user_id,
  'request_key',r.request_key,'size_bytes',r.size_bytes,'sha256',r.sha256,'object_key',r.object_key,
  'state',r.state,'created_at',r.created_at,'expires_at',floor(extract(epoch FROM r.expires_at))::bigint,
  'lease_token',r.lease_token,'lease_expires_at',floor(extract(epoch FROM r.lease_expires_at))::bigint,
  'lease_seconds',CASE WHEN r.lease_expires_at IS NULL THEN NULL
   ELSE greatest(0,ceil(extract(epoch FROM r.lease_expires_at-clock_timestamp()))::integer) END,
  'fence',r.fence,'receipt',g3a1_private._receipt_json(r.id))
 FROM g3a1_private.upload_reservations r WHERE r.id=p_id
$$;
-- Every writer takes membership SHARE -> quota UPDATE -> reservation UPDATE.
-- Revocation therefore waits for an already-authorized transaction, and all
-- replicas serialize the same organization quota without process-local locks.
CREATE FUNCTION g3a1_private._lock_org(p_org uuid,p_write boolean,p_internal boolean)
RETURNS void LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
 IF NOT p_internal THEN
  PERFORM g3a_private.lock_api_membership(p_org,p_write);
 END IF;
 PERFORM 1 FROM g3a1_private.upload_limits WHERE organization_id=p_org FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'upload organization unavailable' USING ERRCODE='42501';
 END IF;
END $$;
CREATE FUNCTION g3a1_private._expire_unused(p_org uuid,p_limit integer) RETURNS integer
LANGUAGE plpgsql SET search_path='' AS $$
DECLARE r record; n integer:=0; bytes bigint:=0;
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
  RAISE EXCEPTION 'invalid expiration batch' USING ERRCODE='22023';
 END IF;
 FOR r IN SELECT id,size_bytes FROM g3a1_private.upload_reservations
  WHERE organization_id=p_org AND state='reserved' AND fence=0 AND expires_at<=clock_timestamp()
  ORDER BY id LIMIT p_limit FOR UPDATE
 LOOP
  UPDATE g3a1_private.upload_reservations SET state='expired' WHERE id=r.id;
  INSERT INTO g3a1_private.upload_events(organization_id,reservation_id,event_type,fence)
   VALUES(p_org,r.id,'expired',0);
  n:=n+1; bytes:=bytes+r.size_bytes;
 END LOOP;
 UPDATE g3a1_private.upload_limits SET used_bytes=used_bytes-bytes,used_count=used_count-n
  WHERE organization_id=p_org;
 RETURN n;
END $$;

CREATE FUNCTION g3a1_private.reserve_upload(p_org uuid,p_request_key text,p_size bigint,p_sha text,p_ttl integer DEFAULT 120)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r g3a1_private.upload_reservations%ROWTYPE; q g3a1_private.upload_limits%ROWTYPE;
 new_id uuid; started timestamptz;
BEGIN
 IF p_request_key IS NULL OR p_request_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
  OR p_size IS NULL OR p_size<=0 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
  OR p_ttl IS NULL OR p_ttl NOT BETWEEN 1 AND 86400 THEN
  RAISE EXCEPTION 'invalid upload request' USING ERRCODE='22023';
 END IF;
 PERFORM g3a1_private._lock_org(p_org,true,false);
 PERFORM g3a1_private._expire_unused(p_org,1000);
 SELECT * INTO r FROM g3a1_private.upload_reservations
  WHERE organization_id=p_org AND request_key=p_request_key FOR UPDATE;
 IF FOUND THEN
  IF r.owner_user_id<>auth.uid() OR r.size_bytes<>p_size OR r.sha256<>p_sha THEN
   RAISE EXCEPTION 'idempotency request conflict' USING ERRCODE='23505';
  END IF;
  RETURN g3a1_private._reservation_json(r.id);
 END IF;
 SELECT * INTO q FROM g3a1_private.upload_limits WHERE organization_id=p_org;
 IF p_size>q.max_upload_bytes OR p_size>q.max_bytes-q.used_bytes OR q.used_count>=q.max_count THEN
  RAISE EXCEPTION 'upload quota exhausted' USING ERRCODE='54000';
 END IF;
 started:=clock_timestamp(); new_id:=gen_random_uuid();
 INSERT INTO g3a1_private.upload_reservations
  (id,organization_id,owner_user_id,request_key,size_bytes,sha256,object_key,created_at,expires_at)
 VALUES(new_id,p_org,auth.uid(),p_request_key,p_size,p_sha,
  p_org::text||'/uploads/'||new_id::text||'/unvalidated.bin',started,
  started+make_interval(secs=>least(p_ttl,q.max_ttl_seconds)));
 UPDATE g3a1_private.upload_limits SET used_bytes=used_bytes+p_size,used_count=used_count+1
  WHERE organization_id=p_org;
 INSERT INTO g3a1_private.upload_events(organization_id,reservation_id,event_type,fence)
  VALUES(p_org,new_id,'reserved',0);
 RETURN g3a1_private._reservation_json(new_id);
END $$;

CREATE FUNCTION g3a1_private._claim_upload(p_org uuid,p_id uuid,p_lease integer,p_reconcile boolean,p_internal boolean)
RETURNS jsonb LANGUAGE plpgsql SET search_path='' AS $$
DECLARE r g3a1_private.upload_reservations%ROWTYPE; max_lease integer;
BEGIN
 IF p_lease IS NULL OR p_lease NOT BETWEEN 1 AND 3600 THEN
  RAISE EXCEPTION 'invalid lease duration' USING ERRCODE='22023';
 END IF;
 PERFORM g3a1_private._lock_org(p_org,true,p_internal);
 SELECT * INTO r FROM g3a1_private.upload_reservations WHERE id=p_id AND organization_id=p_org FOR UPDATE;
 IF NOT FOUND OR (NOT p_internal AND r.owner_user_id<>auth.uid()) THEN
  RAISE EXCEPTION 'upload not found' USING ERRCODE='P0002';
 END IF;
 IF r.state IN ('uploaded','expired') THEN RETURN g3a1_private._reservation_json(r.id); END IF;
 IF r.state='reserved' AND r.expires_at<=clock_timestamp() THEN
  UPDATE g3a1_private.upload_reservations SET state='expired' WHERE id=r.id;
  UPDATE g3a1_private.upload_limits SET used_bytes=used_bytes-r.size_bytes,used_count=used_count-1
   WHERE organization_id=p_org;
  INSERT INTO g3a1_private.upload_events(organization_id,reservation_id,event_type,fence)
   VALUES(p_org,r.id,'expired',0);
  RETURN g3a1_private._reservation_json(r.id);
 END IF;
 IF p_reconcile AND r.state='reserved' THEN RETURN g3a1_private._reservation_json(r.id); END IF;
 IF NOT p_reconcile AND r.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'upload grant expired' USING ERRCODE='22023';
 END IF;
 IF r.lease_expires_at>clock_timestamp() THEN
  RAISE EXCEPTION 'upload lease busy' USING ERRCODE='55P03';
 END IF;
 SELECT max_lease_seconds INTO max_lease FROM g3a1_private.upload_limits WHERE organization_id=p_org;
 UPDATE g3a1_private.upload_reservations SET state='publishing',fence=fence+1,
  lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp()+make_interval(secs=>least(p_lease,max_lease))
  WHERE id=r.id;
 INSERT INTO g3a1_private.upload_events(organization_id,reservation_id,event_type,fence)
  VALUES(p_org,r.id,'claimed',r.fence+1);
 RETURN g3a1_private._reservation_json(r.id);
END $$;
CREATE FUNCTION g3a1_private.claim_upload(p_org uuid,p_id uuid,p_lease integer)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._claim_upload(p_org,p_id,p_lease,false,false)
$$;
CREATE FUNCTION g3a1_private.reconcile_own_upload(p_org uuid,p_id uuid,p_lease integer)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._claim_upload(p_org,p_id,p_lease,true,false)
$$;
CREATE FUNCTION g3a1_private.internal_claim_reconciliation(p_org uuid,p_id uuid,p_lease integer)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._claim_upload(p_org,p_id,p_lease,true,true)
$$;

CREATE FUNCTION g3a1_private._heartbeat_upload(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,p_lease integer,p_internal boolean)
RETURNS boolean LANGUAGE plpgsql SET search_path='' AS $$
DECLARE r g3a1_private.upload_reservations%ROWTYPE; max_lease integer;
BEGIN
 IF p_lease IS NULL OR p_lease NOT BETWEEN 1 AND 3600 THEN
  RAISE EXCEPTION 'invalid lease duration' USING ERRCODE='22023';
 END IF;
 PERFORM g3a1_private._lock_org(p_org,true,p_internal);
 SELECT * INTO r FROM g3a1_private.upload_reservations WHERE id=p_id AND organization_id=p_org FOR UPDATE;
 IF NOT FOUND OR (NOT p_internal AND r.owner_user_id<>auth.uid()) THEN
  RAISE EXCEPTION 'upload not found' USING ERRCODE='P0002';
 END IF;
 IF r.state<>'publishing' OR p_token IS DISTINCT FROM r.lease_token
  OR p_fence IS DISTINCT FROM r.fence OR r.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
 SELECT max_lease_seconds INTO max_lease FROM g3a1_private.upload_limits WHERE organization_id=p_org;
 UPDATE g3a1_private.upload_reservations SET lease_expires_at=clock_timestamp()+make_interval(secs=>least(p_lease,max_lease))
  WHERE id=r.id;
 INSERT INTO g3a1_private.upload_events(organization_id,reservation_id,event_type,fence)
  VALUES(p_org,r.id,'heartbeat',r.fence);
 RETURN true;
END $$;
CREATE FUNCTION g3a1_private.heartbeat_upload(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,p_lease integer)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._heartbeat_upload(p_org,p_id,p_token,p_fence,p_lease,false)
$$;
CREATE FUNCTION g3a1_private.internal_heartbeat_upload(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,p_lease integer)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._heartbeat_upload(p_org,p_id,p_token,p_fence,p_lease,true)
$$;

CREATE FUNCTION g3a1_private._complete_upload(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,
 p_key text,p_size bigint,p_sha text,p_generation text,p_internal boolean)
RETURNS jsonb LANGUAGE plpgsql SET search_path='' AS $$
DECLARE r g3a1_private.upload_reservations%ROWTYPE; receipt g3a1_private.upload_receipts%ROWTYPE;
BEGIN
 PERFORM g3a1_private._lock_org(p_org,true,p_internal);
 SELECT * INTO r FROM g3a1_private.upload_reservations WHERE id=p_id AND organization_id=p_org FOR UPDATE;
 IF NOT FOUND OR (NOT p_internal AND r.owner_user_id<>auth.uid()) THEN
  RAISE EXCEPTION 'upload not found' USING ERRCODE='P0002';
 END IF;
 IF p_key IS DISTINCT FROM r.object_key OR p_size IS DISTINCT FROM r.size_bytes OR p_sha IS DISTINCT FROM r.sha256
  OR (p_generation IS NOT NULL AND length(p_generation) NOT BETWEEN 1 AND 128) THEN
  RAISE EXCEPTION 'object metadata does not match reservation' USING ERRCODE='22023';
 END IF;
 IF r.state='uploaded' THEN
  IF p_fence IS DISTINCT FROM r.fence THEN
   RAISE EXCEPTION 'stale upload lease' USING ERRCODE='40001';
  END IF;
  SELECT * INTO receipt FROM g3a1_private.upload_receipts WHERE id=r.id;
  IF p_generation IS DISTINCT FROM receipt.generation THEN
   RAISE EXCEPTION 'immutable object generation conflict' USING ERRCODE='23505';
  END IF;
  RETURN g3a1_private._receipt_json(r.id);
 END IF;
 IF r.state<>'publishing' OR p_token IS DISTINCT FROM r.lease_token
  OR p_fence IS DISTINCT FROM r.fence OR r.lease_expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'stale upload lease' USING ERRCODE='40001';
 END IF;
 INSERT INTO g3a1_private.upload_receipts(id,organization_id,owner_user_id,object_key,size_bytes,sha256,generation)
  VALUES(r.id,r.organization_id,r.owner_user_id,r.object_key,r.size_bytes,r.sha256,p_generation);
 UPDATE g3a1_private.upload_reservations SET state='uploaded',lease_token=NULL,lease_expires_at=NULL WHERE id=r.id;
 INSERT INTO g3a1_private.upload_events(organization_id,reservation_id,event_type,fence)
  VALUES(p_org,r.id,'uploaded',r.fence);
 -- Bytes/count remain charged: a durable object and receipt still occupy quota.
 RETURN g3a1_private._receipt_json(r.id);
END $$;
CREATE FUNCTION g3a1_private.complete_upload(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,
 p_key text,p_size bigint,p_sha text,p_generation text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._complete_upload(p_org,p_id,p_token,p_fence,p_key,p_size,p_sha,p_generation,false)
$$;
CREATE FUNCTION g3a1_private.internal_complete_upload(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,
 p_key text,p_size bigint,p_sha text,p_generation text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._complete_upload(p_org,p_id,p_token,p_fence,p_key,p_size,p_sha,p_generation,true)
$$;

CREATE FUNCTION g3a1_private._mark_upload_uncertain(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,
 p_error text,p_internal boolean)
RETURNS void LANGUAGE plpgsql SET search_path='' AS $$
DECLARE r g3a1_private.upload_reservations%ROWTYPE;
BEGIN
 IF p_error IS NOT NULL AND p_error !~ '^[A-Z0-9_]{1,80}$' THEN
  RAISE EXCEPTION 'invalid bounded error code' USING ERRCODE='22023';
 END IF;
 PERFORM g3a1_private._lock_org(p_org,true,p_internal);
 SELECT * INTO r FROM g3a1_private.upload_reservations WHERE id=p_id AND organization_id=p_org FOR UPDATE;
 IF NOT FOUND OR (NOT p_internal AND r.owner_user_id<>auth.uid()) THEN
  RAISE EXCEPTION 'upload not found' USING ERRCODE='P0002';
 END IF;
 IF r.state='uploaded' THEN
  IF p_fence IS DISTINCT FROM r.fence THEN
   RAISE EXCEPTION 'stale upload lease' USING ERRCODE='40001';
  END IF;
  RETURN;
 END IF;
 IF r.state<>'publishing' OR p_token IS DISTINCT FROM r.lease_token
  OR p_fence IS DISTINCT FROM r.fence OR r.lease_expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'stale upload lease' USING ERRCODE='40001';
 END IF;
 UPDATE g3a1_private.upload_reservations SET state='uncertain',lease_token=NULL,lease_expires_at=NULL WHERE id=r.id;
 INSERT INTO g3a1_private.upload_events(organization_id,reservation_id,event_type,fence,error_code)
  VALUES(p_org,r.id,'uncertain',r.fence,p_error);
 -- Never refund a claim that may have reached an external writer. Missing bytes
 -- are not proof that an expired writer cannot publish later.
END $$;
CREATE FUNCTION g3a1_private.mark_upload_uncertain(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,p_error text DEFAULT NULL)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._mark_upload_uncertain(p_org,p_id,p_token,p_fence,p_error,false)
$$;
CREATE FUNCTION g3a1_private.internal_mark_upload_uncertain(p_org uuid,p_id uuid,p_token uuid,p_fence bigint,p_error text DEFAULT NULL)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT g3a1_private._mark_upload_uncertain(p_org,p_id,p_token,p_fence,p_error,true)
$$;

CREATE FUNCTION g3a1_private.get_upload(p_org uuid,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r g3a1_private.upload_reservations%ROWTYPE;
BEGIN
 PERFORM g3a_private.lock_api_membership(p_org,false);
 SELECT * INTO r FROM g3a1_private.upload_reservations WHERE id=p_id AND organization_id=p_org;
 IF NOT FOUND OR r.owner_user_id<>auth.uid() THEN
  RAISE EXCEPTION 'upload not found' USING ERRCODE='P0002';
 END IF;
 RETURN g3a1_private._reservation_json(r.id);
END $$;
CREATE FUNCTION g3a1_private.get_upload_receipt(p_org uuid,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE result jsonb;
BEGIN
 result:=g3a1_private.get_upload(p_org,p_id);
 RETURN result->'receipt';
END $$;
CREATE FUNCTION g3a1_private.expire_unused_uploads(p_org uuid,p_limit integer DEFAULT 100) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 PERFORM g3a1_private._lock_org(p_org,true,false);
 RETURN g3a1_private._expire_unused(p_org,p_limit);
END $$;
CREATE FUNCTION g3a1_private.internal_expire_unused_uploads(p_org uuid,p_limit integer DEFAULT 100) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 PERFORM g3a1_private._lock_org(p_org,true,true);
 RETURN g3a1_private._expire_unused(p_org,p_limit);
END $$;
CREATE FUNCTION g3a1_private.upload_metrics(p_org uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE result jsonb;
BEGIN
 PERFORM g3a_private.lock_api_membership(p_org,false);
 SELECT jsonb_build_object('organization_id',q.organization_id,'used_bytes',q.used_bytes,'used_count',q.used_count,
  'max_bytes',q.max_bytes,'max_count',q.max_count,'max_upload_bytes',q.max_upload_bytes,
  'reserved',(SELECT count(*) FROM g3a1_private.upload_reservations r WHERE r.organization_id=p_org AND r.state='reserved'),
  'publishing',(SELECT count(*) FROM g3a1_private.upload_reservations r WHERE r.organization_id=p_org AND r.state='publishing'),
  'uncertain',(SELECT count(*) FROM g3a1_private.upload_reservations r WHERE r.organization_id=p_org AND r.state='uncertain'),
  'uploaded',(SELECT count(*) FROM g3a1_private.upload_reservations r WHERE r.organization_id=p_org AND r.state='uploaded'),
  'expired',(SELECT count(*) FROM g3a1_private.upload_reservations r WHERE r.organization_id=p_org AND r.state='expired'))
 INTO result FROM g3a1_private.upload_limits q WHERE q.organization_id=p_org;
 IF NOT FOUND THEN RAISE EXCEPTION 'upload organization unavailable' USING ERRCODE='42501'; END IF;
 RETURN result;
END $$;

-- No default PUBLIC EXECUTE, including internal helpers or trigger functions.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA g3a1_private FROM PUBLIC,anon,authenticated,engine_worker,g3a1_upload_reconciler;
GRANT EXECUTE ON FUNCTION
 g3a1_private.reserve_upload(uuid,text,bigint,text,integer),
 g3a1_private.claim_upload(uuid,uuid,integer),
 g3a1_private.reconcile_own_upload(uuid,uuid,integer),
 g3a1_private.heartbeat_upload(uuid,uuid,uuid,bigint,integer),
 g3a1_private.complete_upload(uuid,uuid,uuid,bigint,text,bigint,text,text),
 g3a1_private.mark_upload_uncertain(uuid,uuid,uuid,bigint,text),
 g3a1_private.get_upload(uuid,uuid),g3a1_private.get_upload_receipt(uuid,uuid),
 g3a1_private.expire_unused_uploads(uuid,integer),g3a1_private.upload_metrics(uuid)
 TO authenticated;
GRANT EXECUTE ON FUNCTION
 g3a1_private.internal_claim_reconciliation(uuid,uuid,integer),
 g3a1_private.internal_heartbeat_upload(uuid,uuid,uuid,bigint,integer),
 g3a1_private.internal_complete_upload(uuid,uuid,uuid,bigint,text,bigint,text,text),
 g3a1_private.internal_mark_upload_uncertain(uuid,uuid,uuid,bigint,text),
 g3a1_private.internal_expire_unused_uploads(uuid,integer)
 TO g3a1_upload_reconciler;
