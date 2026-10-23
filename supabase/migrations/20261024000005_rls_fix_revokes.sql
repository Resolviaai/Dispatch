-- 0005: revoke missed EXECUTE grants on two 0002 functions (found by the live
-- permission-verification query 2026-10-24: PUBLIC/anon/authenticated could call
-- cloud_list_abandoned_staging / cloud_confirm_abandon). Forward-only; no data changes.

revoke all on function public.cloud_list_abandoned_staging() from public, authenticated, anon;
revoke all on function public.cloud_confirm_abandon(uuid) from public, authenticated, anon;
grant execute on function public.cloud_list_abandoned_staging() to service_role;
grant execute on function public.cloud_confirm_abandon(uuid) to service_role;
