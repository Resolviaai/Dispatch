-- 0017: reject/restore ordering + truthful command deliveries (2026-10-27 audit).
-- Fixes four races in the 0016 offline-reject state machine:
--   A. A NEW Reject must supersede an already-CLAIMED restore. Previously the
--      claimed restore was not cancelled (only pending ones) and the enqueue
--      took the idempotent early-return, so the later restore acknowledgement
--      flipped the cloud row back to ready_review and un-rejected a clip the
--      user had (again) rejected.
--      Fix: a new Reject re-stamps the tombstone (reject_requested_at + a NEW
--      reject_purge_after) and enqueues a newer reject command; the restore
--      acknowledgement only un-rejects while the tombstone it was enqueued
--      against is still current (purge_after equality = generation check).
--   B. A 'voided' acknowledgement of a CLAIMED command must mark the command
--      voided, not 'done' (previously both results wrote status='done').
--   C. Commands claimed by a PC that does not own the clip (unpaired machine,
--      clip absent locally) are acked 'unknown' and are REDLIVERED to the PC
--      that holds the clip instead of being swallowed as done. Terminal after
--      8 attempts so truly-unreachable clips stop looping.
--   D. Restore enqueues capture the tombstone generation (payload.purge_after)
--      the command was authorised against; idempotent duplicate enqueues no
--      longer throw on the partial live-command unique index.
drop function if exists public.cloud_enqueue_clip_command(uuid, text, text, jsonb);
create or replace function public.cloud_enqueue_clip_command(
  p_user_id uuid, p_local_clip_id text, p_kind text, p_payload jsonb)
returns table (command_id uuid, state text, purge_after timestamptz)
language plpgsql security definer set search_path = public as $$
declare
  v_clip uuid;
  v_status text;
  v_row_purge_after timestamptz;
  v_existing uuid;
  v_existing_status text;
  v_new_id uuid;
  v_live_restore boolean;
  v_grace constant interval := interval '24 hours';
