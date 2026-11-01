-- 0027: Direct Mobile-to-Cloud Publishing Queue
-- Allows phone to enqueue social publishing jobs directly without PC dependency.
-- Worker and cloud queue process platforms using user credentials stored in Supabase.

create table if not exists public.publishing_jobs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.profiles(user_id) on delete cascade,
    clip_id text not null,
    title text not null,
    description text default '',
    platforms jsonb not null default '["youtube"]'::jsonb,
    publish_mode text not null default 'private' check (publish_mode in ('private', 'unlisted', 'public')),
    r2_key text,
    rendered_fingerprint text,
    status text not null default 'queued' check (status in ('queued', 'processing', 'published', 'failed')),
    platform_results jsonb not null default '{}'::jsonb,
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz
);

create index if not exists idx_pub_jobs_user_clip on public.publishing_jobs (user_id, clip_id, status);
create index if not exists idx_pub_jobs_queue on public.publishing_jobs (created_at) where status = 'queued';

alter table public.publishing_jobs enable row level security;
drop policy if exists "publishing_jobs: users read own" on public.publishing_jobs;
create policy "publishing_jobs: users read own" on public.publishing_jobs for select using (user_id = auth.uid());

revoke insert, update, delete on public.publishing_jobs from public, anon;
grant select on public.publishing_jobs to authenticated, service_role;

create or replace function public.cloud_enqueue_publishing_job(
    p_user_id uuid,
    p_clip_id text,
    p_title text,
    p_description text,
    p_platforms jsonb,
    p_publish_mode text,
    p_r2_key text,
    p_rendered_fingerprint text
) returns table (id uuid, status text) language plpgsql security definer set search_path = public as $$
declare
    v_job_id uuid;
begin
    insert into public.publishing_jobs (
        user_id, clip_id, title, description, platforms, publish_mode, r2_key, rendered_fingerprint, status
    ) values (
        p_user_id, p_clip_id, p_title, coalesce(p_description, ''), coalesce(p_platforms, '["youtube"]'::jsonb),
        coalesce(p_publish_mode, 'private'), p_r2_key, p_rendered_fingerprint, 'queued'
    ) returning publishing_jobs.id into v_job_id;

    return query select v_job_id, 'queued'::text;
end;
$$;

revoke all on function public.cloud_enqueue_publishing_job(uuid, text, text, text, jsonb, text, text, text) from public, authenticated, anon;
grant execute on function public.cloud_enqueue_publishing_job(uuid, text, text, text, jsonb, text, text, text) to service_role;
