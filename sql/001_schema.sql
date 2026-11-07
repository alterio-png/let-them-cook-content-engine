-- DRAFT, NOT APPLIED OR VALIDATED ON A LIVE SUPABASE INSTANCE.
-- PostgreSQL schema with organization-level RLS. Must be reviewed and tested on isolated Supabase.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE public.organizations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 150),
 created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.memberships (
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 role text NOT NULL CHECK (role IN ('owner','admin','editor','viewer')),
 PRIMARY KEY (organization_id, user_id)
);

CREATE OR REPLACE FUNCTION public.is_org_member(candidate_org_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
 SELECT EXISTS (
   SELECT 1 FROM public.memberships m
   WHERE m.organization_id = candidate_org_id
     AND m.user_id = (SELECT auth.uid())
 );
$$;

CREATE TABLE public.assets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
 uploader_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
 storage_key text NOT NULL UNIQUE,
 sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
 bytes bigint NOT NULL CHECK (bytes > 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id, organization_id) -- enables cross-tenant FK enforcement for render jobs
);

CREATE TABLE public.render_jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
 request_key text NOT NULL CHECK (char_length(request_key) BETWEEN 1 AND 128),
 request_hash char(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
 source_asset_id uuid NOT NULL,
 FOREIGN KEY (source_asset_id, organization_id) REFERENCES public.assets(id, organization_id),
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','completed','failed','reconcile')),
 lease_token uuid,
 lease_expires_at timestamptz,
 output_manifest jsonb,
 cost_cents bigint NOT NULL DEFAULT 0 CHECK(cost_cents >= 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (organization_id, request_key)
);

CREATE TABLE public.render_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 job_id uuid NOT NULL REFERENCES public.render_jobs(id) ON DELETE CASCADE,
 attempt_no integer NOT NULL CHECK (attempt_no > 0),
 lease_token uuid NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(),
 ended_at timestamptz,
 result text CHECK(result IN ('completed','failed','expired','cancelled')),
 UNIQUE(job_id, attempt_no)
);

CREATE TABLE public.render_outbox (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 job_id uuid NOT NULL REFERENCES public.render_jobs(id) ON DELETE CASCADE,
 event_type text NOT NULL CHECK(event_type IN ('render_requested')),
 dispatched_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(job_id, event_type)
);

CREATE INDEX ON public.render_jobs (organization_id, created_at DESC);
CREATE INDEX ON public.render_jobs (state, lease_expires_at);
CREATE INDEX ON public.render_outbox (dispatched_at, created_at);

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.render_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.render_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.render_outbox ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_read ON public.organizations FOR SELECT TO authenticated
 USING (public.is_org_member(id));
CREATE POLICY membership_read ON public.memberships FOR SELECT TO authenticated
 USING (user_id = (SELECT auth.uid()));
CREATE POLICY asset_read ON public.assets FOR SELECT TO authenticated
 USING (public.is_org_member(organization_id));
CREATE POLICY job_read ON public.render_jobs FOR SELECT TO authenticated
 USING (public.is_org_member(organization_id));
-- Attempts and outbox are service-owned and are not directly exposed to end-users.
-- No authenticated write policies. All mutations go through an authorized service layer
-- with explicit role checks and the transactional outbox pattern.

REVOKE INSERT, UPDATE, DELETE ON public.organizations,public.memberships,public.assets,
 public.render_jobs,public.render_attempts,public.render_outbox FROM anon,authenticated;
-- Function execution grants MUST be narrowed after service-role and auth integration.
