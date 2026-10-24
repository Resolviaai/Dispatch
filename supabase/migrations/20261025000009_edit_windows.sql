-- 0009: bounded hybrid clip editing (owner brief 2026-10-25).
-- Temporary edit windows: 720p30 proxy copies of a bounded source window
-- (max 300 s span), staged on demand for remote review. Distinct from final
-- clips; never a full source. Quota: temporary windows count as pending/active
-- media_objects (kind 'edit_window') under the SAME per-user/global byte caps.

alter table public.media_objects drop constraint if exists media_objects_kind_check;
alter table public.media_objects add constraint media_objects_kind_check
  check (kind in ('clip_video','thumbnail','edit_window'));

create table if not exists public.edit_windows (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  local_clip_id text not null,
  -- source-relative window bounds (the ONLY time authority for the editor)
  window_start double precision not null,
  window_end double precision not null,
  status text not null default 'staging'
    check (status in ('staging','ready','failed','expired')),
  media_object_id uuid references public.media_objects(id) on delete set null,
  staged_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists idx_edit_windows_clip on public.edit_windows (local_clip_id, created_at desc);
alter table public.edit_windows enable row level security;
-- no client policies: service_role only (PC/Worker write, app reads via signed URLs)

create table if not exists public.window_transcripts (
  window_id uuid primary key references public.edit_windows(id) on delete cascade,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  words jsonb not null,          -- [{w,start,end}] SOURCE-relative seconds
  transcribed_at timestamptz not null default now()
);
alter table public.window_transcripts enable row level security;

-- readiness flag used by the app to switch atomically once upload+validation pass
create or replace function public.cloud_mark_window_ready(p_window_id uuid, p_media_object_id uuid)
returns void language sql security definer set search_path = public as $$
  update edit_windows set status = 'ready', media_object_id = p_media_object_id, staged_at = now()
    where id = p_window_id and status = 'staging';
$$;

create or replace function public.cloud_mark_window_failed(p_window_id uuid, p_error text)
returns void language sql security definer set search_path = public as $$
  update edit_windows set status = 'failed' where id = p_window_id and status = 'staging';
$$;

create or replace function public.cloud_put_window_transcript(
  p_window_id uuid, p_user_id uuid, p_words jsonb)
returns void language sql security definer set search_path = public as $$
  insert into window_transcripts (window_id, user_id, words) values (p_window_id, p_user_id, p_words)
  on conflict (window_id) do update set words = excluded.words, transcribed_at = now();
$$;

create or replace function public.cloud_get_window_transcript(p_window_id uuid)
returns jsonb language sql security definer set search_path = public as $$
  select words from window_transcripts where window_id = p_window_id;
$$;

-- window reuse: newest ready window for this clip whose span covers the wanted range
create or replace function public.cloud_find_covering_window(
  p_user_id uuid, p_local_clip_id text, p_start double precision, p_end double precision)
returns table (window_id uuid, window_start double precision, window_end double precision, media_object_id uuid)
language sql security definer set search_path = public as $$
  select w.id, w.window_start, w.window_end, w.media_object_id
  from edit_windows w
  where w.user_id = p_user_id and w.local_clip_id = p_local_clip_id
    and w.status = 'ready' and w.window_start <= p_start and w.window_end >= p_end
  order by w.created_at desc limit 1;
$$;

-- (client access is denied by the missing grants + RLS; explicit revokes below)
revoke all on public.edit_windows from authenticated, anon;
revoke all on public.window_transcripts from authenticated, anon;
revoke all on function public.cloud_mark_window_ready(uuid, uuid) from public, authenticated, anon;
revoke all on function public.cloud_mark_window_failed(uuid, text) from public, authenticated, anon;
revoke all on function public.cloud_put_window_transcript(uuid, uuid, jsonb) from public, authenticated, anon;
revoke all on function public.cloud_get_window_transcript(uuid) from public, authenticated, anon;
revoke all on function public.cloud_find_covering_window(uuid, text, double precision, double precision) from public, authenticated, anon;
grant execute on function public.cloud_mark_window_ready(uuid, uuid) to service_role;
grant execute on function public.cloud_mark_window_failed(uuid, text) to service_role;
grant execute on function public.cloud_put_window_transcript(uuid, uuid, jsonb) to service_role;
grant execute on function public.cloud_get_window_transcript(uuid) to service_role;
grant execute on function public.cloud_find_covering_window(uuid, text, double precision, double precision) to service_role;

-- Dynamic per-user quota (owner brief): admin reallocates capacity in integer
-- bytes; allocations must fit the global budget alongside other users' quotas.
create or replace function public.cloud_set_user_quota(p_target_user uuid, p_quota_bytes bigint)
returns void language plpgsql security definer set search_path = public as $$
  declare v_other bigint;
  begin
    if p_quota_bytes <= 0 or p_quota_bytes > 10000000000 then
      raise exception 'quota out of bounds (0, 10000000000]';
    end if;
    select coalesce(sum(storage_quota_bytes), 0) into v_other
      from profiles where user_id <> p_target_user;
    if v_other + p_quota_bytes > 10000000000 then
      raise exception 'allocation exceeds the 10000000000-byte global budget';
    end if;
    update profiles set storage_quota_bytes = p_quota_bytes where user_id = p_target_user;
  end;
$$;
revoke all on function public.cloud_set_user_quota(uuid, bigint) from public, authenticated, anon;
grant execute on function public.cloud_set_user_quota(uuid, bigint) to service_role;
