-- Dispatch cloud schema 0001 (plan doc D4). Supabase Postgres; auth.users = identity.
-- Apply only to an authorized Supabase project (docs/cloud/OWNER_SETUP.md); never to any
-- real project without explicit owner approval. Rollback: drop schema objects in reverse
-- (drop table ... cascade). Never destroys local SQLite.
-- Existing users at apply time: on_auth_user_created creates profiles going forward;
-- backfill any pre-existing users manually if needed.
-- Every later migration MUST end with the least-privilege block pattern used below
-- (revoke ..., then explicit grants); Supabase default privileges are broader.

-- SECURITY MODEL (Phase C audit decisions):
-- * Clients (browser/Android via Supabase JWT) are READ-ONLY reviewers: SELECT grants
--   only, RLS-scoped with `user_id = auth.uid()`; anon sees nothing (no grants, login
--   required per D9). The only client-side write anywhere is profiles.display_name.
-- * All lifecycle writes are service_role (Worker; per D8/D9/D11): review transitions
--   (approve/reject/publish), upload reservations/completions, media promote/delete,
--   publications, rejections, extraction requests. Clients therefore cannot bypass
--   quota reservation, upload completion, review transitions, rejection/deletion,
--   publication state, or extraction ownership by writing directly to tables.
-- * storage_quota_bytes is service-managed: no client grant + role-checked guard
--   trigger (defense in depth against a future accidental re-grant).
-- * ai_suggested_start_time/end_time are immutable in Postgres (D3 parity with local
--   db.py): any UPDATE that alters them fails, for every role.
-- * range_revision is monotonic; changing start_time/end_time requires a revision bump
--   (optimistic-concurrency contract D3).
-- * Owner-guard triggers reject cross-user references (a row may never link to a
--   session/clip/media object owned by another user).
-- * service_role bypasses RLS and is the only privileged client; the service-role key
--   lives in the Cloudflare Worker only - never in PC, browser, Android, tests, or Git.

create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  storage_quota_bytes bigint not null default 1073741824
    check (storage_quota_bytes >= 0 and storage_quota_bytes <= 2147483648),
  created_at timestamptz not null default now()
);

create table public.source_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  youtube_video_id text not null,
  title text,
  duration_seconds numeric,
  created_at timestamptz not null default now(),
  unique (user_id, youtube_video_id)
);

create table public.clips_cloud (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  session_id uuid references public.source_sessions(id) on delete set null,
  local_clip_id text not null,
  title text not null,
  status text not null check (status in ('ready_review','approved','rejected','published')),
  ai_suggested_start_time numeric not null,
  ai_suggested_end_time numeric not null,
  start_time numeric not null,
  end_time numeric not null,
  range_revision integer not null default 0 check (range_revision >= 0),
  render_stale boolean not null default false,
  local_updated_at timestamptz not null default now(),
  cloud_updated_at timestamptz not null default now(),
  unique (user_id, local_clip_id),
  check (ai_suggested_start_time >= 0),
  check (ai_suggested_end_time >= 0),
  check (ai_suggested_end_time > ai_suggested_start_time),
  check (start_time >= 0),
  check (end_time >= 0),
  check (end_time > start_time)
);

create table public.media_objects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  clip_id uuid references public.clips_cloud(id) on delete set null,
  kind text not null check (kind in ('clip_video','thumbnail')),
  status text not null check (status in ('pending','active','deletion_pending','deleted')),
  staging_key text,
  final_key text unique,
  mime_type text not null,
  byte_count bigint not null default 0 check (byte_count >= 0),
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  deleted_at timestamptz,
  -- a staging key must live under the lifecycle-managed staging/ prefix (D5/D7)
  check (staging_key is null or staging_key like 'staging/%'),
  -- an active object is fully materialized: real immutable key and verified size
  check (status <> 'active' or (final_key is not null and byte_count > 0)),
  -- pending objects hold no bytes yet; bytes are recorded at completion (D5) so the
  -- accounting sums below cannot miss them
  check (status <> 'pending' or byte_count = 0),
  check (status <> 'deleted' or deleted_at is not null)
);

