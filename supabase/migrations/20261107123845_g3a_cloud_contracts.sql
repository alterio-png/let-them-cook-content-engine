-- G3A internal launch ledger. No end-user or anonymous access.
ALTER TABLE public.render_outbox ADD CONSTRAINT render_outbox_event_job_org_unique
  UNIQUE (id, job_id, organization_id);
CREATE TABLE public.cloud_job_launches (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  job_id uuid NOT NULL,
  event_id uuid NOT NULL,
  expected_fence bigint NOT NULL CHECK (expected_fence > 0),
  state text NOT NULL CHECK (state IN ('submitting','accepted','uncertain','claimed','closed')),
  operation_name text CHECK (operation_name IS NULL OR length(operation_name) <= 1024),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  touched_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  claimed_at timestamptz,
  last_error text CHECK (last_error IS NULL OR length(last_error) <= 80),
  UNIQUE (job_id, expected_fence),
  FOREIGN KEY (job_id, organization_id) REFERENCES public.render_jobs(id,organization_id),
  FOREIGN KEY (event_id,job_id,organization_id) REFERENCES public.render_outbox(id,job_id,organization_id)
);
CREATE INDEX cloud_job_launches_unresolved_idx ON public.cloud_job_launches(state,touched_at);
ALTER TABLE public.cloud_job_launches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cloud_job_launches FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.cloud_job_launches TO engine_worker;
CREATE POLICY cloud_launch_internal ON public.cloud_job_launches TO engine_worker
 USING (true) WITH CHECK (true);
CREATE FUNCTION public.guard_cloud_launch() RETURNS trigger LANGUAGE plpgsql
 SET search_path = pg_catalog,public AS $$
BEGIN
 IF (NEW.id,NEW.organization_id,NEW.job_id,NEW.event_id,NEW.expected_fence,NEW.created_at)
    IS DISTINCT FROM
    (OLD.id,OLD.organization_id,OLD.job_id,OLD.event_id,OLD.expected_fence,OLD.created_at)
    OR (OLD.operation_name IS NOT NULL AND NEW.operation_name IS DISTINCT FROM OLD.operation_name)
    OR (OLD.state='closed' AND NEW.state <> 'closed')
    OR (OLD.state='claimed' AND NEW.state NOT IN ('claimed','closed'))
    OR (OLD.state IN ('accepted','uncertain') AND NEW.state='submitting') THEN
   RAISE EXCEPTION 'immutable launch identity or invalid state transition' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_cloud_launch() FROM PUBLIC,anon,authenticated;
CREATE TRIGGER cloud_launch_immutable BEFORE UPDATE ON public.cloud_job_launches
 FOR EACH ROW EXECUTE FUNCTION public.guard_cloud_launch();

-- Narrow API authorization lock. No membership mutation privilege is granted.
CREATE SCHEMA IF NOT EXISTS g3a_private;
REVOKE ALL ON SCHEMA g3a_private FROM PUBLIC,anon,authenticated,engine_worker;
GRANT USAGE ON SCHEMA g3a_private TO authenticated;
CREATE FUNCTION g3a_private.lock_api_membership(p_org uuid,p_requires_write boolean)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE
 member_role text;
BEGIN
 IF auth.uid() IS NULL OR p_requires_write IS NULL THEN
  RAISE EXCEPTION 'verified identity required' USING ERRCODE='42501';
 END IF;
 SELECT m.role INTO member_role FROM public.memberships m
  WHERE m.organization_id=p_org AND m.user_id=auth.uid() FOR SHARE;
 IF NOT FOUND OR (p_requires_write AND member_role NOT IN ('owner','admin','editor')) THEN
  RAISE EXCEPTION 'organization access denied' USING ERRCODE='42501';
 END IF;
 RETURN member_role;
END;
$$;
REVOKE ALL ON FUNCTION g3a_private.lock_api_membership(uuid,boolean) FROM PUBLIC,anon,engine_worker;
GRANT EXECUTE ON FUNCTION g3a_private.lock_api_membership(uuid,boolean) TO authenticated;
