-- 0007: remote processing job queue (friends submit from anywhere; the owner's
-- enrolled PC worker claims and processes — no shared Wi-Fi required).

create table if not exists public.processing_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  youtube_url text not null,
  status text not null default 'queued' check (status in ('queued','claimed','done','failed')),
  video_id text,
  last_error text,
  created_at timestamptz not null default now(),
  claimed_at timestamptz,
  finished_at timestamptz
);
create index if not exists idx_pjobs_user on public.processing_jobs (user_id, status);
create index if not exists idx_pjobs_queue on public.processing_jobs (created_at) where status = 'queued';
alter table public.processing_jobs enable row level security;
create policy "own jobs read" on public.processing_jobs for select using (user_id = auth.uid());
-- Writes go through the Worker/PC service identity only.

create or replace function public.cloud_submit_job(p_user_id uuid, p_url text)
returns table (id uuid, status text) language plpgsql security definer set search_path = public as $$
  declare v_job uuid; v_existing uuid;
  begin
    -- dedupe: one active job per identical URL per user
    select id into v_existing from processing_jobs
      where user_id = p_user_id and youtube_url = p_url and status in ('queued','claimed')
      limit 1;
    if found then
      return query select v_existing, 'queued'::text;
      return;
    end if;
    insert into processing_jobs (user_id, youtube_url) values (p_user_id, p_url)
      returning id into v_job;
    return query select v_job, 'queued'::text;
  end;
$$;

create or replace function public.cloud_claim_job(p_worker_id text)
returns table (job_id uuid, user_id uuid, youtube_url text)
language plpgsql security definer set search_path = public as $$
  declare v_job uuid;
  begin
    -- single-worker alpha: atomic claim of the oldest queued job
    update processing_jobs set status = 'claimed', claimed_at = now()
      where id = (
        select id from processing_jobs where status = 'queued'
        order by created_at asc limit 1 for update skip locked
      )
      returning id into v_job;
    if v_job is null then return; end if;
    return query select v_job, user_id, youtube_url from processing_jobs where id = v_job;
  end;
$$;

create or replace function public.cloud_finish_job(p_job_id uuid, p_status text, p_video_id text, p_error text)
returns void language sql security definer set search_path = public as $$
  update processing_jobs set status = p_status, video_id = p_video_id,
    last_error = p_error, finished_at = now()
    where id = p_job_id and status = 'claimed';
$$;

revoke all on function public.cloud_submit_job(uuid, text) from public, authenticated, anon;
revoke all on function public.cloud_claim_job(text) from public, authenticated, anon;
revoke all on function public.cloud_finish_job(uuid, text, text, text) from public, authenticated, anon;
grant execute on function public.cloud_submit_job(uuid, text) to service_role;
grant execute on function public.cloud_claim_job(text) to service_role;
grant execute on function public.cloud_finish_job(uuid, text, text, text) to service_role;
