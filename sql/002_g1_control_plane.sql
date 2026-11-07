-- G1, Issue #2: additive upgrade; keep the G0 draft byte-for-byte.
-- Apply as the database owner AFTER 001_schema.sql, in one transaction.
-- auth.users/auth.uid() and authenticated/anon are supplied by the identity layer.
BEGIN;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $$
BEGIN
 IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'engine_worker') THEN
  CREATE ROLE engine_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 ELSIF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'engine_worker'
              AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin)) THEN
  RAISE EXCEPTION 'existing engine_worker role is not restricted';
 END IF;
END;
$$;

-- Metadata is authoritative ingestion output, never supplied by render callers.
-- Legacy rows remain unknown and cannot be submitted until a trusted importer verifies them.
ALTER TABLE public.assets
 ADD COLUMN duration_ms integer CHECK (duration_ms > 0),
 ADD COLUMN width integer CHECK (width > 0),
 ADD COLUMN height integer CHECK (height > 0),
 ADD COLUMN rights_confirmed boolean NOT NULL DEFAULT false;

CREATE TABLE public.org_quotas (
 organization_id uuid PRIMARY KEY REFERENCES public.organizations(id) ON DELETE RESTRICT,
 budget_cents bigint NOT NULL DEFAULT 100000 CHECK (budget_cents >= 0),
 reserved_cents bigint NOT NULL DEFAULT 0 CHECK (reserved_cents >= 0),
 max_jobs integer NOT NULL DEFAULT 1000 CHECK (max_jobs > 0),
 max_active_jobs integer NOT NULL DEFAULT 4 CHECK (max_active_jobs > 0),
 max_duration_ms integer NOT NULL DEFAULT 900000 CHECK (max_duration_ms > 0),
 max_pixels bigint NOT NULL DEFAULT 8294400 CHECK (max_pixels > 0),
 max_bytes bigint NOT NULL DEFAULT 524288000 CHECK (max_bytes > 0),
 CHECK (reserved_cents <= budget_cents)
);

ALTER TABLE public.render_jobs
 ADD COLUMN request_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 ADD COLUMN fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
 ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
 ADD COLUMN max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
 ADD COLUMN available_at timestamptz NOT NULL DEFAULT now(),
 ADD COLUMN last_error text,
 ADD CONSTRAINT render_jobs_id_org_unique UNIQUE (id, organization_id);

ALTER TABLE public.render_attempts ADD COLUMN organization_id uuid;
UPDATE public.render_attempts a SET organization_id = j.organization_id
 FROM public.render_jobs j WHERE j.id = a.job_id;
ALTER TABLE public.render_attempts
 ALTER COLUMN organization_id SET NOT NULL,
 ADD COLUMN fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
 ADD COLUMN lease_expires_at timestamptz,
 ADD CONSTRAINT attempt_job_tenant_fk FOREIGN KEY (job_id, organization_id)
  REFERENCES public.render_jobs(id, organization_id) ON DELETE CASCADE;

ALTER TABLE public.render_outbox ADD COLUMN organization_id uuid;
UPDATE public.render_outbox o SET organization_id = j.organization_id
 FROM public.render_jobs j WHERE j.id = o.job_id;
ALTER TABLE public.render_outbox
 ALTER COLUMN organization_id SET NOT NULL,
 ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
 ADD COLUMN fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
 ADD COLUMN available_at timestamptz NOT NULL DEFAULT now(),
 ADD COLUMN lease_token uuid,
 ADD COLUMN lease_expires_at timestamptz,
 ADD COLUMN last_error text,
 ADD COLUMN state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','dispatched','reconcile')),
 ADD CONSTRAINT outbox_job_tenant_fk FOREIGN KEY (job_id, organization_id)
  REFERENCES public.render_jobs(id, organization_id) ON DELETE CASCADE;

UPDATE public.render_outbox SET state = 'dispatched' WHERE dispatched_at IS NOT NULL;

CREATE TABLE public.audit_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
 job_id uuid NOT NULL,
 actor_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
 event_type text NOT NULL CHECK (char_length(event_type) BETWEEN 1 AND 80),
 details jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (job_id, organization_id) REFERENCES public.render_jobs(id, organization_id)
);
CREATE INDEX audit_events_org_time ON public.audit_events(organization_id, created_at);
CREATE INDEX render_outbox_pending ON public.render_outbox(available_at, created_at)
 WHERE dispatched_at IS NULL;
CREATE INDEX render_jobs_ready ON public.render_jobs(available_at, created_at)
 WHERE state IN ('queued', 'reconcile');

