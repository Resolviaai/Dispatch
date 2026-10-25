-- 0010: Multi-account connected destinations (YouTube, Instagram, LinkedIn, Twitter/X),
-- per-user Gemini API keys vault, and admin priority scheduling with fairness.

-- 1. Profiles queue priority
alter table public.profiles add column if not exists queue_priority int not null default 100 check (queue_priority >= 1 and queue_priority <= 1000);

-- 2. Connected Accounts Table (Multi-account support for YouTube and Instagram, single for LinkedIn & X)
create table if not exists public.connected_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  provider text not null check (provider in ('youtube', 'instagram', 'linkedin', 'twitter')),
  account_id text not null,
  account_name text not null,
  account_email text,
  avatar_url text,
  credentials_encrypted text not null,
  metadata jsonb not null default '{}'::jsonb,
  is_verified boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_user_provider_account unique (user_id, provider, account_id)
);
create index if not exists idx_connected_accounts_user on public.connected_accounts(user_id);
create index if not exists idx_connected_accounts_provider on public.connected_accounts(user_id, provider);
alter table public.connected_accounts enable row level security;
revoke all on public.connected_accounts from anon, authenticated;
grant select on public.connected_accounts to authenticated;
create policy "users can view own connected accounts" on public.connected_accounts
  for select using (auth.uid() = user_id);

