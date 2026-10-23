-- Dispatch cloud schema 0001 (plan doc D4). Supabase Postgres; auth.users = identity.
-- Rollback: drop schema objects in reverse (drop table ... cascade). Never destroys local SQLite.
-- Apply only after owner authorization (docs/cloud/OWNER_SETUP.md).

create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  storage_quota_bytes bigint not null default 1073741824 check (storage_quota_bytes <= 2147483648),
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
  range_revision integer not null default 0,
  render_stale boolean not null default false,
  local_updated_at timestamptz not null default now(),
  cloud_updated_at timestamptz not null default now(),
  unique (user_id, local_clip_id)
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
  deleted_at timestamptz
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
  status text not null check (status in ('queued','uploading','published','failed'))
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
  claimed_at timestamptz
);

create index idx_clips_user_status on public.clips_cloud (user_id, status);
create index idx_media_user_status on public.media_objects (user_id, status);
create index idx_media_expires on public.media_objects (expires_at) where status in ('active','deletion_pending');
create index idx_resv_expires on public.upload_reservations (expires_at) where status = 'pending';
create index idx_extraction_status on public.extraction_requests (status);

alter table public.profiles enable row level security;
alter table public.source_sessions enable row level security;
alter table public.clips_cloud enable row level security;
alter table public.media_objects enable row level security;
alter table public.upload_reservations enable row level security;
alter table public.publications enable row level security;
alter table public.rejections enable row level security;
alter table public.extraction_requests enable row level security;

create policy "own profile" on public.profiles for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own sessions" on public.source_sessions for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own clips" on public.clips_cloud for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own media" on public.media_objects for select using (user_id = auth.uid());
create policy "own reservations" on public.upload_reservations for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own publications" on public.publications for select using (user_id = auth.uid());
create policy "own rejections" on public.rejections for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own extraction" on public.extraction_requests for all using (user_id = auth.uid()) with check (user_id = auth.uid());

revoke all on all tables in schema public from anon, authenticated;
grant select, insert, update, delete on public.profiles, public.source_sessions, public.clips_cloud,
  public.upload_reservations, public.rejections, public.extraction_requests to authenticated;
grant select on public.media_objects, public.publications to authenticated;
-- service_role (Worker/PC privileged ops) bypasses RLS; keep key server-side only.
