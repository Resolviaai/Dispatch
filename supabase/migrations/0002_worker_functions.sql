-- 0002: service-side quota/lifecycle functions for the Cloudflare Worker (plan D4/D6).
-- Executed ONLY by service_role (Worker). SECURITY DEFINER so the advisory-lock +
-- FOR UPDATE + insert sequence runs as ONE atomic Postgres transaction.
-- Accounting contract (must match 0001_init.sql SECURITY MODEL + dispatch/cloud):
--   * stored usage counts media_objects with status active OR deletion_pending
--     (deletion_pending bytes are NOT released until cloud_confirm_deletion);
--   * reserved usage counts expected_bytes of pending, unexpired reservations;
--   * per-user and global checks both use stored + reserved usage;
--   * limits: user quota (default 2,000,000,000; max 10,000,000,000),
--     global cap 10,000,000,000, clip cap 512 MiB, thumbnail cap 8 MiB,
--     max 3 outstanding (pending, unexpired) reservations per user;
--   * completion requires 0 < actual_bytes <= expected_bytes and <= kind cap;
--   * re-reserve with the same client_token returns the existing reservation
--     unchanged (parameters must match) BEFORE any quota/outstanding rejection.
-- Rollback: drop function each; nothing else depends on them.

create or replace function public.cloud_quota_usage(p_user_id uuid)
returns table (user_stored_bytes bigint, user_reserved_bytes bigint,
               user_outstanding int, global_stored_bytes bigint, global_reserved_bytes bigint)
language sql security definer set search_path = public as $$
  select
    coalesce(sum(case when mo.user_id = p_user_id and mo.status in ('active','deletion_pending') then mo.byte_count end), 0),
    (select coalesce(sum(ur.expected_bytes), 0) from upload_reservations ur
       join media_objects m2 on m2.id = ur.media_object_id
       where ur.user_id = p_user_id and ur.status = 'pending' and m2.status = 'pending'),
    (select count(*) from upload_reservations ur
       where ur.user_id = p_user_id and ur.status = 'pending' and ur.expires_at > now()),
    coalesce(sum(case when mo.status in ('active','deletion_pending') then mo.byte_count end), 0),
    (select coalesce(sum(ur.expected_bytes), 0) from upload_reservations ur
       join media_objects m3 on m3.id = ur.media_object_id
       where ur.status = 'pending' and m3.status = 'pending')
  from media_objects mo;
$$;

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

  -- Idempotent retry FIRST: a reused client_token must never be rejected by quota
  -- limits and must never alter the original reservation's parameters.
  -- Explicit semantics for a token that is no longer pending (unique (user_id,
  -- client_token) forbids silent re-use): completed -> reject as completed;
  -- expired/cancelled -> reject and require a fresh token.
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
  return query select v_res, v_key, v_obj;
end;
$$;

create or replace function public.cloud_complete_upload(
  p_reservation_id uuid, p_actual_bytes bigint, p_final_key text)
returns media_objects language plpgsql security definer set search_path = public as $$
declare
  v_res upload_reservations%rowtype; v_obj media_objects;
begin
  select * into v_res from upload_reservations where id = p_reservation_id for update;
  if not found then raise exception 'COMPLETE: unknown reservation'; end if;
  select * into v_obj from media_objects where id = v_res.media_object_id for update;
  if v_res.status = 'completed' then
    if v_obj.final_key is distinct from p_final_key then
      raise exception 'COMPLETE: idempotent replay with different final key';
    end if;
    return v_obj;  -- duplicate completion is a no-op returning the stored object
  end if;
  if v_res.status <> 'pending' then raise exception 'COMPLETE: reservation not pending'; end if;
  -- real R2 size (Worker-verified head) governs committed usage, never the client claim
  if p_actual_bytes <= 0 or p_actual_bytes > v_res.expected_bytes then
    raise exception 'COMPLETE: actual size exceeds reserved expectation';
  end if;
  if p_actual_bytes > (case v_obj.kind when 'clip_video' then 536870912 else 8388608 end) then
    raise exception 'COMPLETE: actual size exceeds kind cap';
  end if;
  update media_objects set status = 'active', final_key = p_final_key, staging_key = null,
    byte_count = p_actual_bytes where id = v_obj.id returning * into v_obj;
  update upload_reservations set status = 'completed' where id = v_res.id;
  return v_obj;
end;
$$;

