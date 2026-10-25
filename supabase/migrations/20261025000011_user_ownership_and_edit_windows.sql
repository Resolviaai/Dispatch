-- 00011: Support 'edit_window' kind in upload reservation & complete legacy token cleanup on disconnect.

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
  return query select v_res, v_key, v_obj;
end;
$$;

create or replace function public.cloud_delete_connected_account(p_account_id uuid, p_user_id uuid)
returns boolean language plpgsql security definer set search_path = public as $$
declare
  v_provider text;
begin
  select provider into v_provider from connected_accounts
  where id = p_account_id and user_id = p_user_id;

  delete from connected_accounts
  where id = p_account_id and user_id = p_user_id;

  if v_provider = 'youtube' then
    delete from youtube_connections where user_id = p_user_id;
  end if;

  return found;
end;
$$;

revoke all on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) from public, anon, authenticated;
grant execute on function public.cloud_reserve_upload(uuid, text, text, bigint, text, uuid) to service_role;

revoke all on function public.cloud_delete_connected_account(uuid, uuid) from public, anon, authenticated;
grant execute on function public.cloud_delete_connected_account(uuid, uuid) to service_role;
