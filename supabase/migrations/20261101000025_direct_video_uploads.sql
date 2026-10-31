-- 0025: Route C — Direct Video Uploads (Task 14).
-- A phone/web client uploads a <=3-minute video straight to R2 via the generic
-- /reserve → PUT → /complete pipeline (kind 'clip_video'; works with the PC off),
-- then submits a processing_jobs row with source='direct'. The owner's PC claims
-- it through cloud_claim_job, downloads the object via /media-url, ffprobe-validates
-- (hard <=180 s cap) and registers a `dir_*` youtube_inbox row at DOWNLOADED — after
-- which the UNCHANGED transcription/highlight/render workers produce ONE full-length
-- captioned clip (no AI highlight selection, no auto word-snap trim).

-- Live-DB drift healing: production already carries `stage` and the
-- 'cancelled' status value (added out-of-band 2026-10-26/27; worker routes
-- PATCH and select them) while no prior migration file declares them.
-- Re-declared additively here so fresh environments converge with production;
-- live data is untouched. The status check is ALSO genuinely fixed: live
-- `processing_jobs_status_check` lacks 'cancelled', which makes /jobs/cancel
-- fail with a constraint violation today.

alter table public.processing_jobs
  add column if not exists stage text;

alter table public.processing_jobs
  add column if not exists source text not null default 'youtube'
    check (source in ('youtube', 'direct')),
  add column if not exists upload_object_id uuid
    references public.media_objects(id) on delete set null,
  add column if not exists source_title text;

-- Direct rows carry no YouTube URL.
alter table public.processing_jobs alter column youtube_url drop not null;

do $$ begin
  alter table public.processing_jobs drop constraint if exists processing_jobs_status_check;
end $$;

alter table public.processing_jobs
  add constraint processing_jobs_status_check
  check (status in ('queued', 'claimed', 'done', 'failed', 'cancelled'));

create index if not exists idx_pjobs_direct_obj
  on public.processing_jobs (upload_object_id)
  where upload_object_id is not null;

-- Grant the PC worker a queued direct job for download+ingestion.
-- One active job per (user, uploaded object); rows appear only AFTER /complete,
-- so a queued direct job always references a fully-uploaded, quota-accounted object.
create or replace function public.cloud_submit_direct_job(
  p_user_id uuid, p_media_object_id uuid, p_title text default null)
returns table (id uuid, status text)
language plpgsql security definer set search_path = public as $$
#variable_conflict use_column
declare
  v_obj record;
  v_existing uuid;
  v_job uuid;
begin
  select mo.id into v_obj
    from media_objects mo
    where mo.id = p_media_object_id
      and mo.user_id = p_user_id
      and mo.kind = 'clip_video'
      and mo.mime_type = 'video/mp4'
      and mo.status = 'active';
  if not found then
    raise exception 'SUBMIT_DIRECT: unknown, non-owned, or non-active clip_video object';
  end if;

  -- idempotent: one active job per uploaded object per user
  select pj.id into v_existing
    from processing_jobs pj
    where pj.user_id = p_user_id
      and pj.upload_object_id = p_media_object_id
      and pj.status in ('queued', 'claimed')
    limit 1;
  if found then
    return query select v_existing as id, 'queued'::text as status;
    return;
  end if;

  insert into processing_jobs (user_id, youtube_url, source, upload_object_id, source_title)
    values (p_user_id, null, 'direct', p_media_object_id,
            left(trim(coalesce(p_title, '')), 200))
  returning processing_jobs.id into v_job;

  return query select v_job as id, 'queued'::text as status;
end;
$$;

-- Extended claim return (supersedes 20261026000012): same global FIFO + 45-minute
-- stale-lease reclamation + 3-consecutive-user fairness, widened to describe
-- direct jobs. youtube_url stays the canonical source column for youtube rows
-- (null for direct rows); direct rows reference their uploaded R2 object instead. Postgres forbids CREATE OR REPLACE with a changed OUT row type,
-- so the old overload is dropped first (service-role grants are re-issued below;
-- revokes were applied to the old overload at every prior revision).
drop function if exists public.cloud_claim_job(text);

create or replace function public.cloud_claim_job(p_worker_id text)
returns table (job_id uuid, user_id uuid, youtube_url text,
               source text, upload_object_id uuid, source_title text)
language plpgsql security definer set search_path = public as $$
declare
  v_job uuid;
  v_recent_same_user uuid;
  v_recent_count int;
begin
  -- Step A: Reclaim leases older than 45 minutes
  update processing_jobs pj
  set status = 'queued', claimed_at = null
  where pj.status = 'claimed' and pj.claimed_at < now() - interval '45 minutes';

  -- Step B: Check fairness: has the same user claimed the last 3 consecutive jobs?
  select pj_agg.user_id, count(*) into v_recent_same_user, v_recent_count
  from (
    select pj_inner.user_id from processing_jobs pj_inner
    where pj_inner.status in ('claimed', 'done')
    order by pj_inner.claimed_at desc nulls last limit 3
  ) pj_agg
  group by pj_agg.user_id
  having count(*) >= 3;

  -- If same user had 3 consecutive claims AND there are jobs from OTHER users queued,
  -- prioritize other users to prevent starvation.
  if v_recent_count >= 3 and exists (
    select 1 from processing_jobs pj_q where pj_q.status = 'queued' and pj_q.user_id != v_recent_same_user
  ) then
    update processing_jobs pj_target set status = 'claimed', claimed_at = now()
    where pj_target.id = (
      select j.id from processing_jobs j
      join profiles p on p.user_id = j.user_id
      where j.status = 'queued' and j.user_id != v_recent_same_user
      order by p.queue_priority asc, j.created_at asc limit 1 for update skip locked
    )
    returning pj_target.id into v_job;
  else
    update processing_jobs pj_target set status = 'claimed', claimed_at = now()
    where pj_target.id = (
      select j.id from processing_jobs j
      join profiles p on p.user_id = j.user_id
      where j.status = 'queued'
      order by p.queue_priority asc, j.created_at asc limit 1 for update skip locked
    )
    returning pj_target.id into v_job;
  end if;

  if v_job is null then return; end if;
  return query
    select pj_res.id, pj_res.user_id, pj_res.youtube_url,
           pj_res.source, pj_res.upload_object_id, pj_res.source_title
    from processing_jobs pj_res where pj_res.id = v_job;
end;
$$;

-- Ownership model of this product: writes go through the Worker/PC service identity
-- only; clients submit via worker routes which call these functions server-side.
revoke all on function public.cloud_submit_direct_job(uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.cloud_submit_direct_job(uuid, uuid, text) to service_role;

revoke all on function public.cloud_claim_job(text) from public, authenticated, anon;
grant execute on function public.cloud_claim_job(text) to service_role;

-- Post-apply permission verification (expected: 2 rows, both service_role + EXECUTE;
-- any anon/authenticated/public row is a grant regression):
select grantee, privilege_type, routine_name
from information_schema.routine_privileges
where routine_schema = 'public'
  and routine_name in ('cloud_submit_direct_job', 'cloud_claim_job')
order by routine_name, grantee, privilege_type;
