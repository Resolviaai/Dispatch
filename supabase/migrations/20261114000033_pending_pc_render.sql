-- 0033: Pending PC render queue (spec Phase 4 — dual render fallback).
-- When the phone exports a clip edited in full-source mode while the PC is
-- unreachable, the user can defer the render to the headless PC instead of
-- burning the 720p cached proxy into the final cut. clips_cloud gains:
--   pending_pc_render   boolean      — set by the phone request, drained by the PC sync loop
--   render_requested_at timestamptz  — queue ordering + diagnostics
-- Columns follow the table's existing model: the Worker writes with
-- service_role on behalf of both the phone and the PC; authenticated reads
-- flow through the existing clips_cloud RLS policies (no policy changes —
-- new columns inherit the table grants).
-- RPCs (service_role only, like every cloud_* helper since 0002):
--   cloud_request_pc_render(p_user_id, p_local_clip_id)
--       Flag one owned clip for PC rendering. Idempotent: re-requesting
--       refreshes render_requested_at.
--   cloud_list_pending_pc_renders(p_user_id)
--       Oldest-first pending queue snapshot for the PC poll loop. Includes
--       render_fingerprint/rendered_fingerprint so the PC can skip clips
--       whose current state is already rendered fresh.
-- Worker wiring: POST /clips/:id/request-pc-render calls the request RPC;
-- GET /sync/pc/renders calls the list RPC; POST /sync/pc/renders/complete
-- clears the flag with a direct service-role PATCH (same pattern as
-- /sync/pc/render-complete). dispatch/cloud/sync.py drains the queue in the
-- 5-minute sync loop: pull latest cloud timeline -> render_clip 1080p from
-- the raw source -> R2 upload (kind render) -> /sync/pc/media-link ->
-- /sync/pc/render-complete -> clear pending_pc_render.
-- Rollback: drop the two functions, then
--   alter table public.clips_cloud
--     drop column if exists pending_pc_render,
--     drop column if exists render_requested_at;

alter table public.clips_cloud
  add column if not exists pending_pc_render boolean not null default false,
  add column if not exists render_requested_at timestamptz;

create or replace function public.cloud_request_pc_render(
  p_user_id uuid, p_local_clip_id text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
  update public.clips_cloud
     set pending_pc_render = true,
         render_requested_at = now(),
         cloud_updated_at = now()
   where user_id = p_user_id
     and local_clip_id = p_local_clip_id;
$$;

create or replace function public.cloud_list_pending_pc_renders(p_user_id uuid)
returns table (
  id uuid,
  local_clip_id text,
  render_requested_at timestamptz,
  range_revision int,
  render_fingerprint text,
  rendered_fingerprint text)
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select c.id, c.local_clip_id, c.render_requested_at, c.range_revision,
         c.render_fingerprint, c.rendered_fingerprint
    from public.clips_cloud c
   where c.user_id = p_user_id
     and c.pending_pc_render
   order by c.render_requested_at asc nulls last, c.cloud_updated_at asc;
$$;

revoke all on function public.cloud_request_pc_render(uuid, text) from public, anon, authenticated;
revoke all on function public.cloud_list_pending_pc_renders(uuid) from public, anon, authenticated;
grant execute on function public.cloud_request_pc_render(uuid, text) to service_role;
grant execute on function public.cloud_list_pending_pc_renders(uuid) to service_role;
