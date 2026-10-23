-- 0003: global 10,000,000,000-byte cap hardening (R2 has NO native bucket cap —
-- developers.cloudflare.com/r2/platform/limits: "Data storage per bucket: Unlimited").
-- This is an APPLICATION-LEVEL cap over Dispatch-tracked objects; it cannot bind
-- bytes written outside Dispatch's reserve/complete contract.
-- Changes vs 0002:
--   1. Usage counts the PROMOTION DUPLICATE: an active object whose staging copy is
--      not yet confirmed deleted counts byte_count TWICE (final + staging physically
--      coexist), so concurrent promotions cannot overspend the budget.
--   2. cloud_complete_upload keeps staging_key until cloud_confirm_staging_removed,
--      and atomically re-checks the global cap for the projected post-promotion
--      footprint (reserved slot released, final + duplicate counted).
--   3. cloud_confirm_staging_removed / cloud_list_promotion_leftovers let the Worker
--      release the duplicate safely and reap crash leftovers.
-- Rollback: restore 0002 function bodies via create or replace; no data changes.

create or replace function public.cloud_quota_usage(p_user_id uuid)
returns table (user_stored_bytes bigint, user_reserved_bytes bigint,
               user_outstanding int, global_stored_bytes bigint, global_reserved_bytes bigint)
language sql security definer set search_path = public as $$
  select
    coalesce(sum(case when mo.user_id = p_user_id and mo.status in ('active','deletion_pending') then mo.byte_count end), 0)
      + coalesce(sum(case when mo.user_id = p_user_id and mo.status = 'active' and mo.staging_key is not null then mo.byte_count end), 0),
    (select coalesce(sum(ur.expected_bytes), 0) from upload_reservations ur
       join media_objects m2 on m2.id = ur.media_object_id
       where ur.user_id = p_user_id and ur.status = 'pending' and m2.status = 'pending'),
    (select count(*) from upload_reservations ur
       where ur.user_id = p_user_id and ur.status = 'pending' and ur.expires_at > now()),
    coalesce(sum(case when mo.status in ('active','deletion_pending') then mo.byte_count end), 0)
      + coalesce(sum(case when mo.status = 'active' and mo.staging_key is not null then mo.byte_count end), 0),
    (select coalesce(sum(ur.expected_bytes), 0) from upload_reservations ur
       join media_objects m3 on m3.id = ur.media_object_id
       where ur.status = 'pending' and m3.status = 'pending')
  from media_objects mo;
$$;

create or replace function public.cloud_complete_upload(
  p_reservation_id uuid, p_actual_bytes bigint, p_final_key text)
returns media_objects language plpgsql security definer set search_path = public as $$
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
  if p_actual_bytes > (case v_obj.kind when 'clip_video' then 536870912 else 8388608 end) then
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

create or replace function public.cloud_confirm_staging_removed(p_media_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  -- Releases the promotion duplicate from accounting. Called by the Worker only
  -- after the R2 staging delete succeeded (or head confirmed absence).
  update media_objects set staging_key = null
    where id = p_media_id and status = 'active' and staging_key is not null;
end;
$$;

create or replace function public.cloud_list_promotion_leftovers()
returns table (media_object_id uuid, staging_key text)
language sql security definer set search_path = public as $$
  -- Active objects whose staging copy was never confirmed deleted (Worker crash
  -- between copy and confirm). Doubly counted until reaped: conservative.
  select id, staging_key from media_objects
  where status = 'active' and staging_key is not null;
$$;

revoke all on function public.cloud_confirm_staging_removed(uuid) from public, authenticated, anon;
revoke all on function public.cloud_list_promotion_leftovers() from public, authenticated, anon;
grant execute on function public.cloud_confirm_staging_removed(uuid) to service_role;
grant execute on function public.cloud_list_promotion_leftovers() to service_role;
