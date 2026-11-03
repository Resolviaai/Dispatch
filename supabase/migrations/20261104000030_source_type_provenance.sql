-- Migration: 20261104000030_source_type_provenance.sql
-- Ingestion source provenance tracking: distinguishes native camera recordings,
-- imported YouTube links, and direct video uploads.
-- Authoritative server-side guard ensures only camera recordings can be deleted from YouTube.

-- 1. Add source_type column to processing_jobs
alter table public.processing_jobs
  add column if not exists source_type text not null default 'youtube_import'
  check (source_type in ('camera', 'youtube_import', 'direct_upload'));

-- 2. Backfill existing processing_jobs rows
update public.processing_jobs
  set source_type = 'direct_upload'
  where source = 'direct' or upload_object_id is not null;

update public.processing_jobs
  set source_type = 'camera'
  where (session_id is not null and session_id like 'sess_%')
     or (youtube_url is not null and youtube_url like '%dispatch%');

-- 3. Upgrade cloud_submit_job with source_type and session_id tracking,
-- and extract video_id immediately upon submission.
drop function if exists public.cloud_submit_job(uuid, text);
drop function if exists public.cloud_submit_job(uuid, text, text, text);

create or replace function public.cloud_submit_job(
  p_user_id uuid,
  p_url text,
  p_source_type text default 'youtube_import',
  p_session_id text default null
)
returns table (id uuid, status text)
language plpgsql security definer set search_path = public as $$
#variable_conflict use_column
declare
  v_job uuid;
  v_existing uuid;
  v_existing_status text;
  v_extracted_vid text;
  v_valid_source_type text;
begin
  -- Validate and normalize source_type
  v_valid_source_type := case
    when p_source_type in ('camera', 'youtube_import', 'direct_upload') then p_source_type
    else 'youtube_import'
  end;

  -- Extract 11-char YouTube video ID from URL if present
  -- Supports youtu.be/<id>, watch?v=<id>, shorts/<id>
  v_extracted_vid := substring(p_url from '(?:watch\?v=|shorts/|youtu\.be/)([A-Za-z0-9_-]{11})');

  -- Dedupe: if a job is already queued or claimed for this exact URL, return that job
  select pj.id, pj.status into v_existing, v_existing_status from public.processing_jobs pj
    where pj.user_id = p_user_id and pj.youtube_url = p_url and pj.status in ('queued','claimed')
    limit 1;
  if found then
    return query select v_existing as id, v_existing_status as status;
    return;
  end if;

  -- Insert new queued processing job with explicit source provenance and extracted video_id
  insert into public.processing_jobs (
    user_id,
    youtube_url,
    status,
    source,
    source_type,
    session_id,
    video_id
  )
  values (
    p_user_id,
    p_url,
    'queued',
    'youtube',
    v_valid_source_type,
    p_session_id,
    v_extracted_vid
  )
  returning processing_jobs.id into v_job;

  return query select v_job as id, 'queued'::text as status;
end;
$$;

revoke all on function public.cloud_submit_job(uuid, text, text, text) from public, authenticated, anon;
grant execute on function public.cloud_submit_job(uuid, text, text, text) to service_role;

-- 4. Update cloud_submit_direct_job to ensure source_type = 'direct_upload'
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

  insert into processing_jobs (
    user_id,
    youtube_url,
    source,
    source_type,
    upload_object_id,
    source_title,
    video_id
  )
  values (
    p_user_id,
    null,
    'direct',
    'direct_upload',
    p_media_object_id,
    left(trim(coalesce(p_title, '')), 200),
    'dir_' || replace(p_media_object_id::text, '-', '')
  )
  returning processing_jobs.id into v_job;

  return query select v_job as id, 'queued'::text as status;
end;
$$;

revoke all on function public.cloud_submit_direct_job(uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.cloud_submit_direct_job(uuid, uuid, text) to service_role;