begin
  if p_kind not in ('reject','restore') then
    raise exception 'unsupported command kind';
  end if;
  if p_local_clip_id is null or length(p_local_clip_id) = 0 or length(p_local_clip_id) > 128 then
    raise exception 'invalid clip id';
  end if;

  select c.id, c.status, c.reject_purge_after into v_clip, v_status, v_row_purge_after
    from clips_cloud c
    where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
    for update;
  if v_clip is null then
    raise exception 'clip not found';
  end if;

  if p_kind = 'reject' then
    -- A queued (pending) restore loses to a later reject (undo-of-undo order).
    update clip_commands pc
      set status = 'voided', done_at = now(), updated_at = now()
      where pc.user_id = p_user_id and pc.local_clip_id = p_local_clip_id
        and pc.kind = 'restore' and pc.status = 'pending';

    select exists(
      select 1 from clip_commands rc
        where rc.user_id = p_user_id and rc.local_clip_id = p_local_clip_id
          and rc.kind = 'restore' and rc.status = 'claimed'
    ) into v_live_restore;

    if v_status = 'rejected' and v_row_purge_after is not null and not v_live_restore then
      -- Idempotent: already tombstoned with no restore in flight. Report the
      -- live/past command state.
      select cmd.id, cmd.status into v_existing, v_existing_status
        from clip_commands cmd
        where cmd.user_id = p_user_id and cmd.local_clip_id = p_local_clip_id
          and cmd.kind = 'reject' and cmd.status in ('pending','claimed','done')
        order by cmd.created_at desc limit 1;
      if v_existing is not null then
        return query select v_existing, v_existing_status, v_row_purge_after;
      else
        return query select null::uuid, 'rejected'::text, v_row_purge_after;
      end if;
      return;
    end if;

    -- Fresh tombstone OR supersede of an in-flight restore: BOTH re-stamp the
    -- request-time ordering. A newer reject_purge_after invalidates any older
    -- restore ack (see cloud_ack_clip_command), so the user's newest intent
    -- (reject) always wins.
    v_row_purge_after := now() + v_grace;
    update clips_cloud
      set status = 'rejected',
          reject_requested_at = now(),
          reject_purge_after = v_row_purge_after
      where id = v_clip;

    -- Cloud-side learning record for the rejected clip (reversed explicitly
    -- by a restore inside grace; deletion is handled in ack/restore paths).
    if not exists (
      select 1 from rejections r where r.clip_id = v_clip and r.user_id = p_user_id
    ) then
      insert into public.rejections (clip_id, user_id, reason)
      values (v_clip, p_user_id,
        coalesce(p_payload->>'reason', 'Rejected from mobile review'));
    end if;

    insert into clip_commands (user_id, local_clip_id, kind, payload)
      values (p_user_id, p_local_clip_id, 'reject',
        jsonb_build_object(
          'requested_at', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
          'purge_after', to_char(v_row_purge_after, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
          'reason', coalesce(p_payload->>'reason', 'Rejected from mobile review')))
      on conflict (user_id, local_clip_id, kind) where status in ('pending','claimed') do nothing
      returning id into v_new_id;

    if v_new_id is null then
      -- A live reject already occupies the (user, clip, kind) slot. Its local
      -- apply converges to the same refreshed tombstone; report it truthfully.
      select cmd.id, cmd.status into v_existing, v_existing_status
        from clip_commands cmd
        where cmd.user_id = p_user_id and cmd.local_clip_id = p_local_clip_id
          and cmd.kind = 'reject' and cmd.status in ('pending','claimed')
        order by cmd.created_at desc limit 1;
      return query select v_existing, v_existing_status, v_row_purge_after;
      return;
    end if;
    return query select v_new_id, 'pending'::text, v_row_purge_after;
    return;
  end if;

  -- p_kind = 'restore' ------------------------------------------------------
  if v_status is distinct from 'rejected' or v_row_purge_after is null then
    -- Nothing to restore. Void any pending reject so it cannot fire later.
    update clip_commands pc
      set status = 'voided', done_at = now(), updated_at = now()
      where pc.user_id = p_user_id and pc.local_clip_id = p_local_clip_id
        and pc.kind = 'reject' and pc.status = 'pending';
    return query select null::uuid, 'not_rejected'::text, null::timestamptz;
    return;
  end if;

  if v_row_purge_after <= now() then
    -- Grace measured from the original request has lapsed: the PC will (or
    -- did) purge media; do not promise restore.
    return query select null::uuid, 'expired'::text, v_row_purge_after;
    return;
  end if;

  -- Undo BEFORE the PC claimed it: void the reject, restore instantly (the
  -- clip becomes visible again without waiting for the PC) and reverse the
  -- cloud learning record explicitly.
  if exists (
    select 1 from clip_commands pc
      where pc.user_id = p_user_id and pc.local_clip_id = p_local_clip_id
        and pc.kind = 'reject' and pc.status = 'pending'
  ) then
    update clip_commands pc
      set status = 'voided', done_at = now(), updated_at = now()
      where pc.user_id = p_user_id and pc.local_clip_id = p_local_clip_id
        and pc.kind = 'reject' and pc.status = 'pending';
    update clips_cloud
      set status = 'ready_review',
          reject_requested_at = null,
          reject_purge_after = null
      where id = v_clip;
    delete from rejections r where r.clip_id = v_clip and r.user_id = p_user_id;
    return query select null::uuid, 'restored'::text, null::timestamptz;
    return;
  end if;

  -- Reject claimed (or done): enqueue a durable, ordered restore capturing the
  -- tombstone generation (purge_after) the ack will be validated against.
  if exists (
    select 1 from clip_commands pc
      where pc.user_id = p_user_id and pc.local_clip_id = p_local_clip_id
        and pc.kind = 'restore' and pc.status in ('pending','claimed')
  ) then
    select cmd.id, cmd.status into v_existing, v_existing_status
      from clip_commands cmd
      where cmd.user_id = p_user_id and cmd.local_clip_id = p_local_clip_id
        and cmd.kind = 'restore' and cmd.status in ('pending','claimed')
      order by cmd.created_at desc limit 1;
    return query select v_existing, v_existing_status, v_row_purge_after;
    return;
  end if;

  insert into clip_commands (user_id, local_clip_id, kind, payload)
    values (p_user_id, p_local_clip_id, 'restore',
      jsonb_build_object(
        'requested_at', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'purge_after', to_char(v_row_purge_after, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')))
    on conflict (user_id, local_clip_id, kind) where status in ('pending','claimed') do nothing
    returning id into v_new_id;
  if v_new_id is null then
    select cmd.id, cmd.status into v_existing, v_existing_status
      from clip_commands cmd
      where cmd.user_id = p_user_id and cmd.local_clip_id = p_local_clip_id
        and cmd.kind = 'restore' and cmd.status in ('pending','claimed')
      order by cmd.created_at desc limit 1;
    return query select v_existing, v_existing_status, v_row_purge_after;
    return;
  end if;
  return query select v_new_id, 'pending'::text, v_row_purge_after;
end;
$$;

-- ---------------------------------------------------------------- ack -------------
drop function if exists public.cloud_ack_clip_command(uuid, uuid, text, text);
create or replace function public.cloud_ack_clip_command(
  p_user_id uuid, p_command_id uuid, p_result text, p_error text default null)
returns boolean
language plpgsql security definer set search_path = public as $$
declare
  v_ok boolean;
  v_kind text;
  v_local text;
  v_cmd jsonb;
  v_attempts int;
begin
  if p_result not in ('done','voided','failed','unknown') then
    raise exception 'unsupported ack result';
  end if;
  select cmd.kind, cmd.local_clip_id, cmd.payload into v_kind, v_local, v_cmd
    from clip_commands cmd
    where cmd.id = p_command_id and cmd.user_id = p_user_id
      and (cmd.status = 'claimed' or (cmd.status = 'voided' and p_result = 'voided'))
    for update;
  if v_kind is null then
    return false; -- unknown command, wrong owner, or lease never claimed here
  end if;

  -- UNKNOWN (audit 1A): this PC holds no clip for this command. Release the
  -- lease and re-deliver to the machine that does instead of swallowing the
  -- command as done/voided. Bounded: after 8 claim attempts the command is
  -- voided so genuinely orphaned clips do not loop forever.
  if p_result = 'unknown' then
    select cmd.attempts into v_attempts from clip_commands cmd where cmd.id = p_command_id;
    if v_attempts >= 8 then
      update clip_commands cmd
        set status = 'voided',
            last_error = coalesce(p_error, 'unreachable: no owning PC applied this command'),
            done_at = now(),
            lease_until = null,
            updated_at = now()
        where cmd.id = p_command_id
        returning true into v_ok;
    else
      update clip_commands cmd
        set status = 'pending',
            last_error = coalesce(p_error, 'not present locally; awaiting owning PC'),
            lease_until = null,
            updated_at = now()
        where cmd.id = p_command_id
        returning true into v_ok;
    end if;
    return coalesce(v_ok, false);
  end if;

  if p_result = 'failed' then
    update clip_commands cmd
      set status = case when cmd.attempts >= 8 then 'failed' else 'pending' end,
          last_error = coalesce(p_error, 'unknown error'),
          lease_until = null,
          updated_at = now()
      where cmd.id = p_command_id
      returning true into v_ok;
    return coalesce(v_ok, false);
  end if;

  update clip_commands cmd
    set status = case when p_result = 'done' then 'done' else 'voided' end,
        done_at = now(),
        lease_until = null,
        updated_at = now()
    where cmd.id = p_command_id
    returning true into v_ok;
  v_ok := coalesce(v_ok, false);

  -- Tombstone bookkeeping at completion time.
  if v_ok and p_result = 'done' and v_kind = 'restore' then
    if v_cmd->>'purge_after' is not null then
      -- Supersede guard (audit §4): only un-reject while the tombstone this
      -- restore was enqueued against is still the CURRENT one. A newer Reject
      -- re-stamps reject_purge_after; a late restore ack must not overwrite
      -- the user's newest intent (nor its learning record).
      update clips_cloud
        set status = 'ready_review',
            reject_requested_at = null,
            reject_purge_after = null
        where clips_cloud.user_id = p_user_id
          and clips_cloud.local_clip_id = v_local
          and clips_cloud.status = 'rejected'
          and clips_cloud.reject_purge_after = (v_cmd->>'purge_after')::timestamptz;
    else
      -- Pre-0017 restore command without a captured generation: legacy
      -- behavior (flip any current tombstone).
      update clips_cloud
        set status = 'ready_review',
            reject_requested_at = null,
            reject_purge_after = null
        where clips_cloud.user_id = p_user_id
          and clips_cloud.local_clip_id = v_local
          and clips_cloud.status = 'rejected';
    end if;
    delete from rejections r
      where r.user_id = p_user_id
        and r.clip_id in (select c.id from clips_cloud c
                          where c.user_id = p_user_id and c.local_clip_id = v_local
                            and c.status = 'ready_review');
  end if;
  -- A completed reject keeps the tombstone on clips_cloud: the clip must NOT
  -- reappear merely because the live command is gone.

  return v_ok;
end;
$$;

revoke all on function public.cloud_enqueue_clip_command(uuid, text, text, jsonb) from public, authenticated, anon;
revoke all on function public.cloud_ack_clip_command(uuid, uuid, text, text) from public, authenticated, anon;
grant execute on function public.cloud_enqueue_clip_command(uuid, text, text, jsonb) to service_role;
grant execute on function public.cloud_ack_clip_command(uuid, uuid, text, text) to service_role;
