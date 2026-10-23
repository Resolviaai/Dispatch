-- 0004: staging-key watch list (global-cap hardening, forward-only).
-- Problem: after completion/abandonment the staging object is deleted, but the
-- presigned PUT URL remains technically usable until its 600 s expiry. A client
-- replaying that URL can recreate an UNTRACKED R2 object.
-- Fix: every staging key stays watched for (reservation window 30 min + presign
-- grace 15 min). The scheduled /cleanup sweep re-deletes watched keys (idempotent)
-- throughout the window and the row is pruned only after it lapses.
-- Accounting note: this is an anti-orphan sweep, not a quota release - reserved/
-- stored accounting is unchanged. Physical bytes outside the reserve/complete
-- contract remain transiently possible (R2 has no native cap) - documented.

create table if not exists public.staging_watch (
  staging_key text primary key,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  watch_until timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists idx_staging_watch_until on public.staging_watch (watch_until);
alter table public.staging_watch enable row level security;
-- No client grants: service_role only (Worker). RLS with no permissive policy
-- denies authenticated/anon entirely.

create or replace function public.cloud_watch_staging(p_user_id uuid, p_staging_key text)
returns void language sql security definer set search_path = public as $$
  insert into staging_watch (staging_key, user_id, watch_until)
  values (p_staging_key, p_user_id, now() + interval '45 minutes')
  on conflict (staging_key) do update set watch_until = excluded.watch_until;
$$;

create or replace function public.cloud_list_watched_staging()
returns table (staging_key text) language sql security definer set search_path = public as $$
  select staging_key from staging_watch where watch_until > now();
$$;

create or replace function public.cloud_prune_staging_watch()
returns void language sql security definer set search_path = public as $$
  delete from staging_watch where watch_until <= now();
$$;

-- Hook reservation creation into the watch list by rewriting reserve's insert tail.
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

-- Release the watch when staging removal is confirmed or the upload is abandoned.
create or replace function public.cloud_confirm_staging_removed(p_media_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  -- The staging_watch row is intentionally KEPT: the presigned URL may still be
  -- replayed until expiry, so /cleanup keeps sweeping the key, then prunes it.
  update media_objects set staging_key = null
    where id = p_media_id and status = 'active' and staging_key is not null;
end;
$$;

revoke all on function public.cloud_watch_staging(uuid, text) from public, authenticated, anon;
revoke all on function public.cloud_list_watched_staging() from public, authenticated, anon;
revoke all on function public.cloud_prune_staging_watch() from public, authenticated, anon;
revoke all on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) from public, authenticated, anon;
grant execute on function public.cloud_watch_staging(uuid, text) to service_role;
grant execute on function public.cloud_list_watched_staging() to service_role;
grant execute on function public.cloud_prune_staging_watch() to service_role;
grant execute on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) to service_role;
