-- G3B-R0 local-only trusted ingestion. Existing G0/G1/G2/G3A/G3A.1 files
-- remain unchanged. API principals receive EXECUTE only; receipts never become
-- validated assets on a browser assertion or from caller-supplied media facts.
CREATE SCHEMA g3br0_private;
REVOKE ALL ON SCHEMA g3br0_private FROM PUBLIC,anon,authenticated,engine_worker;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='g3br0_ingester') THEN
  CREATE ROLE g3br0_ingester NOLOGIN NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
ALTER ROLE g3br0_ingester NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA g3br0_private TO authenticated,g3br0_ingester,engine_worker;

-- Seeded solely by the isolated administrator from locally generated fixtures.
-- Exact video+caption hashes and sizes bind approval to the actual byte pair.
CREATE TABLE g3br0_private.synthetic_allowlist (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
 fixture_id text NOT NULL CHECK (fixture_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
 video_sha256 text NOT NULL CHECK (video_sha256 ~ '^[0-9a-f]{64}$'),
 video_bytes bigint NOT NULL CHECK (video_bytes BETWEEN 1 AND 1048576),
 caption_sha256 text NOT NULL CHECK (caption_sha256 ~ '^[0-9a-f]{64}$'),
 caption_bytes bigint NOT NULL CHECK (caption_bytes BETWEEN 1 AND 65536),
 provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance)='object' AND octet_length(provenance::text)<=4096),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,video_sha256,video_bytes,caption_sha256,caption_bytes)
);
CREATE TABLE g3br0_private.ingest_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
 owner_user_id uuid NOT NULL,
 request_key text NOT NULL CHECK (request_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
 video_receipt_id uuid NOT NULL REFERENCES g3a1_private.upload_receipts(id) ON DELETE RESTRICT,
 caption_receipt_id uuid NOT NULL REFERENCES g3a1_private.upload_receipts(id) ON DELETE RESTRICT,
 asset_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','validating','completed','failed','uncertain')),
 fence bigint NOT NULL DEFAULT 0 CHECK (fence>=0),
 attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
 lease_token uuid,
 lease_expires_at timestamptz,
 deadline_at timestamptz,
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 last_error text CHECK (last_error ~ '^[a-z_]{1,64}$'),
 completed_metadata jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,request_key),
 UNIQUE(organization_id,video_receipt_id,caption_receipt_id),
 FOREIGN KEY(video_receipt_id,organization_id,owner_user_id)
  REFERENCES g3a1_private.upload_reservations(id,organization_id,owner_user_id) ON DELETE RESTRICT,
 FOREIGN KEY(caption_receipt_id,organization_id,owner_user_id)
  REFERENCES g3a1_private.upload_reservations(id,organization_id,owner_user_id) ON DELETE RESTRICT,
 CHECK(video_receipt_id<>caption_receipt_id),
 CHECK((state='validating' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND deadline_at IS NOT NULL)
    OR (state<>'validating' AND lease_token IS NULL AND lease_expires_at IS NULL AND deadline_at IS NULL)),
 CHECK((state='completed')=(completed_metadata IS NOT NULL))
);
CREATE INDEX ingest_requests_claim_idx ON g3br0_private.ingest_requests(state,available_at,created_at,id)
 WHERE state IN ('queued','validating','uncertain');
CREATE TABLE g3br0_private.ingest_provenance (
 asset_id uuid PRIMARY KEY REFERENCES public.assets(id) ON DELETE RESTRICT,
 ingest_id uuid NOT NULL UNIQUE REFERENCES g3br0_private.ingest_requests(id) ON DELETE RESTRICT,
 organization_id uuid NOT NULL,
 owner_user_id uuid NOT NULL,
 approval_id uuid NOT NULL REFERENCES g3br0_private.synthetic_allowlist(id) ON DELETE RESTRICT,
 video_receipt_id uuid NOT NULL REFERENCES g3a1_private.upload_receipts(id) ON DELETE RESTRICT,
 caption_receipt_id uuid NOT NULL REFERENCES g3a1_private.upload_receipts(id) ON DELETE RESTRICT,
 verified_metadata jsonb NOT NULL,
 verified_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(asset_id,organization_id) REFERENCES public.assets(id,organization_id) ON DELETE RESTRICT
);

CREATE FUNCTION g3br0_private.immutable_provenance() RETURNS trigger LANGUAGE plpgsql
 SET search_path='' AS $$ BEGIN RAISE EXCEPTION 'immutable ingestion provenance' USING ERRCODE='23514'; END $$;
