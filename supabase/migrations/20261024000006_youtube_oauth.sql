-- 0006: per-user YouTube OAuth storage (on-device sign-in flow, ADR-001 Decision 4).
-- Refresh tokens are stored ENCRYPTED (AES-256-GCM) with the server-side vault key
-- held as a Worker secret + PC env; never plaintext, never to clients.
-- oauth_states: single-use CSRF state for the authorization dance (10 min TTL).

create table if not exists public.youtube_connections (
  user_id uuid primary key references public.profiles(user_id) on delete cascade,
  refresh_token_encrypted text not null,
  google_email text,
  connected_at timestamptz not null default now()
);
alter table public.youtube_connections enable row level security;
-- No client policies: service_role only (Worker writes, PC reads).

create table if not exists public.oauth_states (
  state text primary key,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  created_at timestamptz not null default now()
);
create index if not exists idx_oauth_states_created on public.oauth_states (created_at);
alter table public.oauth_states enable row level security;

create or replace function public.cloud_store_refresh_token(
  p_user_id uuid, p_refresh_token_encrypted text, p_google_email text)
returns void language sql security definer set search_path = public as $$
  insert into youtube_connections (user_id, refresh_token_encrypted, google_email, connected_at)
  values (p_user_id, p_refresh_token_encrypted, p_google_email, now())
  on conflict (user_id) do update set
    refresh_token_encrypted = excluded.refresh_token_encrypted,
    google_email = excluded.google_email,
    connected_at = now();
$$;

create or replace function public.cloud_get_refresh_token(p_user_id uuid)
returns text language sql security definer set search_path = public as $$
  select refresh_token_encrypted from youtube_connections where user_id = p_user_id;
$$;

create or replace function public.cloud_delete_refresh_token(p_user_id uuid)
returns void language sql security definer set search_path = public as $$
  delete from youtube_connections where user_id = p_user_id;
$$;

create or replace function public.cloud_put_oauth_state(p_state text, p_user_id uuid)
returns void language sql security definer set search_path = public as $$
  insert into oauth_states (state, user_id) values (p_state, p_user_id);
$$;

create or replace function public.cloud_consume_oauth_state(p_state text)
returns uuid language plpgsql security definer set search_path = public as $$
  declare v_user uuid;
  begin
    -- Single use: delete-returning is atomic; older than 10 minutes is expired.
    delete from oauth_states
      where state = p_state and created_at > now() - interval '10 minutes'
      returning user_id into v_user;
    return v_user;
  end;
$$;

create or replace function public.cloud_prune_oauth_states()
returns void language sql security definer set search_path = public as $$
  delete from oauth_states where created_at <= now() - interval '10 minutes';
$$;

revoke all on function public.cloud_store_refresh_token(uuid, text, text) from public, authenticated, anon;
revoke all on function public.cloud_get_refresh_token(uuid) from public, authenticated, anon;
revoke all on function public.cloud_delete_refresh_token(uuid) from public, authenticated, anon;
revoke all on function public.cloud_put_oauth_state(text, uuid) from public, authenticated, anon;
revoke all on function public.cloud_consume_oauth_state(text) from public, authenticated, anon;
revoke all on function public.cloud_prune_oauth_states() from public, authenticated, anon;
grant execute on function public.cloud_store_refresh_token(uuid, text, text) to service_role;
grant execute on function public.cloud_get_refresh_token(uuid) to service_role;
grant execute on function public.cloud_delete_refresh_token(uuid) to service_role;
grant execute on function public.cloud_put_oauth_state(text, uuid) to service_role;
grant execute on function public.cloud_consume_oauth_state(text) to service_role;
grant execute on function public.cloud_prune_oauth_states() to service_role;
