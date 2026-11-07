-- Issue #4. Additive G2 contracts; apply after 001/002 on an isolated database.
BEGIN;
CREATE TABLE public.media_bindings (
 asset_id uuid PRIMARY KEY,
 organization_id uuid NOT NULL,
 caption_key text,
 caption_sha256 text CHECK (caption_sha256 ~ '^[a-f0-9]{64}$'),
 caption_bytes bigint CHECK (caption_bytes > 0 AND caption_bytes <= 65536),
 binding_digest text NOT NULL CHECK (binding_digest ~ '^[a-f0-9]{64}$'),
 FOREIGN KEY(asset_id,organization_id) REFERENCES public.assets(id,organization_id),
 UNIQUE(asset_id,organization_id),
 CHECK ((caption_key IS NULL AND caption_sha256 IS NULL AND caption_bytes IS NULL)
     OR (caption_key IS NOT NULL AND caption_sha256 IS NOT NULL AND caption_bytes IS NOT NULL)),
 CHECK (caption_key IS NULL OR caption_key = organization_id::text || '/assets/' || asset_id::text || '/caption.srt')
);
CREATE TABLE public.media_job_specs (
 job_id uuid PRIMARY KEY,
 organization_id uuid NOT NULL,
 asset_id uuid NOT NULL,
 media_digest text NOT NULL CHECK (media_digest ~ '^[a-f0-9]{64}$'),
 FOREIGN KEY(job_id,organization_id) REFERENCES public.render_jobs(id,organization_id),
 FOREIGN KEY(asset_id,organization_id) REFERENCES public.media_bindings(asset_id,organization_id)
);
CREATE TABLE public.media_attempt_metrics (
 job_id uuid NOT NULL,
 organization_id uuid NOT NULL,
 fence bigint NOT NULL CHECK(fence > 0),
 stage text NOT NULL CHECK(length(stage) BETWEEN 1 AND 40),
 elapsed_ms bigint NOT NULL CHECK(elapsed_ms >= 0),
 read_bytes bigint NOT NULL CHECK(read_bytes >= 0),
 written_bytes bigint NOT NULL CHECK(written_bytes >= 0),
 error_code text CHECK(length(error_code) <= 80),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(job_id,fence),
 FOREIGN KEY(job_id,organization_id) REFERENCES public.render_jobs(id,organization_id)
);

CREATE FUNCTION public.guard_media_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
 RAISE EXCEPTION 'media binding/spec is immutable' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER media_binding_immutable BEFORE UPDATE ON public.media_bindings
 FOR EACH ROW EXECUTE FUNCTION public.guard_media_identity();
CREATE TRIGGER media_spec_immutable BEFORE UPDATE ON public.media_job_specs
 FOR EACH ROW EXECUTE FUNCTION public.guard_media_identity();

-- A binding is sealed at trusted ingestion. Changing captions means a new asset
-- id. The G1 digest therefore identifies that immutable asset+caption bundle;
-- media_digest additionally hashes the explicit binding digest and G1 digest.
CREATE FUNCTION public.submit_media_render(
 p_organization_id uuid,p_request_key text,p_source_asset_id uuid,
 p_duration_ms integer,p_quality text
) RETURNS public.render_jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
 binding public.media_bindings%ROWTYPE;
 job public.render_jobs%ROWTYPE;
BEGIN
 IF auth.uid() IS NULL OR NOT EXISTS (
  SELECT FROM public.memberships m WHERE m.organization_id=p_organization_id
   AND m.user_id=auth.uid() AND m.role IN ('owner','admin','editor')
 ) THEN
  RAISE EXCEPTION 'media submission forbidden' USING ERRCODE='42501';
 END IF;
 SELECT * INTO binding FROM public.media_bindings
  WHERE asset_id=p_source_asset_id AND organization_id=p_organization_id FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'verified media binding required' USING ERRCODE='42501';
 END IF;
 job := public.submit_render(p_organization_id,p_request_key,p_source_asset_id,p_duration_ms,p_quality);
 INSERT INTO public.media_job_specs(job_id,organization_id,asset_id,media_digest)
 VALUES(job.id,job.organization_id,p_source_asset_id,
  pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
    'g2-v1:' || job.request_hash::text || ':' || binding.binding_digest,'UTF8')),'hex'))
 ON CONFLICT(job_id) DO NOTHING;
 RETURN job;
END;
$$;

ALTER TABLE public.media_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.media_job_specs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.media_attempt_metrics ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.media_bindings,public.media_job_specs,public.media_attempt_metrics
 FROM PUBLIC,anon,authenticated,engine_worker;
GRANT SELECT ON public.media_bindings,public.media_job_specs TO authenticated,engine_worker;
GRANT SELECT,INSERT,UPDATE ON public.media_attempt_metrics TO engine_worker;
CREATE POLICY media_binding_read ON public.media_bindings FOR SELECT TO authenticated
 USING(public.is_org_member(organization_id));
CREATE POLICY media_spec_read ON public.media_job_specs FOR SELECT TO authenticated
 USING(public.is_org_member(organization_id));
CREATE POLICY worker_media_binding ON public.media_bindings FOR SELECT TO engine_worker USING(true);
CREATE POLICY worker_media_spec ON public.media_job_specs FOR SELECT TO engine_worker USING(true);
CREATE POLICY worker_media_metrics ON public.media_attempt_metrics TO engine_worker USING(true) WITH CHECK(true);
REVOKE ALL ON FUNCTION public.guard_media_identity() FROM PUBLIC,anon,authenticated,engine_worker;
REVOKE ALL ON FUNCTION public.submit_media_render(uuid,text,uuid,integer,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.submit_media_render(uuid,text,uuid,integer,text) TO authenticated;
COMMIT;