create table public.upload_reservations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  media_object_id uuid not null references public.media_objects(id) on delete cascade,
  client_token text not null,
  expected_bytes bigint not null check (expected_bytes > 0),
  status text not null default 'pending' check (status in ('pending','completed','expired','cancelled')),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '30 minutes',
  unique (user_id, client_token)
);

create table public.publications (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid not null references public.clips_cloud(id) on delete cascade,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  platform text not null check (platform in ('youtube')),
  remote_id text,
  remote_url text,
  published_at timestamptz,
  status text not null check (status in ('queued','uploading','published','failed')),
  check (status <> 'published' or published_at is not null)
);

create table public.rejections (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid references public.clips_cloud(id) on delete set null,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  reason text not null,
  created_at timestamptz not null default now()
);

create table public.extraction_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  session_id uuid not null references public.source_sessions(id) on delete cascade,
  status text not null default 'queued' check (status in ('queued','claimed','done','failed')),
  created_at timestamptz not null default now(),
  claimed_at timestamptz,
  check ((status = 'queued' and claimed_at is null) or
         (status <> 'queued' and claimed_at is not null))
);

create index idx_clips_user_status on public.clips_cloud (user_id, status);
create index idx_media_user_status on public.media_objects (user_id, status);
create index idx_media_expires on public.media_objects (expires_at) where status in ('active','deletion_pending');
create index idx_resv_expires on public.upload_reservations (expires_at) where status = 'pending';
create index idx_resv_user_pending on public.upload_reservations (user_id) where status = 'pending';
create index idx_extraction_status on public.extraction_requests (status);

alter table public.profiles enable row level security;
alter table public.source_sessions enable row level security;
alter table public.clips_cloud enable row level security;
alter table public.media_objects enable row level security;
alter table public.upload_reservations enable row level security;
alter table public.publications enable row level security;
alter table public.rejections enable row level security;
alter table public.extraction_requests enable row level security;

-- Clients are read-only reviewers: every policy is scoped to authenticated, and there
-- is deliberately NO insert/update/delete policy on any table except the narrow
-- profiles display_name update below. Missing policy = deny, even if grants regress.
create policy "profiles: own row readable" on public.profiles
  for select to authenticated using (user_id = auth.uid());
create policy "profiles: own display_name updateable" on public.profiles
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "source_sessions: own rows readable" on public.source_sessions
  for select to authenticated using (user_id = auth.uid());
create policy "clips_cloud: own rows readable" on public.clips_cloud
  for select to authenticated using (user_id = auth.uid());
create policy "media_objects: own rows readable" on public.media_objects
  for select to authenticated using (user_id = auth.uid());
create policy "upload_reservations: own rows readable" on public.upload_reservations
  for select to authenticated using (user_id = auth.uid());
create policy "publications: own rows readable" on public.publications
  for select to authenticated using (user_id = auth.uid());
create policy "rejections: own rows readable" on public.rejections
  for select to authenticated using (user_id = auth.uid());
create policy "extraction_requests: own rows readable" on public.extraction_requests
  for select to authenticated using (user_id = auth.uid());

