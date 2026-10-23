-- 0002: service-side quota/lifecycle functions for the Cloudflare Worker (plan D4/D6).
-- Executed ONLY by service_role (Worker). SECURITY DEFINER so the advisory-lock +
-- FOR UPDATE + insert sequence runs as ONE atomic Postgres transaction; grants are
-- service_role-only. Decimal-byte policy must match dispatch/cloud/config.py:
--   default user quota 2,000,000,000; global cap 10,000,000,000; clip cap 512 MiB;
--   thumbnail cap 8 MiB; max 3 outstanding reservations per user.
-- Rollback: drop function each; nothing else depends on them.

create or replace function public.cloud_quota_usage(p_user_id uuid)
returns table (user_active_bytes bigint, user_pending_bytes bigint,
               user_outstanding int, global_active_bytes bigint, global_pending_bytes bigint)
language sql security definer set search_path = public as $$
  select
    coalesce(sum(case when mo.user_id = p_user_id and mo.status = 'active' then mo.byte_count end), 0),
    coalesce(sum(case when mo.user_id = p_user_id and mo.status = 'pending' then mo.byte_count end), 0),
    (select count(*) from upload_reservations ur
       where ur.user_id = p_user_id and ur.status = 'pending' and ur.expires_at > now()),
    coalesce(sum(case when mo.status = 'active' then mo.byte_count end), 0),
    coalesce(sum(case when mo.status = 'pending' then mo.byte_count end), 0)
  from media_objects mo;
$$;

create or replace function public.cloud_reserve_upload(
  p_user_id uuid, p_kind text, p_mime text, p_expected_bytes bigint, p_client_token text,
  p_clip_id uuid default null)
returns table (reservation_id uuid, staging_key text, media_object_id uuid)
language plpgsql security definer set search_path = public as $$
declare
  v_quota bigint; v_user_active bigint; v_user_pending bigint; v_outstanding int;
  v_global_active bigint; v_global_pending bigint;
  v_kind_cap bigint; v_obj uuid; v_res uuid; v_key text;
begin
  -- Serialize quota decisions globally (plan D4): one reservation at a time.
  perform pg_advisory_xact_lock(918273645);
  select storage_quota_bytes into v_quota from profiles where user_id = p_user_id for update;
  if not found then raise exception 'RESERVE: unknown user'; end if;

  select user_active_bytes, user_pending_bytes, user_outstanding, global_active_bytes, global_pending_bytes
    into v_user_active, v_user_pending, v_outstanding, v_global_active, v_global_pending
    from cloud_quota_usage(p_user_id);

  if p_kind = 'clip_video' then v_kind_cap := 536870912;
  elsif p_kind = 'thumbnail' then v_kind_cap := 8388608;
  else raise exception 'RESERVE: unsupported kind'; end if;
  if p_mime not in ('video/mp4', 'image/jpeg') then raise exception 'RESERVE: unsupported mime'; end if;
  if p_expected_bytes <= 0 or p_expected_bytes > v_kind_cap then raise exception 'RESERVE: size out of bounds'; end if;
  if v_outstanding >= 3 then raise exception 'RESERVE: outstanding limit'; end if;
  if v_user_active + v_user_pending + p_expected_bytes > v_quota then
    raise exception 'RESERVE: user quota exceeded'; end if;
  if v_global_active + v_global_pending + p_expected_bytes > 10000000000 then
    raise exception 'RESERVE: global cap exceeded'; end if;

  -- idempotent re-reserve with same client token returns the existing reservation
  select id, media_object_id into v_res, v_obj from upload_reservations
    where user_id = p_user_id and client_token = p_client_token and status = 'pending';
  if found then
    return query select v_res, mo.staging_key, v_obj from media_objects mo where mo.id = v_obj;
    return;
  end if;

  v_key := 'staging/' || p_user_id || '/' || gen_random_uuid();
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
  if v_res.status = 'completed' then  -- idempotent duplicate completion
    return v_obj;
  end if;
  if v_res.status <> 'pending' then raise exception 'COMPLETE: reservation not pending'; end if;
  -- real R2 size (Worker-verified head) governs committed usage, never the client claim
  if p_actual_bytes <= 0 or p_actual_bytes > (case v_obj.kind when 'clip_video' then 536870912 else 8388608 end) then
    raise exception 'COMPLETE: actual size out of bounds';
  end if;
  update media_objects set status = 'active', final_key = p_final_key, staging_key = null,
    byte_count = p_actual_bytes where id = v_obj.id returning * into v_obj;
  update upload_reservations set status = 'completed' where id = v_res.id;
  return v_obj;
end;
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

create or replace function public.cloud_reserve_lookup(p_reservation_id uuid)
returns table (media_object_id uuid, user_id uuid, status text)
language sql security definer set search_path = public as $$
  select ur.media_object_id, ur.user_id, ur.status from upload_reservations ur where ur.id = p_reservation_id;
$$;

revoke all on function public.cloud_quota_usage(uuid) from public, authenticated, anon;
revoke all on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) from public, authenticated, anon;
revoke all on function public.cloud_complete_upload(uuid, bigint, text) from public, authenticated, anon;
revoke all on function public.cloud_fail_reservation(uuid) from public, authenticated, anon;
revoke all on function public.cloud_mark_deletion(uuid) from public, authenticated, anon;
revoke all on function public.cloud_confirm_deletion(uuid) from public, authenticated, anon;
-- service_role keeps default execute.
