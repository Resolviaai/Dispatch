-- 0008: revoke dangerous default grants on newer tables (found by live audit
-- 2026-10-24: migrations 0004/0006/0007 created tables without explicit revokes,
-- leaving anon/authenticated with INSERT/UPDATE/DELETE/TRUNCATE. RLS row policies
-- deny row writes today, but TRUNCATE is NOT covered by RLS and any future policy
-- mistake becomes exploitable. Least privilege: service-role only.)

revoke all on public.staging_watch from anon, authenticated;
revoke all on public.oauth_states from anon, authenticated;
revoke all on public.youtube_connections from anon, authenticated;
revoke all on public.processing_jobs from anon, authenticated;
-- keep the intended read-only client surface (RLS-scoped SELECT):
grant select on public.processing_jobs to authenticated;