CREATE FUNCTION public.guard_render_request() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
 IF (NEW.id, NEW.organization_id, NEW.request_key, NEW.request_hash,
     NEW.source_asset_id, NEW.request_payload, NEW.cost_cents, NEW.created_at)
    IS DISTINCT FROM
    (OLD.id, OLD.organization_id, OLD.request_key, OLD.request_hash,
     OLD.source_asset_id, OLD.request_payload, OLD.cost_cents, OLD.created_at) THEN
  RAISE EXCEPTION 'render request is immutable' USING ERRCODE = '23514';
 END IF;
 IF OLD.state = 'completed' AND
    (NEW.state, NEW.output_manifest, NEW.fence, NEW.lease_token, NEW.lease_expires_at, NEW.attempt_count)
    IS DISTINCT FROM
    (OLD.state, OLD.output_manifest, OLD.fence, OLD.lease_token, OLD.lease_expires_at, OLD.attempt_count) THEN
  RAISE EXCEPTION 'completed render is immutable' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER render_request_immutable BEFORE UPDATE ON public.render_jobs
 FOR EACH ROW EXECUTE FUNCTION public.guard_render_request();

CREATE FUNCTION public.guard_asset_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
 -- Unknown legacy metadata may be verified once by the trusted ingestion role.
 IF (NEW.id, NEW.organization_id, NEW.storage_key, NEW.sha256, NEW.bytes, NEW.created_at)
     IS DISTINCT FROM
    (OLD.id, OLD.organization_id, OLD.storage_key, OLD.sha256, OLD.bytes, OLD.created_at)
 OR (OLD.duration_ms IS NOT NULL AND NEW.duration_ms IS DISTINCT FROM OLD.duration_ms)
 OR (OLD.width IS NOT NULL AND NEW.width IS DISTINCT FROM OLD.width)
 OR (OLD.height IS NOT NULL AND NEW.height IS DISTINCT FROM OLD.height)
 OR (OLD.rights_confirmed AND NEW.rights_confirmed IS DISTINCT FROM true) THEN
  RAISE EXCEPTION 'asset identity is immutable' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER asset_identity_immutable BEFORE UPDATE ON public.assets
 FOR EACH ROW EXECUTE FUNCTION public.guard_asset_identity();

-- The only end-user mutation entry point. Verified identity must be installed by
-- the trusted authenticator; arbitrary SQL/JWT-setting access is never an API.
CREATE FUNCTION public.submit_render(
 p_organization_id uuid, p_request_key text, p_source_asset_id uuid,
 p_duration_ms integer, p_quality text
) RETURNS public.render_jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
 actor uuid := auth.uid();
 member_role text;
 quota public.org_quotas%ROWTYPE;
 asset public.assets%ROWTYPE;
 job public.render_jobs%ROWTYPE;
 payload jsonb;
 request_digest text;
 estimate bigint;
