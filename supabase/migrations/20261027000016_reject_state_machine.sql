-- 0016: durable offline-reject state machine (2026-10-27 remediation).
-- Fixes four gaps in the v1.4.0-alpha command queue:
--   A. A tombstone must survive command acknowledgement (clip cannot reappear
--      after the PC reports done) -> state lives on clips_cloud, not only on
--      the live-command filter.
--   B. Undo while a reject is already CLAIMED must enqueue a durable restore
--      command ordered after it -> the per-clip live index becomes per-kind.
--   C. The 24h grace must be measured from the original user REQUEST, not from
--      whenever the PC re-connects -> purge_after stamped at enqueue.
--   D. Cloud + R2 rendered assets purge after grace via an owned, idempotent
--      worker path -> reject state + timestamps live durably on the row.
alter table public.clips_cloud
  add column if not exists reject_requested_at timestamptz;
alter table public.clips_cloud
  add column if not exists reject_purge_after timestamptz;

-- Versioned cloud caption edits (conflict detection, no silent last-write-wins).
alter table public.clips_cloud
  add column if not exists caption_revision bigint not null default 0;
alter table public.clips_cloud
  add column if not exists caption_updated_at timestamptz;

-- One LIVE command per (user, clip, kind): a claimed reject and a pending
-- restore can co-exist while ordering is guaranteed by created_at.
drop index if exists uq_clip_commands_live;
create unique index if not exists uq_clip_commands_live_kind
  on public.clip_commands (user_id, local_clip_id, kind)
  where status in ('pending','claimed');

-- ----------------------------------------------------------------- enqueue --
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
    -- A queued restore loses to a later reject (undo-of-undo ordering).
    update clip_commands pc
      set status = 'voided', done_at = now(), updated_at = now()
      where pc.user_id = p_user_id and pc.local_clip_id = p_local_clip_id
        and pc.kind = 'restore' and pc.status = 'pending';

    if v_status = 'rejected' and v_row_purge_after is not null then
      -- Idempotent: already tombstoned. Report the live/past command state.
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

    -- Fresh tombstone, stamped at REQUEST time (not PC-apply time): this is
    -- what makes the 24h grace independent of how long the PC stays offline.
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
      returning id into v_new_id;
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

  -- Reject claimed (or done): enqueue a durable, ordered restore. Idempotent:
  -- return an already-live restore command rather than duplicating it.
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
    returning id into v_new_id;
  return query select v_new_id, 'pending'::text, v_row_purge_after;
end;
$$;

-- -------------------------------------------------------------------- ack --
create or replace function public.cloud_ack_clip_command(
  p_user_id uuid, p_command_id uuid, p_result text, p_error text default null)
returns boolean
language plpgsql security definer set search_path = public as $$
declare
  v_ok boolean;
  v_kind text;
  v_local text;
begin
  if p_result not in ('done','voided','failed') then
    raise exception 'unsupported ack result';
  end if;
  select cmd.kind, cmd.local_clip_id into v_kind, v_local
    from clip_commands cmd
    where cmd.id = p_command_id and cmd.user_id = p_user_id
      and (cmd.status = 'claimed' or (cmd.status = 'voided' and p_result = 'voided'))
    for update;
  if v_kind is null then
    return false; -- unknown command, wrong owner, or lease never claimed here
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
    set status = 'done', done_at = now(), lease_until = null, updated_at = now()
    where cmd.id = p_command_id
    returning true into v_ok;
  v_ok := coalesce(v_ok, false);

  -- Tombstone bookkeeping at completion time.
  if v_ok and p_result = 'done' and v_kind = 'restore' then
    update clips_cloud
      set status = 'ready_review',
          reject_requested_at = null,
          reject_purge_after = null
      where clips_cloud.user_id = p_user_id
        and clips_cloud.local_clip_id = v_local
        and clips_cloud.status = 'rejected';
    delete from rejections r
      where r.user_id = p_user_id
        and r.clip_id in (select c.id from clips_cloud c
                          where c.user_id = p_user_id and c.local_clip_id = v_local);
    update clip_commands cmd2
      set status = 'voided', done_at = now(), updated_at = now()
      where cmd2.user_id = p_user_id and cmd2.local_clip_id = v_local
        and cmd2.kind = 'reject' and cmd2.status = 'pending';
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

-- --------------------------------------- versioned cloud captions (0016) --
-- Monotonic revision guard: a save that was prepared against an older
-- revision is refused as a conflict instead of silently clobbering a newer
-- edit. Ownership: enforced by user_id + local_clip_id lookup.
drop function if exists public.cloud_put_clip_captions(uuid, text, jsonb);
create or replace function public.cloud_put_clip_captions(
  p_user_id uuid, p_local_clip_id text, p_caption jsonb, p_base_revision bigint default null)
returns table (revision bigint, conflict boolean)
language plpgsql security definer set search_path = public as $$
declare
  v_clip uuid;
  v_current_revision bigint;
  v_size bigint;
begin
  if p_caption is null or jsonb_typeof(p_caption) <> 'object' then
    raise exception 'caption track required';
  end if;
  v_size := length(p_caption::text);
  if v_size > 262144 then
    raise exception 'caption track too large (%)', v_size;
  end if;

  select c.id, c.caption_revision into v_clip, v_current_revision
    from clips_cloud c
    where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
    for update;
  if v_clip is null then
    raise exception 'clip not found';
  end if;

  if p_base_revision is not null and p_base_revision <> v_current_revision then
    return query select v_current_revision, true;
    return;
  end if;

  update clips_cloud
    set caption_data = p_caption,
        caption_revision = v_current_revision + 1,
        caption_updated_at = now()
    where id = v_clip;
  return query select v_current_revision + 1, false;
end;
$$;
revoke all on function public.cloud_put_clip_captions(uuid, text, jsonb, bigint) from public, authenticated, anon;
grant execute on function public.cloud_put_clip_captions(uuid, text, jsonb, bigint) to service_role;
