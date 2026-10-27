-- 0015: durable offline clip commands (Reject with 24h grace + real restore).
-- The phone can reject/restore a clip while its PC is offline: commands are
-- queued here, the PC claims them with a lease, applies them locally, and
-- acknowledges. Reject keeps the learning record immediately and only removes
-- media AFTER a 24h grace so a late restore is still possible.
create table if not exists public.clip_commands (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  local_clip_id text not null,
  kind text not null check (kind in ('reject','restore')),
  payload jsonb,
  status text not null default 'pending'
    check (status in ('pending','claimed','voided','done','failed')),
  attempts int not null default 0,
  last_error text,
  lease_until timestamptz,
  done_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_clip_commands_user_status
  on public.clip_commands (user_id, status, created_at);
-- One live command per user+clip: enqueue is idempotent and replaces nothing.
create unique index if not exists uq_clip_commands_live
  on public.clip_commands (user_id, local_clip_id)
  where status in ('pending','claimed');

-- Enqueue a command. Auto-voids a pending sibling-clip REJECT when the user
-- requests a RESTORE (undo within grace); leaves a claimed one untouched
-- (ownership passed to the PC already).
create or replace function public.cloud_enqueue_clip_command(
  p_user_id uuid, p_local_clip_id text, p_kind text, p_payload jsonb)
returns table (command_id uuid, state text)
language plpgsql security definer set search_path = public as $$
declare
  v_existing uuid;
  v_existing_status text;
  v_existing_kind text;
  v_new_id uuid;
begin
  if p_kind not in ('reject','restore') then
    raise exception 'unsupported command kind';
  end if;
  if p_local_clip_id is null or length(p_local_clip_id) = 0 or length(p_local_clip_id) > 128 then
    raise exception 'invalid clip id';
  end if;
  select c.id, c.status, c.kind into v_existing, v_existing_status, v_existing_kind
    from clip_commands c
    where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
      and c.status in ('pending','claimed')
    for update;
  if v_existing is not null then
    if v_existing_status = 'pending' and v_existing_kind = 'reject' and p_kind = 'restore' then
      update clip_commands set status = 'voided', done_at = now(), updated_at = now()
        where id = v_existing;
      return query select v_existing, 'voided'::text;
      return;
    end if;
    return query select v_existing, v_existing_status;
    return;
  end if;
  insert into clip_commands (user_id, local_clip_id, kind, payload)
    values (p_user_id, p_local_clip_id, p_kind, p_payload)
    returning id into v_new_id;
  return query select v_new_id, 'pending'::text;
end;
$$;

-- Claim a lease-scoped batch: pending rows or rows whose lease expired.
create or replace function public.cloud_claim_clip_commands(
  p_user_id uuid, p_limit int, p_lease_seconds int)
returns table (id uuid, local_clip_id text, kind text, payload jsonb, attempts int)
language sql security definer set search_path = public as $$
  with claimable as (
    select c.id from clip_commands c
    where c.user_id = p_user_id
      and (c.status = 'pending'
           or (c.status = 'claimed' and c.lease_until is not null and c.lease_until < now()))
    order by c.created_at
    limit greatest(1, least(coalesce(p_limit, 10), 50))
    for update skip locked
  )
  update clip_commands c
    set status = 'claimed',
        attempts = c.attempts + 1,
        lease_until = now() + make_interval(secs => greatest(30, least(coalesce(p_lease_seconds, 600), 3600))),
        updated_at = now()
    from claimable k
    where c.id = k.id
  returning c.id, c.local_clip_id, c.kind, c.payload, c.attempts;
$$;

-- Acknowledge: done / voided / failed (retry-able). Expired leases may still ack.
create or replace function public.cloud_ack_clip_command(
  p_user_id uuid, p_command_id uuid, p_result text, p_error text default null)
returns boolean
language plpgsql security definer set search_path = public as $$
declare v_ok boolean;
begin
  if p_result not in ('done','voided','failed') then
    raise exception 'unsupported ack result';
  end if;
  update clip_commands c set
    status = case p_result
      when 'done' then 'done'
      when 'voided' then 'voided'
      else case when c.attempts >= 8 then 'failed' else 'pending' end
    end,
    done_at = case when p_result in ('done','voided') then now() else c.done_at end,
    last_error = case when p_result = 'failed' then coalesce(p_error, 'unknown error') else c.last_error end,
    lease_until = null,
    updated_at = now()
  where c.id = p_command_id and c.user_id = p_user_id
    and (c.status = 'claimed' or (c.status = 'voided' and p_result = 'voided'))
    returning true into v_ok;
  return coalesce(v_ok, false);
end;
$$;

-- Live command lookup used by /clips to keep tombstoned clips out of the list.
create or replace function public.cloud_list_live_clip_commands(p_user_id uuid)
returns table (local_clip_id text, kind text, created_at timestamptz)
language sql security definer set search_path = public as $$
  select local_clip_id, kind, created_at from clip_commands
    where user_id = p_user_id and status in ('pending','claimed')
    order by created_at;
$$;

alter table public.clip_commands enable row level security;
-- no client policies: service_role only (PC claim/ack/apply, phone enqueues via worker)

revoke all on public.clip_commands from authenticated, anon;
revoke all on function public.cloud_enqueue_clip_command(uuid, text, text, jsonb) from public, authenticated, anon;
revoke all on function public.cloud_claim_clip_commands(uuid, int, int) from public, authenticated, anon;
revoke all on function public.cloud_ack_clip_command(uuid, uuid, text, text) from public, authenticated, anon;
revoke all on function public.cloud_list_live_clip_commands(uuid) from public, authenticated, anon;
grant execute on function public.cloud_enqueue_clip_command(uuid, text, text, jsonb) to service_role;
grant execute on function public.cloud_claim_clip_commands(uuid, int, int) to service_role;
grant execute on function public.cloud_ack_clip_command(uuid, uuid, text, text) to service_role;
grant execute on function public.cloud_list_live_clip_commands(uuid) to service_role;