BEGIN
 IF actor IS NULL THEN
  RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501';
 END IF;
 -- Hold the authorization decision until commit, including role revocation races.
 SELECT m.role INTO member_role FROM public.memberships m
  WHERE m.organization_id = p_organization_id AND m.user_id = actor FOR SHARE;
 IF member_role IS NULL OR member_role NOT IN ('owner', 'admin', 'editor') THEN
  RAISE EXCEPTION 'render submission forbidden' USING ERRCODE = '42501';
 END IF;
 IF p_request_key IS NULL OR p_request_key !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    OR p_duration_ms IS NULL OR p_duration_ms <= 0
    OR p_quality IS NULL OR p_quality NOT IN ('smoke', 'production') THEN
  RAISE EXCEPTION 'invalid render request' USING ERRCODE = '22023';
 END IF;
 -- Serializes deduplication AND reservations for different keys in one org.
 SELECT * INTO quota FROM public.org_quotas q
  WHERE q.organization_id = p_organization_id FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'organization quota is not provisioned' USING ERRCODE = '23514';
 END IF;
 SELECT * INTO asset FROM public.assets a
  WHERE a.id = p_source_asset_id AND a.organization_id = p_organization_id FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'source asset not available' USING ERRCODE = '42501';
 END IF;
 payload := pg_catalog.jsonb_build_object(
  'renderer_contract', 'g1-v1', 'source_asset_id', asset.id,
  'source_sha256', asset.sha256, 'source_bytes', asset.bytes,
  'source_duration_ms', asset.duration_ms, 'source_width', asset.width,
  'source_height', asset.height, 'rights_confirmed', asset.rights_confirmed,
  'duration_ms', p_duration_ms, 'quality', p_quality
 );
 request_digest := pg_catalog.encode(
  pg_catalog.sha256(pg_catalog.convert_to(payload::text, 'UTF8')), 'hex');
 SELECT * INTO job FROM public.render_jobs j
  WHERE j.organization_id = p_organization_id AND j.request_key = p_request_key;
 IF FOUND THEN
  IF job.request_hash <> request_digest THEN
   RAISE EXCEPTION 'request key conflicts with immutable payload' USING ERRCODE = '23505';
  END IF;
  -- Return authoritative state even when capacity/budget has since changed.
  RETURN job;
 END IF;
 IF asset.duration_ms IS NULL OR asset.width IS NULL OR asset.height IS NULL
    OR NOT asset.rights_confirmed OR p_duration_ms > asset.duration_ms
    OR asset.duration_ms > quota.max_duration_ms
    OR p_duration_ms > quota.max_duration_ms
    OR asset.width::bigint * asset.height::bigint > quota.max_pixels
    OR asset.bytes > quota.max_bytes THEN
  RAISE EXCEPTION 'asset rights or media limits rejected' USING ERRCODE = '23514';
 END IF;
 -- Explicitly FAKE cost model for contract tests, not pricing/billing evidence.
 estimate := ((p_duration_ms::bigint + 999) / 1000) * 3;
 IF quota.reserved_cents + estimate > quota.budget_cents
    OR (SELECT pg_catalog.count(*) FROM public.render_jobs j
        WHERE j.organization_id = p_organization_id) >= quota.max_jobs THEN
  RAISE EXCEPTION 'organization budget or job quota exhausted' USING ERRCODE = '23514';
 END IF;
 INSERT INTO public.render_jobs(
  organization_id, request_key, request_hash, source_asset_id, request_payload, cost_cents
 ) VALUES (p_organization_id, p_request_key, request_digest, p_source_asset_id, payload, estimate)
 RETURNING * INTO job;
 INSERT INTO public.render_outbox(job_id, organization_id, event_type)
  VALUES (job.id, p_organization_id, 'render_requested');
 INSERT INTO public.audit_events(organization_id, job_id, actor_id, event_type, details)
  VALUES (p_organization_id, job.id, actor, 'render_submitted',
   pg_catalog.jsonb_build_object('request_hash', request_digest, 'reserved_cents', estimate));
 UPDATE public.org_quotas SET reserved_cents = reserved_cents + estimate
  WHERE organization_id = p_organization_id;
 RETURN job;
END;
$$;

ALTER TABLE public.org_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY quota_read ON public.org_quotas FOR SELECT TO authenticated
 USING (public.is_org_member(organization_id));
CREATE POLICY audit_read ON public.audit_events FOR SELECT TO authenticated
 USING (public.is_org_member(organization_id));

-- Roles own no tables and cannot bypass RLS. The worker is a trusted internal
-- principal; never grant membership in it to an end-user/authenticator role.
GRANT USAGE ON SCHEMA public TO authenticated, engine_worker;
REVOKE ALL ON public.organizations, public.memberships, public.assets, public.render_jobs,
 public.render_attempts, public.render_outbox, public.org_quotas, public.audit_events
 FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.organizations, public.memberships, public.assets, public.render_jobs,
 public.org_quotas, public.audit_events TO authenticated;
GRANT SELECT ON public.assets, public.render_jobs, public.render_attempts,
 public.render_outbox, public.org_quotas, public.audit_events TO engine_worker;
GRANT UPDATE ON public.render_jobs, public.render_outbox, public.render_attempts TO engine_worker;
GRANT INSERT ON public.render_attempts, public.audit_events TO engine_worker;
CREATE POLICY worker_assets ON public.assets FOR SELECT TO engine_worker USING (true);
CREATE POLICY worker_quotas ON public.org_quotas FOR SELECT TO engine_worker USING (true);
CREATE POLICY worker_jobs ON public.render_jobs TO engine_worker USING (true) WITH CHECK (true);
CREATE POLICY worker_attempts ON public.render_attempts TO engine_worker USING (true) WITH CHECK (true);
CREATE POLICY worker_outbox ON public.render_outbox TO engine_worker USING (true) WITH CHECK (true);
CREATE POLICY worker_audit ON public.audit_events TO engine_worker USING (true) WITH CHECK (true);

REVOKE ALL ON FUNCTION public.is_org_member(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.is_org_member(uuid) TO authenticated;
REVOKE ALL ON FUNCTION public.submit_render(uuid, text, uuid, integer, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.submit_render(uuid, text, uuid, integer, text) TO authenticated;
REVOKE ALL ON FUNCTION public.guard_render_request(), public.guard_asset_identity()
 FROM PUBLIC, anon, authenticated, engine_worker;
COMMIT;
