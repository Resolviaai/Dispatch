-- 0018: Security hardening (audit 2026-10-28)
-- - revoke EXECUTE on three SECURITY DEFINER RPCs from `authenticated`
-- - stop exposing connected_accounts.credentials_encrypted to clients
-- - restore the staging_watch anti-orphan insert dropped by 0011
-- - allow the Facebook provider already emitted by the Worker
-- Forward-only, additive. No data changes.

-- 1. RPC grants: 0010 revoked only `public, anon`, leaving the Supabase
-- default EXECUTE grant on `authenticated` in place for these three functions.
revoke all on function public.cloud_list_connected_accounts(uuid) from public, anon, authenticated;
revoke all on function public.cloud_delete_gemini_key(uuid) from public, anon, authenticated;
revoke all on function public.cloud_get_gemini_status(uuid) from public, anon, authenticated;
grant execute on function public.cloud_list_connected_accounts(uuid) to service_role;
grant execute on function public.cloud_delete_gemini_key(uuid) to service_role;
grant execute on function public.cloud_get_gemini_status(uuid) to service_role;

-- 2. Connected accounts: never expose credential ciphertext to end users.
-- The app talks to the Worker, not this table, so column-scoped SELECT is enough.
revoke select on public.connected_accounts from authenticated;
grant select (
  id, user_id, provider, account_id, account_name, account_email,
  avatar_url, metadata, is_verified, created_at, updated_at
) on public.connected_accounts to authenticated;

-- 3. The Worker OAuth callback stores provider='facebook'; 0010's CHECK rejected it.
alter table public.connected_accounts
  drop constraint if exists connected_accounts_provider_check;
alter table public.connected_accounts
  add constraint connected_accounts_provider_check
  check (provider in ('youtube', 'instagram', 'linkedin', 'twitter', 'facebook'));

-- 4. Restore the staging_watch insert removed by 0011. Without it, a presigned
-- PUT replayed after /complete could recreate an untracked R2 object.
create or replace function public.cloud_reserve_upload(
  p_user_id uuid, p_kind text, p_mime text, p_expected_bytes bigint, p_client_token text,
  p_clip_id uuid default null)
returns table (reservation_id uuid, staging_key text, media_object_id uuid)
language plpgsql security definer set search_path = public as $$
declare
  v_quota bigint; v_usage record;
  v_kind_cap bigint; v_obj uuid; v_res uuid; v_key text;
  v_prev_kind text; v_prev_mime text; v_prev_bytes bigint; v_prev_status text; v_prev_clip uuid;
begin
  -- Serialize quota decisions globally; per-user row lock taken while held.
  perform pg_advisory_xact_lock(918273645);
  select storage_quota_bytes into v_quota from profiles where user_id = p_user_id for update;
  if not found then raise exception 'RESERVE: unknown user'; end if;

  select ur.id, ur.status, ur.expected_bytes, mo.id, mo.staging_key, mo.kind, mo.mime_type, mo.clip_id
    into v_res, v_prev_status, v_prev_bytes, v_obj, v_key, v_prev_kind, v_prev_mime, v_prev_clip
  from upload_reservations ur
  join media_objects mo on mo.id = ur.media_object_id
  where ur.user_id = p_user_id and ur.client_token = p_client_token;
  if found then
    if v_prev_status <> 'pending' then
      raise exception 'RESERVE: client_token already %; use a new client_token', v_prev_status;
    end if;
    if v_prev_kind <> p_kind or v_prev_mime <> p_mime or v_prev_bytes <> p_expected_bytes
       or v_prev_clip is distinct from p_clip_id then
      raise exception 'RESERVE: client_token reuse with different parameters';
    end if;
    return query select v_res, v_key, v_obj;
    return;
  end if;

  select * into v_usage from cloud_quota_usage(p_user_id);

  if p_kind = 'clip_video' then v_kind_cap := 536870912;
  elsif p_kind = 'edit_window' then v_kind_cap := 536870912;
  elsif p_kind = 'thumbnail' then v_kind_cap := 8388608;
  else raise exception 'RESERVE: unsupported kind'; end if;

  if p_mime not in ('video/mp4', 'image/jpeg') then raise exception 'RESERVE: unsupported mime'; end if;
  if p_expected_bytes <= 0 or p_expected_bytes > v_kind_cap then raise exception 'RESERVE: size out of bounds'; end if;
  if v_usage.user_outstanding >= 3 then raise exception 'RESERVE: outstanding limit'; end if;
  if v_usage.user_stored_bytes + v_usage.user_reserved_bytes + p_expected_bytes > v_quota then
    raise exception 'RESERVE: user quota exceeded';
  end if;
  if v_usage.global_stored_bytes + v_usage.global_reserved_bytes + p_expected_bytes > 10000000000 then
    raise exception 'RESERVE: global cap exceeded';
  end if;

  v_key := 'staging/' || p_user_id::text || '/' || gen_random_uuid()::text;
  insert into media_objects (user_id, clip_id, kind, status, staging_key, mime_type, byte_count)
    values (p_user_id, p_clip_id, p_kind, 'pending', v_key, p_mime, 0) returning id into v_obj;
  insert into upload_reservations (user_id, media_object_id, client_token, expected_bytes)
    values (p_user_id, v_obj, p_client_token, p_expected_bytes) returning id into v_res;
  insert into staging_watch (staging_key, user_id, watch_until)
    values (v_key, p_user_id, now() + interval '45 minutes')
    on conflict (staging_key) do update set watch_until = excluded.watch_until;
  return query select v_res, v_key, v_obj;
end;
$$;

revoke all on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) from public, anon, authenticated;
grant execute on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) to service_role;