CREATE TRIGGER synthetic_allowlist_immutable BEFORE UPDATE ON g3br0_private.synthetic_allowlist
 FOR EACH ROW EXECUTE FUNCTION g3br0_private.immutable_provenance();
CREATE TRIGGER ingest_provenance_immutable BEFORE UPDATE ON g3br0_private.ingest_provenance
 FOR EACH ROW EXECUTE FUNCTION g3br0_private.immutable_provenance();
CREATE FUNCTION g3br0_private.guard_ingest_identity() RETURNS trigger LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
 IF (NEW.id,NEW.organization_id,NEW.owner_user_id,NEW.request_key,NEW.video_receipt_id,
     NEW.caption_receipt_id,NEW.asset_id,NEW.created_at) IS DISTINCT FROM
    (OLD.id,OLD.organization_id,OLD.owner_user_id,OLD.request_key,OLD.video_receipt_id,
     OLD.caption_receipt_id,OLD.asset_id,OLD.created_at) OR OLD.state='completed' THEN
  RAISE EXCEPTION 'immutable ingestion identity or result' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ingest_identity_immutable BEFORE UPDATE ON g3br0_private.ingest_requests
 FOR EACH ROW EXECUTE FUNCTION g3br0_private.guard_ingest_identity();

ALTER TABLE g3br0_private.synthetic_allowlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE g3br0_private.ingest_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE g3br0_private.ingest_provenance ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA g3br0_private FROM PUBLIC,anon,authenticated,engine_worker,g3br0_ingester;

CREATE FUNCTION g3br0_private.ingest_view(p_row g3br0_private.ingest_requests) RETURNS jsonb
 LANGUAGE sql STABLE SET search_path='' AS $$
 SELECT jsonb_build_object('id',p_row.id,'organization_id',p_row.organization_id,
  'request_key',p_row.request_key,'state',p_row.state,'attempt_count',p_row.attempt_count,
  'asset_id',CASE WHEN p_row.state='completed' THEN p_row.asset_id ELSE NULL END,
  'error_code',p_row.last_error,'metadata',p_row.completed_metadata,
  'created_at',p_row.created_at,'updated_at',p_row.updated_at)
$$;

