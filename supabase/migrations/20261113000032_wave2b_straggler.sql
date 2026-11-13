-- 0032: Wave 2b straggler — cloud_complete_upload kind caps were left behind when
-- 'edit_window' reserve support landed. cloud_reserve_upload accepts
-- kind 'edit_window' up to 536870912 bytes (00011:38, 00018:69, 00023:50), but
-- this completion RPC still capped every non-clip_video kind at 8388608
-- (0003:55). Any edit_window proxy larger than 8 MiB uploaded to R2 successfully
-- and then failed /complete with 'COMPLETE: actual size exceeds kind cap',
-- orphaning the staging object until cleanup reaped it.
-- Redefine cloud_complete_upload with the per-kind caps the reserve contract
-- enforces: clip_video -> 512 MiB, edit_window -> 512 MiB, thumbnail -> 8 MiB.
-- Everything else is byte-identical to the 0003 definition except the hardened
-- search_path (pg_catalog first, per the grant-hygiene contract): signature
-- (uuid, bigint, text) -> media_objects, language plpgsql, SECURITY DEFINER,
-- set search_path = public, idempotent-completion replay guard, reserved-
-- expectation check, atomic global-cap re-check for the promotion footprint
-- (advisory lock 918273645), and staging_key retention until
-- cloud_confirm_staging_removed. The Worker calls this RPC with the unchanged
-- parameter set (cloud-worker/src/index.ts /complete ->
-- rpc cloud_complete_upload {p_reservation_id, p_actual_bytes, p_final_key}).
-- CREATE OR REPLACE preserves the 0002/0003 grants (service_role only), so no
-- revoke/grant statements are needed here.
-- Rollback: restore the 0003 function body via create or replace; no data changes.

create or replace function public.cloud_complete_upload(
  p_reservation_id uuid, p_actual_bytes bigint, p_final_key text)
returns media_objects language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  v_res upload_reservations%rowtype; v_obj media_objects; v_usage record; v_quota bigint;
begin
  select * into v_res from upload_reservations where id = p_reservation_id for update;
  if not found then raise exception 'COMPLETE: unknown reservation'; end if;
  select * into v_obj from media_objects where id = v_res.media_object_id for update;
  if v_res.status = 'completed' then
    if v_obj.final_key is distinct from p_final_key then
      raise exception 'COMPLETE: idempotent replay with different final key';
    end if;
    return v_obj;
  end if;
  if v_res.status <> 'pending' then raise exception 'COMPLETE: reservation not pending'; end if;
  if p_actual_bytes <= 0 or p_actual_bytes > v_res.expected_bytes then
    raise exception 'COMPLETE: actual size exceeds reserved expectation';
  end if;
  -- Kind caps mirror the reserve contract (00011/00018/00023): edit_window
  -- reserves up to 512 MiB, so completion must not cap it at the thumbnail limit.
  if p_actual_bytes > (case v_obj.kind
    when 'clip_video' then 536870912
    when 'edit_window' then 536870912
    else 8388608 end) then
    raise exception 'COMPLETE: actual size exceeds kind cap';
  end if;
  -- Atomic global-cap re-check for the promotion footprint: this reservation's
  -- reserved slot is released, the final object AND its still-present staging
  -- duplicate are both counted. Serialized by the same advisory lock as reserve.
  perform pg_advisory_xact_lock(918273645);
  select * into v_usage from cloud_quota_usage(v_obj.user_id);
  if v_usage.global_stored_bytes + v_usage.global_reserved_bytes - v_res.expected_bytes + 2 * p_actual_bytes
     > 10000000000 then
    raise exception 'COMPLETE: global cap exceeded';
  end if;
  -- staging_key is KEPT: the duplicate keeps consuming budget until
  -- cloud_confirm_staging_removed runs after the Worker deleted the staging object.
  update media_objects set status = 'active', final_key = p_final_key,
    byte_count = p_actual_bytes where id = v_obj.id returning * into v_obj;
  update upload_reservations set status = 'completed' where id = v_res.id;
  return v_obj;
end;
$$;

-- cloud_find_covering_window (0009) is the canonical reuse lookup for future
-- callers, but it ranked purely by created_at: since re-staging a clip always
-- registers a NEWEST row first, a window whose transcript never landed
-- (Worker transcript_status='missing', no window_transcripts row) could shadow
-- an older window that has words. Prefer windows with stored transcripts,
-- then newest. The Worker's own oldest-first sibling match in /sync/pc/windows
-- is registration idempotency and is deliberately unchanged.
-- CREATE OR REPLACE preserves the 0009 grants (service_role only); no
-- revoke/grant statements are needed here.
-- Rollback: restore the 0009 function body via create or replace; no data changes.
create or replace function public.cloud_find_covering_window(
  p_user_id uuid, p_local_clip_id text, p_start double precision, p_end double precision)
returns table (window_id uuid, window_start double precision, window_end double precision, media_object_id uuid)
language sql security definer set search_path = pg_catalog, public as $$
  select w.id, w.window_start, w.window_end, w.media_object_id
  from edit_windows w
  left join window_transcripts t on t.window_id = w.id
  where w.user_id = p_user_id and w.local_clip_id = p_local_clip_id
    and w.status = 'ready' and w.window_start <= p_start and w.window_end >= p_end
  order by (t.window_id is not null) desc, w.created_at desc
  limit 1;
$$;

-- Re-assert the service-role-only contract explicitly so static grant checks
-- see it (CREATE OR REPLACE preserves the 0003/0009 grants; these are no-ops).
revoke all on function public.cloud_complete_upload(uuid, bigint, text) from public, anon, authenticated;
revoke all on function public.cloud_find_covering_window(uuid, text, double precision, double precision) from public, anon, authenticated;
grant execute on function public.cloud_complete_upload(uuid, bigint, text) to service_role;
grant execute on function public.cloud_find_covering_window(uuid, text, double precision, double precision) to service_role;