-- 3. Per-User Gemini API Keys Vault Table
create table if not exists public.gemini_connections (
  user_id uuid primary key references public.profiles(user_id) on delete cascade,
  api_key_encrypted text not null,
  key_prefix text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.gemini_connections enable row level security;
revoke all on public.gemini_connections from anon, authenticated;

-- Backwards compatibility: migrate existing youtube_connections into connected_accounts
insert into public.connected_accounts (user_id, provider, account_id, account_name, account_email, credentials_encrypted, created_at)
select
  user_id,
  'youtube',
  coalesce(google_email, user_id::text),
  coalesce(google_email, 'YouTube Channel'),
  google_email,
  refresh_token_encrypted,
  connected_at
from public.youtube_connections
on conflict (user_id, provider, account_id) do nothing;

-- 4. RPC Functions for Connected Accounts
create or replace function public.cloud_list_connected_accounts(p_user_id uuid)
returns table (
  id uuid,
  provider text,
  account_id text,
  account_name text,
  account_email text,
  avatar_url text,
  metadata jsonb,
  is_verified boolean,
  created_at timestamptz
) language sql security definer set search_path = public as $$
  select id, provider, account_id, account_name, account_email, avatar_url, metadata, is_verified, created_at
  from connected_accounts
  where user_id = p_user_id
  order by created_at desc;
$$;

create or replace function public.cloud_get_account_credentials(p_account_id uuid, p_user_id uuid)
returns text language sql security definer set search_path = public as $$
  select credentials_encrypted
  from connected_accounts
  where id = p_account_id and user_id = p_user_id;
$$;

create or replace function public.cloud_get_credentials_by_account_id(p_account_id text, p_user_id uuid default null)
returns text language sql security definer set search_path = public as $$
  select credentials_encrypted
  from connected_accounts
  where (account_id = p_account_id or id::text = p_account_id)
    and (p_user_id is null or user_id = p_user_id)
  limit 1;
$$;

create or replace function public.cloud_delete_connected_account(p_account_id uuid, p_user_id uuid)
returns boolean language plpgsql security definer set search_path = public as $$
begin
  delete from connected_accounts
  where id = p_account_id and user_id = p_user_id;
  return found;
end;
$$;

create or replace function public.cloud_store_connected_account(
  p_user_id uuid,
  p_provider text,
  p_account_id text,
  p_account_name text,
  p_account_email text,
  p_avatar_url text,
  p_credentials_encrypted text,
  p_metadata jsonb default '{}'::jsonb
) returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_count int;
  v_existing_id uuid;
  v_id uuid;
begin
  select id into v_existing_id from connected_accounts
  where user_id = p_user_id and provider = p_provider and account_id = p_account_id;

  if v_existing_id is null then
    if p_provider in ('linkedin', 'twitter') then
      select count(*) into v_count from connected_accounts
      where user_id = p_user_id and provider = p_provider;
      if v_count >= 1 then
        raise exception 'LIMIT_EXCEEDED: Maximum 1 % account allowed per user', p_provider;
      end if;
    end if;

    insert into connected_accounts (
      user_id, provider, account_id, account_name, account_email, avatar_url, credentials_encrypted, metadata, updated_at
    ) values (
      p_user_id, p_provider, p_account_id, p_account_name, p_account_email, p_avatar_url, p_credentials_encrypted, p_metadata, now()
    ) returning id into v_id;
  else
    update connected_accounts set
      account_name = p_account_name,
      account_email = p_account_email,
      avatar_url = p_avatar_url,
      credentials_encrypted = p_credentials_encrypted,
      metadata = p_metadata,
      is_verified = true,
      updated_at = now()
    where id = v_existing_id
    returning id into v_id;
  end if;

  return v_id;
end;
$$;

-- 5. RPC Functions for Gemini Keys Vault
create or replace function public.cloud_store_gemini_key(p_user_id uuid, p_key_encrypted text, p_key_prefix text)
returns void language sql security definer set search_path = public as $$
  insert into gemini_connections (user_id, api_key_encrypted, key_prefix, updated_at)
  values (p_user_id, p_key_encrypted, p_key_prefix, now())
  on conflict (user_id) do update set
    api_key_encrypted = excluded.api_key_encrypted,
    key_prefix = excluded.key_prefix,
    updated_at = now();
$$;

create or replace function public.cloud_get_gemini_key(p_user_id uuid)
returns text language sql security definer set search_path = public as $$
  select api_key_encrypted from gemini_connections where user_id = p_user_id;
$$;

create or replace function public.cloud_delete_gemini_key(p_user_id uuid)
returns void language sql security definer set search_path = public as $$
  delete from gemini_connections where user_id = p_user_id;
$$;

create or replace function public.cloud_get_gemini_status(p_user_id uuid)
returns table (has_key boolean, key_prefix text, updated_at timestamptz)
language sql security definer set search_path = public as $$
  select true, key_prefix, updated_at from gemini_connections where user_id = p_user_id;
$$;

-- 6. RPC Function for Admin Priority
create or replace function public.cloud_set_user_priority(p_user_id uuid, p_priority int)
returns void language plpgsql security definer set search_path = public as $$
begin
  if p_priority < 1 or p_priority > 1000 then
    raise exception 'INVALID_PRIORITY: Priority must be between 1 and 1000';
  end if;
  update public.profiles set queue_priority = p_priority where user_id = p_user_id;
end;
$$;

-- 7. Upgraded Job Claiming with Stale Lease Reclaim + Priority + Fairness
create or replace function public.cloud_claim_job(p_worker_id text)
returns table (job_id uuid, user_id uuid, youtube_url text)
language plpgsql security definer set search_path = public as $$
declare
  v_job uuid;
  v_recent_same_user uuid;
  v_recent_count int;
begin
  -- Step A: Reclaim leases older than 45 minutes
  update processing_jobs
  set status = 'queued', claimed_at = null
  where status = 'claimed' and claimed_at < now() - interval '45 minutes';

  -- Step B: Check fairness: has the same user claimed the last 3 consecutive jobs?
  select pj.user_id, count(*) into v_recent_same_user, v_recent_count
  from (
    select user_id from processing_jobs
    where status in ('claimed', 'done')
    order by claimed_at desc nulls last limit 3
  ) pj
  group by pj.user_id
  having count(*) >= 3;

  -- If same user had 3 consecutive claims AND there are jobs from OTHER users queued,
  -- prioritize other users to prevent starvation.
  if v_recent_count >= 3 and exists (
    select 1 from processing_jobs where status = 'queued' and user_id != v_recent_same_user
  ) then
    update processing_jobs set status = 'claimed', claimed_at = now()
    where id = (
      select j.id from processing_jobs j
      join profiles p on p.user_id = j.user_id
      where j.status = 'queued' and j.user_id != v_recent_same_user
      order by p.queue_priority asc, j.created_at asc limit 1 for update skip locked
    )
    returning id into v_job;
  else
    update processing_jobs set status = 'claimed', claimed_at = now()
    where id = (
      select j.id from processing_jobs j
      join profiles p on p.user_id = j.user_id
      where j.status = 'queued'
      order by p.queue_priority asc, j.created_at asc limit 1 for update skip locked
    )
    returning id into v_job;
  end if;

  if v_job is null then return; end if;
  return query select v_job, pj.user_id, pj.youtube_url from processing_jobs pj where pj.id = v_job;
end;
$$;

-- Revocations and grants
revoke all on function public.cloud_list_connected_accounts(uuid) from public, anon;
revoke all on function public.cloud_get_account_credentials(uuid, uuid) from public, anon, authenticated;
revoke all on function public.cloud_get_credentials_by_account_id(text, uuid) from public, anon, authenticated;
revoke all on function public.cloud_delete_connected_account(uuid, uuid) from public, anon;
revoke all on function public.cloud_store_connected_account(uuid, text, text, text, text, text, text, jsonb) from public, anon, authenticated;
revoke all on function public.cloud_store_gemini_key(uuid, text, text) from public, anon, authenticated;
revoke all on function public.cloud_get_gemini_key(uuid) from public, anon, authenticated;
revoke all on function public.cloud_delete_gemini_key(uuid) from public, anon;
revoke all on function public.cloud_get_gemini_status(uuid) from public, anon;
revoke all on function public.cloud_set_user_priority(uuid, int) from public, anon, authenticated;

grant execute on function public.cloud_list_connected_accounts(uuid) to service_role;
grant execute on function public.cloud_get_account_credentials(uuid, uuid) to service_role;
grant execute on function public.cloud_get_credentials_by_account_id(text, uuid) to service_role;
grant execute on function public.cloud_delete_connected_account(uuid, uuid) to service_role;
grant execute on function public.cloud_store_connected_account(uuid, text, text, text, text, text, text, jsonb) to service_role;
grant execute on function public.cloud_store_gemini_key(uuid, text, text) to service_role;
grant execute on function public.cloud_get_gemini_key(uuid) to service_role;
grant execute on function public.cloud_delete_gemini_key(uuid) to service_role;
grant execute on function public.cloud_get_gemini_status(uuid) to service_role;
grant execute on function public.cloud_set_user_priority(uuid, int) to service_role;