CREATE FUNCTION g3br0_private.request_ingest(p_org uuid,p_key text,p_video uuid,p_caption uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r g3br0_private.ingest_requests; actor uuid := auth.uid(); n integer;
BEGIN
 PERFORM g3a_private.lock_api_membership(p_org,true);
 IF p_key IS NULL OR p_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR p_video=p_caption THEN
  RAISE EXCEPTION 'invalid ingestion request' USING ERRCODE='22023';
 END IF;
 SELECT count(*) INTO n FROM g3a1_private.upload_receipts u
  WHERE u.id IN (p_video,p_caption) AND u.organization_id=p_org AND u.owner_user_id=actor;
 IF n<>2 THEN RAISE EXCEPTION 'owned upload receipts required' USING ERRCODE='42501'; END IF;
 INSERT INTO g3br0_private.ingest_requests(organization_id,owner_user_id,request_key,video_receipt_id,caption_receipt_id)
  VALUES(p_org,actor,p_key,p_video,p_caption) ON CONFLICT(organization_id,request_key) DO NOTHING;
 SELECT * INTO r FROM g3br0_private.ingest_requests WHERE organization_id=p_org AND request_key=p_key FOR UPDATE;
 IF r.owner_user_id<>actor OR r.video_receipt_id<>p_video OR r.caption_receipt_id<>p_caption THEN
  RAISE EXCEPTION 'ingestion idempotency conflict' USING ERRCODE='23505';
 END IF;
 RETURN g3br0_private.ingest_view(r);
END $$;
CREATE FUNCTION g3br0_private.get_ingest(p_org uuid,p_id uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r g3br0_private.ingest_requests;
BEGIN
 PERFORM g3a_private.lock_api_membership(p_org,false);
 SELECT * INTO r FROM g3br0_private.ingest_requests
  WHERE id=p_id AND organization_id=p_org AND owner_user_id=auth.uid();
 IF NOT FOUND THEN RAISE EXCEPTION 'ingestion not found' USING ERRCODE='42501'; END IF;
 RETURN g3br0_private.ingest_view(r);
END $$;

-- Membership SHARE precedes request UPDATE, consistently with upload authority.
-- Fixed 30s operation deadline cannot be extended by a heartbeat.
CREATE FUNCTION g3br0_private.claim_ingest(p_lease integer DEFAULT 30) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE c record; r g3br0_private.ingest_requests; m text; approval jsonb;
BEGIN
 IF p_lease IS NULL OR p_lease NOT BETWEEN 1 AND 30 THEN RAISE EXCEPTION 'lease outside bounds' USING ERRCODE='22023'; END IF;
 FOR c IN SELECT id,organization_id,owner_user_id FROM g3br0_private.ingest_requests
  WHERE state IN ('queued','uncertain','validating') AND available_at<=clock_timestamp()
   AND (state<>'validating' OR lease_expires_at<=clock_timestamp() OR deadline_at<=clock_timestamp())
  ORDER BY available_at,created_at,id LIMIT 32
 LOOP
  SELECT role INTO m FROM public.memberships WHERE organization_id=c.organization_id
    AND user_id=c.owner_user_id AND role IN ('owner','admin','editor') FOR SHARE;
  SELECT * INTO r FROM g3br0_private.ingest_requests WHERE id=c.id FOR UPDATE SKIP LOCKED;
  IF NOT FOUND OR r.state NOT IN ('queued','uncertain','validating') OR r.available_at>clock_timestamp()
    OR (r.state='validating' AND r.lease_expires_at>clock_timestamp() AND r.deadline_at>clock_timestamp()) THEN CONTINUE; END IF;
  IF m IS NULL OR r.attempt_count>=3 THEN
   UPDATE g3br0_private.ingest_requests SET state='failed',lease_token=NULL,lease_expires_at=NULL,deadline_at=NULL,
    last_error=CASE WHEN m IS NULL THEN 'membership_revoked' ELSE 'attempts_exhausted' END,updated_at=clock_timestamp()
    WHERE id=r.id;
   CONTINUE;
  END IF;
  UPDATE g3br0_private.ingest_requests SET state='validating',fence=fence+1,attempt_count=attempt_count+1,
   lease_token=gen_random_uuid(),deadline_at=clock_timestamp()+interval '30 seconds',
   lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease),updated_at=clock_timestamp(),last_error=NULL
   WHERE id=r.id RETURNING * INTO r;
  SELECT to_jsonb(a) INTO approval FROM g3br0_private.synthetic_allowlist a
   JOIN g3a1_private.upload_receipts v ON v.id=r.video_receipt_id
   JOIN g3a1_private.upload_receipts s ON s.id=r.caption_receipt_id
   WHERE a.organization_id=r.organization_id AND a.video_sha256=v.sha256 AND a.video_bytes=v.size_bytes
    AND a.caption_sha256=s.sha256 AND a.caption_bytes=s.size_bytes;
  RETURN to_jsonb(r)||jsonb_build_object('video_receipt',(SELECT to_jsonb(v) FROM g3a1_private.upload_receipts v WHERE id=r.video_receipt_id),
   'caption_receipt',(SELECT to_jsonb(s) FROM g3a1_private.upload_receipts s WHERE id=r.caption_receipt_id),'approval',approval);
 END LOOP;
 RETURN NULL;
END $$;

CREATE FUNCTION g3br0_private.heartbeat_ingest(p_id uuid,p_token uuid,p_fence bigint,p_lease integer) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE n integer;
BEGIN
 IF p_lease IS NULL OR p_lease NOT BETWEEN 1 AND 30 THEN RAISE EXCEPTION 'lease outside bounds' USING ERRCODE='22023'; END IF;
 UPDATE g3br0_private.ingest_requests SET lease_expires_at=least(deadline_at,clock_timestamp()+make_interval(secs=>p_lease)),
  updated_at=clock_timestamp() WHERE id=p_id AND state='validating' AND lease_token=p_token AND fence=p_fence
  AND lease_expires_at>clock_timestamp() AND deadline_at>clock_timestamp();
 GET DIAGNOSTICS n=ROW_COUNT;
 RETURN n=1;
END $$;

CREATE FUNCTION g3br0_private.complete_ingest(p_id uuid,p_token uuid,p_fence bigint,p_metadata jsonb) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r g3br0_private.ingest_requests; v g3a1_private.upload_receipts; s g3a1_private.upload_receipts;
 a g3br0_private.synthetic_allowlist; m text; source_key text; caption_key text; binding text; digest_value text;
 duration_value integer; width_value integer; height_value integer;
BEGIN
 SELECT * INTO r FROM g3br0_private.ingest_requests WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'unknown ingestion' USING ERRCODE='42501'; END IF;
 SELECT role INTO m FROM public.memberships WHERE organization_id=r.organization_id AND user_id=r.owner_user_id
  AND role IN ('owner','admin','editor') FOR SHARE;
 SELECT * INTO r FROM g3br0_private.ingest_requests WHERE id=p_id FOR UPDATE;
 IF r.state='completed' THEN
  IF r.lease_token IS NULL AND r.fence=p_fence AND r.completed_metadata=p_metadata THEN RETURN g3br0_private.ingest_view(r); END IF;
  RAISE EXCEPTION 'stale ingestion result' USING ERRCODE='55000';
 END IF;
 IF m IS NULL OR r.state<>'validating' OR r.lease_token IS DISTINCT FROM p_token OR r.fence IS DISTINCT FROM p_fence
  OR r.lease_expires_at<=clock_timestamp() OR r.deadline_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'revoked or stale ingestion lease' USING ERRCODE='55000';
 END IF;
 SELECT * INTO STRICT v FROM g3a1_private.upload_receipts WHERE id=r.video_receipt_id
  AND organization_id=r.organization_id AND owner_user_id=r.owner_user_id;
 SELECT * INTO STRICT s FROM g3a1_private.upload_receipts WHERE id=r.caption_receipt_id
  AND organization_id=r.organization_id AND owner_user_id=r.owner_user_id;
 SELECT * INTO a FROM g3br0_private.synthetic_allowlist WHERE organization_id=r.organization_id
  AND video_sha256=v.sha256 AND video_bytes=v.size_bytes AND caption_sha256=s.sha256 AND caption_bytes=s.size_bytes;
 IF NOT FOUND THEN RAISE EXCEPTION 'synthetic pair not approved' USING ERRCODE='42501'; END IF;
 source_key := r.organization_id::text||'/assets/'||r.asset_id::text||'/source.mp4';
 caption_key := r.organization_id::text||'/assets/'||r.asset_id::text||'/caption.srt';
 duration_value := (p_metadata->>'duration_ms')::integer;
 width_value := (p_metadata->>'width')::integer; height_value := (p_metadata->>'height')::integer;
 IF jsonb_typeof(p_metadata) IS DISTINCT FROM 'object' OR p_metadata->>'source_key' IS DISTINCT FROM source_key
  OR p_metadata->>'caption_key' IS DISTINCT FROM caption_key
  OR p_metadata->>'source_sha256' IS DISTINCT FROM v.sha256 OR (p_metadata->>'source_bytes')::bigint IS DISTINCT FROM v.size_bytes
  OR p_metadata->>'caption_sha256' IS DISTINCT FROM s.sha256 OR (p_metadata->>'caption_bytes')::bigint IS DISTINCT FROM s.size_bytes
  OR p_metadata->>'video_generation' IS DISTINCT FROM v.generation
  OR p_metadata->>'caption_generation' IS DISTINCT FROM s.generation
  OR p_metadata->>'approval_id' IS DISTINCT FROM a.id::text
  OR duration_value IS NULL OR duration_value NOT BETWEEN 1 AND 900000
  OR width_value IS NULL OR height_value IS NULL OR width_value<=0 OR height_value<=0
  OR width_value::bigint*height_value::bigint>8294400 OR p_metadata->'has_audio' IS DISTINCT FROM 'true'::jsonb THEN
  RAISE EXCEPTION 'invalid verified ingestion metadata' USING ERRCODE='23514';
 END IF;
 -- Same canonical G2 binding digest, computed independently by PostgreSQL.
 binding := '{"caption":{"key":"'||caption_key||'","sha256":"'||s.sha256||'","size_bytes":'||s.size_bytes::text||
  '},"source_bytes":'||v.size_bytes::text||',"source_sha256":"'||v.sha256||'","version":"g2-v1"}';
 digest_value := encode(public.digest(binding,'sha256'),'hex');
 IF p_metadata->>'binding_digest' IS DISTINCT FROM digest_value THEN
  RAISE EXCEPTION 'binding digest mismatch' USING ERRCODE='23514';
 END IF;
 INSERT INTO public.assets(id,organization_id,uploader_id,storage_key,sha256,bytes,duration_ms,width,height,rights_confirmed)
  VALUES(r.asset_id,r.organization_id,r.owner_user_id,source_key,v.sha256,v.size_bytes,duration_value,width_value,height_value,true);
 INSERT INTO public.media_bindings(asset_id,organization_id,caption_key,caption_sha256,caption_bytes,binding_digest)
  VALUES(r.asset_id,r.organization_id,caption_key,s.sha256,s.size_bytes,digest_value);
 INSERT INTO g3br0_private.ingest_provenance(asset_id,ingest_id,organization_id,owner_user_id,approval_id,
  video_receipt_id,caption_receipt_id,verified_metadata)
  VALUES(r.asset_id,r.id,r.organization_id,r.owner_user_id,a.id,v.id,s.id,p_metadata);
 -- A fence/lease cannot be used after time spent acquiring locks or writing.
 UPDATE g3br0_private.ingest_requests SET state='completed',completed_metadata=p_metadata,
  lease_token=NULL,lease_expires_at=NULL,deadline_at=NULL,last_error=NULL,updated_at=clock_timestamp()
  WHERE id=r.id AND lease_token=p_token AND fence=p_fence AND lease_expires_at>clock_timestamp()
   AND deadline_at>clock_timestamp() RETURNING * INTO r;
 IF NOT FOUND THEN RAISE EXCEPTION 'lease expired before ingestion commit' USING ERRCODE='55000'; END IF;
 RETURN g3br0_private.ingest_view(r);
END $$;

CREATE FUNCTION g3br0_private.fail_ingest(p_id uuid,p_token uuid,p_fence bigint,p_error text,p_retryable boolean) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r g3br0_private.ingest_requests;
BEGIN
 IF p_error IS NULL OR p_error !~ '^[a-z_]{1,64}$' OR p_retryable IS NULL THEN
  RAISE EXCEPTION 'invalid failure code' USING ERRCODE='22023'; END IF;
 UPDATE g3br0_private.ingest_requests SET state=CASE WHEN p_retryable AND attempt_count<3 THEN 'uncertain' ELSE 'failed' END,
  lease_token=NULL,lease_expires_at=NULL,deadline_at=NULL,last_error=p_error,
  available_at=clock_timestamp()+make_interval(secs=>least(8,attempt_count*2)),updated_at=clock_timestamp()
  WHERE id=p_id AND state='validating' AND lease_token=p_token AND fence=p_fence
   AND lease_expires_at>clock_timestamp() AND deadline_at>clock_timestamp() RETURNING * INTO r;
 IF NOT FOUND THEN RAISE EXCEPTION 'stale ingestion failure' USING ERRCODE='55000'; END IF;
 RETURN g3br0_private.ingest_view(r);
END $$;

-- Shared PostgreSQL wake transport for the existing G1 durable outbox. This is
-- local infrastructure, not a claim about Cloud Tasks delivery guarantees.
CREATE TABLE g3br0_private.local_wakeups (
 event_id uuid PRIMARY KEY REFERENCES public.render_outbox(id) ON DELETE RESTRICT,
 job_id uuid NOT NULL REFERENCES public.render_jobs(id) ON DELETE RESTRICT,
 acknowledged boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION g3br0_private.guard_wakeup() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 IF TG_OP='UPDATE' AND (NEW.event_id,NEW.job_id,NEW.created_at) IS DISTINCT FROM (OLD.event_id,OLD.job_id,OLD.created_at) THEN
  RAISE EXCEPTION 'immutable wakeup identity' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.render_outbox WHERE id=NEW.event_id AND job_id=NEW.job_id) THEN
  RAISE EXCEPTION 'wakeup payload conflicts with outbox' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER wakeup_identity BEFORE INSERT OR UPDATE ON g3br0_private.local_wakeups
 FOR EACH ROW EXECUTE FUNCTION g3br0_private.guard_wakeup();
ALTER TABLE g3br0_private.local_wakeups ENABLE ROW LEVEL SECURITY;
CREATE POLICY worker_wakeups ON g3br0_private.local_wakeups TO engine_worker USING(true) WITH CHECK(true);
GRANT SELECT,INSERT,UPDATE ON g3br0_private.local_wakeups TO engine_worker;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA g3br0_private FROM PUBLIC,anon,authenticated,engine_worker,g3br0_ingester;
GRANT EXECUTE ON FUNCTION g3br0_private.request_ingest(uuid,text,uuid,uuid),g3br0_private.get_ingest(uuid,uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION g3br0_private.claim_ingest(integer),g3br0_private.heartbeat_ingest(uuid,uuid,bigint,integer),
 g3br0_private.complete_ingest(uuid,uuid,bigint,jsonb),g3br0_private.fail_ingest(uuid,uuid,bigint,text,boolean) TO g3br0_ingester;