create or replace function public.cloud_reserve_lookup(p_reservation_id uuid)
returns table (media_object_id uuid, user_id uuid, status text)
language sql security definer set search_path = public as $$
  select ur.media_object_id, ur.user_id, ur.status from upload_reservations ur where ur.id = p_reservation_id;
$$;

create or replace function public.cloud_fail_reservation(p_reservation_id uuid)
returns void language plpgsql security definer set search_path = public as $$
declare v_obj uuid;
begin
  select media_object_id into v_obj from upload_reservations where id = p_reservation_id;
  update upload_reservations set status = 'cancelled' where id = p_reservation_id and status = 'pending';
  delete from media_objects where id = v_obj and status = 'pending';
end;
$$;

create or replace function public.cloud_mark_deletion(p_media_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  update media_objects set status = 'deletion_pending' where id = p_media_id and status = 'active';
end;
$$;

create or replace function public.cloud_confirm_deletion(p_media_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  update media_objects set status = 'deleted', deleted_at = now(), byte_count = 0, final_key = null
    where id = p_media_id and status = 'deletion_pending';
end;
$$;

create or replace function public.cloud_fail_reservation_by_media(p_media_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  update upload_reservations set status = 'cancelled'
    where media_object_id = p_media_id and status = 'pending';
  delete from media_objects where id = p_media_id and status = 'pending';
end;
$$;

create or replace function public.cloud_list_abandoned_staging()
returns table (media_object_id uuid, staging_key text, reservation_id uuid)
language sql security definer set search_path = public as $$
  -- Reservations past their expiry whose staging media object still exists.
  -- Accounting keeps counting these (pending media object) until the R2 object
  -- is confirmed deleted via cloud_confirm_abandon.
  select mo.id, mo.staging_key, ur.id
  from upload_reservations ur
  join media_objects mo on mo.id = ur.media_object_id
  where ur.status = 'pending' and ur.expires_at <= now() and mo.status = 'pending';
$$;

create or replace function public.cloud_confirm_abandon(p_media_object_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  -- Called by the Worker ONLY after the R2 staging object deletion succeeded
  -- (or head confirmed absence). Releases accounting and removes the row.
  update upload_reservations set status = 'expired'
    where media_object_id = p_media_object_id and status = 'pending';
  delete from media_objects where id = p_media_object_id and status = 'pending';
end;
$$;

revoke all on function public.cloud_quota_usage(uuid) from public, authenticated, anon;
revoke all on function public.cloud_fail_reservation_by_media(uuid) from public, authenticated, anon;
revoke all on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) from public, authenticated, anon;
revoke all on function public.cloud_complete_upload(uuid, bigint, text) from public, authenticated, anon;
revoke all on function public.cloud_reserve_lookup(uuid) from public, authenticated, anon;
revoke all on function public.cloud_fail_reservation(uuid) from public, authenticated, anon;
revoke all on function public.cloud_mark_deletion(uuid) from public, authenticated, anon;
revoke all on function public.cloud_confirm_deletion(uuid) from public, authenticated, anon;
-- Only service_role (the Worker) retains execute; verify with:
--   select proname, proacl from pg_proc where proname like 'cloud_%';

-- Explicit least-privilege grants: service_role only. (Postgres grants EXECUTE on
-- functions to PUBLIC by default, so the revokes above are load-bearing.)
grant execute on function public.cloud_quota_usage(uuid) to service_role;
grant execute on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) to service_role;
grant execute on function public.cloud_complete_upload(uuid, bigint, text) to service_role;
grant execute on function public.cloud_reserve_lookup(uuid) to service_role;
grant execute on function public.cloud_fail_reservation(uuid) to service_role;
grant execute on function public.cloud_fail_reservation_by_media(uuid) to service_role;
grant execute on function public.cloud_mark_deletion(uuid) to service_role;
grant execute on function public.cloud_confirm_deletion(uuid) to service_role;
grant execute on function public.cloud_list_abandoned_staging() to service_role;
grant execute on function public.cloud_confirm_abandon(uuid) to service_role;

-- PERMISSION VERIFICATION (run after applying; NOT verified until executed on a
-- real database). Every cloud_* function must show service_role and NOT PUBLIC:
--   select p.proname, p.proacl
--   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--   where n.nspname = 'public' and p.proname like 'cloud_%';
-- Expected: proacl = {service_role=X/postgres,=X/postgres revoked} — i.e. no
-- plain '=X' grant for PUBLIC and no authenticated/anon entries.