-- Profile rows are created by the trigger on auth.users (GoTrue signup); clients
-- never insert or delete profile rows.
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  insert into public.profiles (user_id, display_name)
  values (
    new.id,
    coalesce(nullif(new.raw_user_meta_data ->> 'display_name', ''), split_part(new.email, '@', 1))
  )
  on conflict (user_id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();

-- Quota is service-managed. The column grant is absent, so this guard only matters if
-- a future migration accidentally re-grants it: client roles (anon/authenticated) may
-- never change storage_quota_bytes, while service_role/admin operations pass.
create function public.profiles_quota_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if new.storage_quota_bytes is distinct from old.storage_quota_bytes
     and coalesce(nullif(current_setting('role', true), ''), current_user)
       in ('anon', 'authenticated') then
    raise exception 'profiles: storage_quota_bytes is managed by the service (service_role) and cannot be changed by clients';
  end if;
  return new;
end;
$$;

create trigger profiles_quota_guard
before update on public.profiles
for each row execute function public.profiles_quota_guard();

-- Owner-guards: a row must never reference a resource owned by a different user.
-- (They run for every role, so they also catch Worker/PC bugs under service_role.)

create function public.clips_cloud_owner_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if new.session_id is not null then
    if not exists (
      select 1 from public.source_sessions s
      where s.id = new.session_id and s.user_id = new.user_id
    ) then
      raise exception 'clips_cloud: session_id must reference a source_sessions row owned by the same user';
    end if;
  end if;
  return new;
end;
$$;

create trigger clips_cloud_owner_guard
before insert or update on public.clips_cloud
for each row execute function public.clips_cloud_owner_guard();

-- Review integrity: immutable AI suggestions, monotonic revisions, trim<->revision
-- coupling (D3). Applies to every role; ordinary review operations cannot touch them.
create function public.clips_cloud_review_integrity() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if new.ai_suggested_start_time is distinct from old.ai_suggested_start_time
     or new.ai_suggested_end_time is distinct from old.ai_suggested_end_time then
    raise exception 'clips_cloud: ai_suggested_start_time/ai_suggested_end_time are immutable once created';
  end if;
  if new.range_revision < old.range_revision then
    raise exception 'clips_cloud: range_revision may not decrease';
  end if;
  if (new.start_time, new.end_time) is distinct from (old.start_time, old.end_time)
     and new.range_revision = old.range_revision then
    raise exception 'clips_cloud: changing start_time/end_time requires a range_revision bump';
  end if;
  return new;
end;
$$;

create trigger clips_cloud_review_integrity
before update on public.clips_cloud
for each row execute function public.clips_cloud_review_integrity();

create function public.media_objects_owner_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if new.clip_id is not null then
    if not exists (
      select 1 from public.clips_cloud c
      where c.id = new.clip_id and c.user_id = new.user_id
    ) then
      raise exception 'media_objects: clip_id must reference a clips_cloud row owned by the same user';
    end if;
  end if;
  return new;
end;
$$;

create trigger media_objects_owner_guard
before insert or update on public.media_objects
for each row execute function public.media_objects_owner_guard();

create function public.upload_reservations_owner_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if not exists (
    select 1 from public.media_objects m
    where m.id = new.media_object_id and m.user_id = new.user_id
  ) then
    raise exception 'upload_reservations: media_object_id must reference a media_objects row owned by the same user';
  end if;
  return new;
end;
$$;

create trigger upload_reservations_owner_guard
before insert or update on public.upload_reservations
for each row execute function public.upload_reservations_owner_guard();

create function public.publications_owner_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if not exists (
    select 1 from public.clips_cloud c
    where c.id = new.clip_id and c.user_id = new.user_id
  ) then
    raise exception 'publications: clip_id must reference a clips_cloud row owned by the same user';
  end if;
  return new;
end;
$$;

create trigger publications_owner_guard
before insert or update on public.publications
for each row execute function public.publications_owner_guard();

create function public.rejections_owner_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if new.clip_id is not null then
    if not exists (
      select 1 from public.clips_cloud c
      where c.id = new.clip_id and c.user_id = new.user_id
    ) then
      raise exception 'rejections: clip_id must reference a clips_cloud row owned by the same user';
    end if;
  end if;
  return new;
end;
$$;

create trigger rejections_owner_guard
before insert or update on public.rejections
for each row execute function public.rejections_owner_guard();

create function public.extraction_requests_owner_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if not exists (
    select 1 from public.source_sessions s
    where s.id = new.session_id and s.user_id = new.user_id
  ) then
    raise exception 'extraction_requests: session_id must reference a source_sessions row owned by the same user';
  end if;
  return new;
end;
$$;

create trigger extraction_requests_owner_guard
before insert or update on public.extraction_requests
for each row execute function public.extraction_requests_owner_guard();

-- Least-privilege write surface (runs AFTER table creation; strip Supabase default
-- privileges, then grant precisely). service_role keeps its full grant path for the
-- Worker; the service-role key lives in the Cloudflare Worker only (D11).
revoke all on all tables in schema public from anon, authenticated;

grant select on public.profiles, public.source_sessions, public.clips_cloud,
  public.media_objects, public.upload_reservations, public.publications,
  public.rejections, public.extraction_requests
  to authenticated;

-- The single client-side write in the whole schema: rename your own profile.
grant update (display_name) on public.profiles to authenticated;

-- The Worker executes every privileged operation as service_role; keep the ACL
-- explicit in case project default privileges ever change.
grant all on all tables in schema public to service_role;

-- Trigger-ish internals must not be callable directly by any non-admin role.
revoke execute on function public.handle_new_user(),
  public.profiles_quota_guard(), public.clips_cloud_owner_guard(),
  public.clips_cloud_review_integrity(), public.media_objects_owner_guard(),
  public.upload_reservations_owner_guard(), public.publications_owner_guard(),
  public.rejections_owner_guard(), public.extraction_requests_owner_guard()
  from public, anon, authenticated;

-- PHASE D CONTRACT - authoritative quota accounting (plan doc D4/D6).
-- The tables above do NOT enforce quotas by existing; enforcement is this exact
-- transaction, which the Worker (/reserve) must run atomically in Postgres:
--   1. ONE transaction per reserve call takes BOTH serialization points before any
--      accounting statement, always in the same fixed order on every path (deviating
--      orderings must never appear - two concurrently reachable orders can deadlock):
--        a. Global budget lock - serializes reservations ACROSS users; per-user row
--           locks alone cannot serialize the global budget between users:
--              select pg_advisory_xact_lock(hashtextextended('dispatch:global-quota-budget', 0));
--           This is the only intentional advisory-lock user in the schema; the
--           hashtextextended key is deterministic, so no magic id can collide silently.
--        b. Per-user headroom lock - serializes reservations of the SAME user:
--              select storage_quota_bytes from public.profiles where user_id = $1 for update;
--   2. usage_bytes =
--        sum(byte_count) over media_objects of the user with status in
--          ('active','deletion_pending')  -- pending rows always have byte_count = 0
--      + sum(expected_bytes) over upload_reservations of the user with
--          status = 'pending' and expires_at > now()  -- expired rows are excluded
--   3. Allow the reservation insert (status 'pending', expires_at = now() + ttl)
--      only if: usage_bytes + expected_bytes <= user quota, the same sums across all
--      users + expected_bytes <= global budget (8 GiB), expected_bytes <= kind cap
--      (512 MiB clip / 8 MiB thumb), and the outstanding-upload limit is STRICTLY 3:
--      count the user's status='pending' rows; refuse when the count is already 3, so
--      the insert that would create a fourth outstanding reservation is rejected
--      (matches D6 "<=3 outstanding reservations/user" and the quota kernel, which
--      refuses when current outstanding >= max_outstanding_uploads_per_user).
--   4. COMMIT, or ROLLBACK on any failure; never persist a partial decision and never
--      allow the browser client a direct insert here (it has no grant - see above).
--   5. /complete runs in ONE transaction: HEAD-verify the real R2 object size/type
--      (declared size is not proof), then promote media_objects pending -> active with
--      the real byte_count and flip the reservation to 'completed' together, and
--      re-validate the usage sum; refuse (and cancel the reservation) if the real
--      size exceeds the reservation bound. Idempotent on repeat. Completion never
--      raises the accounted total (real bytes <= reservation bound), so it needs no
--      global advisory lock; it must simply flip reservation+media atomically.
--   6. Cleanup marks stale 'pending' rows 'expired' (never deletes them) and asserting
--      "expires_at > now()" keeps expired rows out of the accounting.
--   7. Phase D acceptance needs the concurrency tests (plan doc Phase D): two /reserve
--      calls from DIFFERENT users, each within its own quota but jointly over the
--      global budget, commit exactly ONE reservation (the advisory lock serializes
--      them); a same-user burst never leaves more than 3 outstanding rows - the
--      fourth insert is refused.
-- tests/rls_two_user_checklist.sql verifies the accounting query against fixtures
-- once the migration is applied to an authorized project (manual, live-only); the
-- global advisory-lock serialization itself is exercised by the Phase D tests above.
