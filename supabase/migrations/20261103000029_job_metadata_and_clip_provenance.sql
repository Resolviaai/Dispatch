-- Migration: 20261103000029_job_metadata_and_clip_provenance.sql
-- Add duration and session_id tracking to processing_jobs
-- Add youtube_video_id provenance to clips_cloud

-- 1. processing_jobs columns
alter table public.processing_jobs add column if not exists duration numeric;
alter table public.processing_jobs add column if not exists session_id text;

-- 2. clips_cloud columns
alter table public.clips_cloud add column if not exists youtube_video_id text;

-- 3. Enhance cloud_submit_job to deduplicate across finished/active jobs cleanly
create or replace function public.cloud_submit_job(p_user_id uuid, p_url text)
returns table (id uuid, status text)
language plpgsql security definer set search_path = public as $$
#variable_conflict use_column
declare
  v_job uuid;
  v_existing uuid;
  v_existing_status text;
begin
  -- Dedupe: if a job is already queued or claimed for this exact URL, return that job
  select pj.id, pj.status into v_existing, v_existing_status from public.processing_jobs pj
    where pj.user_id = p_user_id and pj.youtube_url = p_url and pj.status in ('queued','claimed')
    limit 1;
  if found then
    return query select v_existing as id, v_existing_status as status;
    return;
  end if;

  -- Insert new queued processing job
  insert into public.processing_jobs (user_id, youtube_url, status)
    values (p_user_id, p_url, 'queued')
    returning processing_jobs.id into v_job;
  return query select v_job as id, 'queued'::text as status;
end;
$$;
