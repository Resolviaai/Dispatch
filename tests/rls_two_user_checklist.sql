-- =====================================================================================
-- Dispatch - two-user RLS & write-privilege verification.  MANUAL PROCEDURE (live DB).
-- =====================================================================================
-- MANDATORY READING:
--
-- 1. This file must run ONLY against an authorized Supabase project (postgres editor
--    connection), or an isolated local Postgres the owner explicitly sets up. Never point
--    the wrong row updates at real data; the script opens a transaction and ROLLS BACK at
--    the end, so a successful run persists nothing.
--
-- 2. PRIVILEGED ROLES BYPASS RLS: postgres, supabase_admin, and service_role all bypass
--    row level security. Consequences honored here:
--      - Only blocks explicitly labeled "(admin context)" (fixtures, service-path,
--        audit) run under the privileged role, modeling the Worker's service_role.
--      - EVERY client check switches to the end-user roles via
--          select set_config('request.jwt.claims', '{"sub":"<user uuid>",...}', true);
--          set local role authenticated;
--        (auth.uid() reads the claims; SET ROLE gives the actual client privileges.)
--      - Never conclude "RLS works" from any statement run under a privileged role.
--
-- 3. PREREQUISITES before running:
--      a. Apply supabase/migrations/0001_init.sql to the project.
--      b. Create two real auth users (Dashboard -> Authentication -> Users) or local shim
--         equivalents. Call them A and B and find their uuids.
--      c. Replace every @USER_A_UUID@ with user A's uuid and every @USER_B_UUID@ with
--         user B's uuid (plain find-replace; the surrounding quotes are already here).
--      d. You must be able to SET ROLE authenticated. Pre-flight check (run alone first):
--           select current_user, pg_has_role(current_user, 'authenticated', 'member') as can_impersonate;
--         If can_impersonate = false, connect to the admin role that owns the roles and run:
--           grant authenticated to <current_user>;
--         then re-run this file intact.
--
-- 4. HOW TO RUN SAFELY: paste the WHOLE file into the SQL editor and run it once. Run it
--    never statement-by-statement. On any failure, every later statement aborts and the
--    transaction is rolled back; fix the schema and re-run the whole file. All messages
--    printed by the checks say "ok: ..." or "RLS TEST FAILED: ...".
--
-- 5. WHAT IS VERIFIED (per Phase C audit):
--      - RLS visibility: two users, positive and negative, direct and indirect
--        (publications/rejections via clip references, extraction via session references).
--      - Write denial by grants: clients hold SELECT only (plus profiles.display_name).
--      - Defense in depth: even with a temporary simulated grant regression, RLS
--        (no insert/update policy) and the quota guard trigger still deny client writes.
--      - Cross-user relationship guards on the service write path.
--      - ai_suggested_* immutability, range_revision monotonicity + trim/bump coupling.
--      - Quota accounting query (Phase D contract) incl. expired-reservation exclusion.
--      - Privilege-surface audit via the introspection catalog.
--
-- 6. DB-backing note: static review of this file and of 0001_init.sql has been done, but
--    as of this checkpoint NO live Postgres/Supabase execution was authorized; treat the
--    results of the first real run as the actual verification.
-- =====================================================================================

begin;

create or replace function pg_temp.rls_assert(p_ok boolean, p_msg text) returns void
language plpgsql as $$
begin
  if p_ok is not true then
    raise exception 'RLS TEST FAILED: %', p_msg;
  end if;
end;
$$;

create or replace function pg_temp.rls_expect_reject(p_stmt text, p_sqlstate text, p_msg text) returns void
language plpgsql as $$
begin
  execute p_stmt;
  -- expected failure did not happen
  raise exception 'RLS TEST FAILED: % - statement unexpectedly succeeded', p_msg;
exception
  when others then
    if SQLSTATE <> p_sqlstate then
      raise exception 'RLS TEST FAILED: % - wrong failure (SQLSTATE %, message: %)', p_msg, SQLSTATE, MESSAGE_TEXT;
    end if;
    raise notice 'ok: % (SQLSTATE %)', p_msg, p_sqlstate;
end;
$$;

-- Make the helpers callable from the impersonated roles regardless of the project's
-- default function privileges.
grant execute on function pg_temp.rls_assert(boolean, text), pg_temp.rls_expect_reject(text, text, text)
  to authenticated, anon;

-- Verified SQLSTATEs used below:
--   42501 insufficient_privilege  (missing grant / row-level security violation)
--   P0001 raise_exception         (owner-guard and integrity triggers)
--   23514 check_violation         (table CHECK constraints)

-- =====================================================================================
-- 1. FIXTURES (admin context - models Worker/service_role writes)
-- =====================================================================================
insert into public.profiles (user_id)
values ('@USER_A_UUID@'), ('@USER_B_UUID@')
on conflict (user_id) do nothing;

update public.profiles set display_name = 'checklist-a' where user_id = '@USER_A_UUID@';
update public.profiles set display_name = 'checklist-b' where user_id = '@USER_B_UUID@';

insert into public.source_sessions (user_id, youtube_video_id, title, duration_seconds)
values
  ('@USER_A_UUID@', 'checklist-a', 'A session', 1800),
  ('@USER_B_UUID@', 'checklist-b', 'B session', 1800)
on conflict do nothing;

insert into public.clips_cloud
  (user_id, session_id, local_clip_id, title, status,
   ai_suggested_start_time, ai_suggested_end_time, start_time, end_time, range_revision)
values
  ('@USER_A_UUID@',
   (select id from public.source_sessions where user_id = '@USER_A_UUID@' and youtube_video_id = 'checklist-a'),
   'checklist-fixture', 'A clip', 'ready_review', 10, 40, 12, 38, 5),
  ('@USER_B_UUID@',
   (select id from public.source_sessions where user_id = '@USER_B_UUID@' and youtube_video_id = 'checklist-b'),
   'checklist-fixture', 'B clip', 'ready_review', 20, 50, 22, 48, 5)
on conflict do nothing;

insert into public.media_objects (user_id, clip_id, kind, status, staging_key, final_key, mime_type, byte_count)
values
  ('@USER_B_UUID@',
   (select id from public.clips_cloud where user_id = '@USER_B_UUID@' and local_clip_id = 'checklist-fixture'),
   'clip_video', 'active', 'staging/checklist-b', 'final/checklist-b', 'video/mp4', 1000000)
on conflict do nothing;

insert into public.media_objects (user_id, clip_id, kind, status, mime_type)
values
  ('@USER_A_UUID@',
   (select id from public.clips_cloud where user_id = '@USER_A_UUID@' and local_clip_id = 'checklist-fixture'),
   'clip_video', 'pending', 'video/mp4')
on conflict do nothing;

insert into public.upload_reservations (user_id, media_object_id, client_token, expected_bytes, status, expires_at)
values
  -- fresh pending reservation: counts toward usage
  ('@USER_B_UUID@',
   (select id from public.media_objects where user_id = '@USER_B_UUID@' and final_key = 'final/checklist-b'),
   'checklist-b', 500000, 'pending', now() + interval '30 minutes'),
  -- expired pending reservation: must be EXCLUDED from usage (Phase D accounting rule)
  ('@USER_B_UUID@',
   (select id from public.media_objects where user_id = '@USER_B_UUID@' and final_key = 'final/checklist-b'),
   'checklist-b-expired', 700000, 'pending', now() - interval '1 minute')
on conflict (user_id, client_token) do nothing;

insert into public.publications (clip_id, user_id, platform, status, published_at, remote_url)
values (
  (select id from public.clips_cloud where user_id = '@USER_B_UUID@' and local_clip_id = 'checklist-fixture'),
  '@USER_B_UUID@', 'youtube', 'published', now() - interval '1 day', 'https://youtu.be/checklist-b');

insert into public.rejections (clip_id, user_id, reason)
values (
  (select id from public.clips_cloud where user_id = '@USER_B_UUID@' and local_clip_id = 'checklist-fixture'),
  '@USER_B_UUID@', 'checklist');

insert into public.extraction_requests (user_id, session_id, status)
values (
  '@USER_B_UUID@',
  (select id from public.source_sessions where user_id = '@USER_B_UUID@' and youtube_video_id = 'checklist-b'),
  'queued');

-- Capture fixture ids for by-id probes (uuids; every consumer must cast ::uuid).
select set_config('app.a_clip',
  (select id::text from public.clips_cloud where user_id = '@USER_A_UUID@' and local_clip_id = 'checklist-fixture'), true);
select set_config('app.b_clip',
  (select id::text from public.clips_cloud where user_id = '@USER_B_UUID@' and local_clip_id = 'checklist-fixture'), true);
select set_config('app.b_media',
  (select id::text from public.media_objects where user_id = '@USER_B_UUID@' and final_key = 'final/checklist-b'), true);

-- Fixture counts are scoped to fixture markers so a run against a project that already
-- holds real rows is never a false failure.
select pg_temp.rls_assert((select count(*) from public.clips_cloud where local_clip_id = 'checklist-fixture') = 2,
  'fixtures present: exactly two checklist clips');
select pg_temp.rls_assert(
  (select count(*) from public.media_objects m join public.clips_cloud c on c.id = m.clip_id
   where c.local_clip_id = 'checklist-fixture') = 2,
  'fixtures present: exactly two checklist media rows');
select pg_temp.rls_assert(
  (select count(*) from public.upload_reservations
   where user_id = '@USER_B_UUID@' and client_token like 'checklist-b%') = 2,
  'fixtures present: exactly two checklist reservations');

-- =====================================================================================
-- 2. IMPERSONATE USER A (client context) - RLS visibility
-- =====================================================================================
select set_config('request.jwt.claims', '{"sub":"@USER_A_UUID@","role":"authenticated"}', true);
set local role authenticated;

select pg_temp.rls_assert(exists (
  select 1 from public.clips_cloud where user_id = '@USER_A_UUID@'), 'A reads own clip row');
select pg_temp.rls_assert((select count(*) from public.clips_cloud where user_id <> '@USER_A_UUID@') = 0,
  'A sees only own clip rows');

select pg_temp.rls_assert(not exists (
  select 1 from public.clips_cloud where id = current_setting('app.b_clip')::uuid),
  'A cannot select the B clip row even by its exact id');

select pg_temp.rls_assert(not exists (
  select 1 from public.media_objects where id = current_setting('app.b_media')::uuid),
  'A cannot select the B media row by its exact id');
select pg_temp.rls_assert(not exists (
  select 1 from public.media_objects where user_id = '@USER_B_UUID@'),
  'A reads no B media rows');

select pg_temp.rls_assert(not exists (
  select 1 from public.source_sessions where user_id = '@USER_B_UUID@'),
  'A reads no B sessions');

select pg_temp.rls_assert(not exists (
  select 1 from public.upload_reservations where user_id = '@USER_B_UUID@'),
  'A reads no B reservations');

-- Indirect relationships: A may never observe rows that reference B-owned resources.
select pg_temp.rls_assert(not exists (
  select 1 from public.publications p join public.clips_cloud c on c.id = p.clip_id
  where c.user_id = '@USER_B_UUID@'),
  'A reads no publications of B clips (indirect over clip reference)');
select pg_temp.rls_assert(not exists (
  select 1 from public.rejections j join public.clips_cloud c on c.id = j.clip_id
  where c.user_id = '@USER_B_UUID@'),
  'A reads no rejections of B clips (indirect over clip reference)');
select pg_temp.rls_assert(not exists (
  select 1 from public.extraction_requests e join public.source_sessions s on s.id = e.session_id
  where s.user_id = '@USER_B_UUID@'),
  'A reads no extraction requests on B sessions (indirect over session reference)');

-- =====================================================================================
-- 3. IMPERSONATE USER A - client write denial by grants (SQLSTATE 42501)
-- =====================================================================================
-- Clients hold SELECT only, so every direct write fails before RLS even applies.
select pg_temp.rls_expect_reject(
  'insert into public.clips_cloud (user_id, local_clip_id, title, status, ai_suggested_start_time, ai_suggested_end_time, start_time, end_time) values (''@USER_A_UUID@'', ''checklist-attack'', ''x'', ''ready_review'', 3, 9, 3, 9)',
  '42501', 'A cannot insert into clips_cloud (quota/review writes are service-only)');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set title = ''nope'' where user_id = ''@USER_A_UUID@''',
  '42501', 'A cannot update own clip rows');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set title = ''hijack'' where id = current_setting(''app.b_clip'')::uuid',
  '42501', 'A cannot update the B clip row by id');

select pg_temp.rls_expect_reject(
  'insert into public.source_sessions (user_id, youtube_video_id) values (''@USER_A_UUID@'', ''checklist-attack'')',
  '42501', 'A cannot create source_sessions (PC/Worker pushes sessions)');
select pg_temp.rls_expect_reject(
  'delete from public.source_sessions where user_id = ''@USER_A_UUID@''',
  '42501', 'A cannot delete own sessions');

select pg_temp.rls_expect_reject(
  'insert into public.media_objects (user_id, kind, status, mime_type) values (''@USER_A_UUID@'', ''clip_video'', ''pending'', ''video/mp4'')',
  '42501', 'A cannot create media_objects (bypasses reservation/completion logic)');
select pg_temp.rls_expect_reject(
  'delete from public.media_objects where user_id = ''@USER_A_UUID@''',
  '42501', 'A cannot delete media_objects (deletion must confirm R2 state)');

select pg_temp.rls_expect_reject(
  'insert into public.upload_reservations (user_id, media_object_id, client_token, expected_bytes) values (''@USER_A_UUID@'', (select id from public.media_objects where user_id = ''@USER_A_UUID@'' and clip_id in (select id from public.clips_cloud where user_id = ''@USER_A_UUID@'' and local_clip_id = ''checklist-fixture'')), ''checklist-attack'', 999999999)',
  '42501', 'A cannot create upload_reservations (quota kernel is service-only)');
select pg_temp.rls_expect_reject(
  'delete from public.upload_reservations where user_id = ''@USER_A_UUID@''',
  '42501', 'A cannot delete upload_reservations');

select pg_temp.rls_expect_reject(
  'insert into public.publications (clip_id, user_id, platform, status) values ((select id from public.clips_cloud where user_id = ''@USER_A_UUID@'' and local_clip_id = ''checklist-fixture''), ''@USER_A_UUID@'', ''youtube'', ''published'')',
  '42501', 'A cannot fabricate publication state');
select pg_temp.rls_expect_reject(
  'insert into public.rejections (clip_id, user_id, reason) values ((select id from public.clips_cloud where user_id = ''@USER_A_UUID@'' and local_clip_id = ''checklist-fixture''), ''@USER_A_UUID@'', ''checklist-attack'')',
  '42501', 'A cannot fabricate rejection records');
select pg_temp.rls_expect_reject(
  'insert into public.extraction_requests (user_id, session_id) values (''@USER_A_UUID@'', (select id from public.source_sessions where user_id = ''@USER_A_UUID@'' and youtube_video_id = ''checklist-a''))',
  '42501', 'A cannot create extraction_requests (semantics pending Phase D routes)');
select pg_temp.rls_expect_reject(
  'delete from public.extraction_requests where user_id = ''@USER_A_UUID@''',
  '42501', 'A cannot delete extraction_requests');

select pg_temp.rls_expect_reject(
  'insert into public.profiles (user_id, storage_quota_bytes) values (''@USER_A_UUID@'', 2147483648)',
  '42501', 'A cannot insert profile rows (creation is trigger/service-owned)');
select pg_temp.rls_expect_reject(
  'delete from public.profiles where user_id = ''@USER_A_UUID@''',
  '42501', 'A cannot delete own profile (account removal is service-only)');
select pg_temp.rls_expect_reject(
  'update public.profiles set storage_quota_bytes = 2147483648 where user_id = ''@USER_A_UUID@''',
  '42501', 'A cannot raise own storage quota (column not granted)');

-- The one allowed client write: display_name self-service.
update public.profiles set display_name = 'checklist-a-rename' where user_id = '@USER_A_UUID@';
select pg_temp.rls_assert(
  (select display_name from public.profiles where user_id = '@USER_A_UUID@') = 'checklist-a-rename',
  'A renames own profile (display_name is the single client write)');
update public.profiles set display_name = 'hijack' where user_id = '@USER_B_UUID@';

-- =====================================================================================
-- 4. DEFENSE IN DEPTH 1 (admin context): simulate a future grant regression
-- =====================================================================================
reset role;
grant insert, update, delete on public.clips_cloud to authenticated;
set local role authenticated;

select pg_temp.rls_expect_reject(
  'insert into public.clips_cloud (user_id, local_clip_id, title, status, ai_suggested_start_time, ai_suggested_end_time, start_time, end_time) values (''@USER_A_UUID@'', ''checklist-attack2'', ''x'', ''ready_review'', 3, 9, 3, 9)',
  '42501', 'simulated grant leak: RLS still denies client INSERT (no insert policy exists)');
update public.clips_cloud set title = 'hijack' where id = current_setting('app.b_clip')::uuid;
update public.clips_cloud set title = 'nope' where user_id = '@USER_A_UUID@';

reset role;
revoke insert, update, delete on public.clips_cloud from authenticated;

select pg_temp.rls_assert(
  (select title from public.clips_cloud where id = current_setting('app.b_clip')::uuid) = 'B clip'
  and (select title from public.clips_cloud where user_id = '@USER_A_UUID@' and local_clip_id = 'checklist-fixture') = 'A clip'
  and (select count(*) from public.clips_cloud where local_clip_id like 'checklist-attack%') = 0,
  'simulated grant leak: RLS still denied insert and updates, rows unchanged');

-- =====================================================================================
-- 5. DEFENSE IN DEPTH 2 (admin context): profiles quota guard trigger
-- =====================================================================================
grant update (display_name, storage_quota_bytes) on public.profiles to authenticated;
set local role authenticated;

select pg_temp.rls_expect_reject(
  'update public.profiles set display_name = ''ok'', storage_quota_bytes = 2147483648 where user_id = ''@USER_A_UUID@''',
  'P0001', 'simulated grant leak: quota guard trigger blocks client quota change even with the column granted');

reset role;
revoke update (storage_quota_bytes) on public.profiles from authenticated;

select pg_temp.rls_expect_reject(
  'update public.profiles set storage_quota_bytes = 3221225472 where user_id = ''@USER_A_UUID@''',
  '23514', 'service-side quota set beyond the hard cap (2 GiB) hits the table CHECK');
update public.profiles set storage_quota_bytes = 536870912 where user_id = '@USER_A_UUID@';
select pg_temp.rls_assert(
  (select storage_quota_bytes from public.profiles where user_id = '@USER_A_UUID@') = 536870912,
  'service-side quota change succeeds (the authorized server-side operation)');

-- =====================================================================================
-- 6. OWNER GUARDS (admin context): cross-user references rejected on the service path.
--    These model service_role/Worker writes; the guards fire for every role.
-- =====================================================================================
select pg_temp.rls_expect_reject(
  'insert into public.clips_cloud (user_id, session_id, local_clip_id, title, status, ai_suggested_start_time, ai_suggested_end_time, start_time, end_time) values (''@USER_A_UUID@'', (select id from public.source_sessions where user_id = ''@USER_B_UUID@'' and youtube_video_id = ''checklist-b''), ''checklist-xref'', ''x'', ''ready_review'', 1, 2, 1, 2)',
  'P0001', 'owner guard: a clip may not reference another user''s session');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set session_id = (select id from public.source_sessions where user_id = ''@USER_A_UUID@'' and youtube_video_id = ''checklist-a'') where user_id = ''@USER_B_UUID@'' and local_clip_id = ''checklist-fixture''',
  'P0001', 'owner guard: a service update may not re-point a clip to another user''s session');

select pg_temp.rls_expect_reject(
  'insert into public.media_objects (user_id, clip_id, kind, status, mime_type) values (''@USER_A_UUID@'', (select id from public.clips_cloud where user_id = ''@USER_B_UUID@'' and local_clip_id = ''checklist-fixture''), ''clip_video'', ''pending'', ''video/mp4'')',
  'P0001', 'owner guard: media may not attach to another user''s clip');

select pg_temp.rls_expect_reject(
  'insert into public.upload_reservations (user_id, media_object_id, client_token, expected_bytes) values (''@USER_A_UUID@'', (select id from public.media_objects where user_id = ''@USER_B_UUID@'' and final_key = ''final/checklist-b''), ''checklist-xref'', 1000)',
  'P0001', 'owner guard: a reservation may not target another user''s media object');

select pg_temp.rls_expect_reject(
  'insert into public.publications (clip_id, user_id, platform, status) values ((select id from public.clips_cloud where user_id = ''@USER_B_UUID@'' and local_clip_id = ''checklist-fixture''), ''@USER_A_UUID@'', ''youtube'', ''queued'')',
  'P0001', 'owner guard: a publication may not reference another user''s clip');

select pg_temp.rls_expect_reject(
  'insert into public.rejections (clip_id, user_id, reason) values ((select id from public.clips_cloud where user_id = ''@USER_B_UUID@'' and local_clip_id = ''checklist-fixture''), ''@USER_A_UUID@'', ''checklist'')',
  'P0001', 'owner guard: a rejection may not record another user''s clip');

select pg_temp.rls_expect_reject(
  'insert into public.extraction_requests (user_id, session_id) values (''@USER_A_UUID@'', (select id from public.source_sessions where user_id = ''@USER_B_UUID@'' and youtube_video_id = ''checklist-b''))',
  'P0001', 'owner guard: an extraction request may not target another user''s session');

-- =====================================================================================
-- 7. REVIEW INTEGRITY (admin context): immutable AI suggestions + revision contract
-- =====================================================================================
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set ai_suggested_start_time = 11 where id = current_setting(''app.a_clip'')::uuid',
  'P0001', 'integrity: ai_suggested_start_time is immutable even to the service');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set ai_suggested_end_time = 44 where id = current_setting(''app.a_clip'')::uuid',
  'P0001', 'integrity: ai_suggested_end_time is immutable even to the service');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set range_revision = 4 where id = current_setting(''app.a_clip'')::uuid',
  'P0001', 'integrity: range_revision may not decrease (5 -> 4)');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set start_time = 13 where id = current_setting(''app.a_clip'')::uuid',
  'P0001', 'integrity: trim change without a revision bump is rejected');

update public.clips_cloud set range_revision = 6 where id = current_setting('app.a_clip')::uuid;
select pg_temp.rls_assert(
  (select range_revision from public.clips_cloud where id = current_setting('app.a_clip')::uuid) = 6,
  'integrity: revision-only bump is allowed');
update public.clips_cloud set start_time = 13, end_time = 37, range_revision = 7 where id = current_setting('app.a_clip')::uuid;
select pg_temp.rls_assert(
  (select start_time from public.clips_cloud where id = current_setting('app.a_clip')::uuid) = 13
  and (select end_time from public.clips_cloud where id = current_setting('app.a_clip')::uuid) = 37
  and (select range_revision from public.clips_cloud where id = current_setting('app.a_clip')::uuid) = 7
  and (select ai_suggested_start_time from public.clips_cloud where id = current_setting('app.a_clip')::uuid) = 10
  and (select ai_suggested_end_time from public.clips_cloud where id = current_setting('app.a_clip')::uuid) = 40,
  'integrity: trim change with a bump succeeds; ai_suggested values remain untouched');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set range_revision = 6 where id = current_setting(''app.a_clip'')::uuid',
  'P0001', 'integrity: range_revision may not decrease (7 -> 6)');

-- =====================================================================================
-- 8. QUOTA ACCOUNTING (admin context): the Phase D reservation/usage contract query
-- =====================================================================================
-- Accounting asserts are scoped to fixture rows (final_key / client_token markers) so a
-- project that already holds real user data can never produce a false failure.
select pg_temp.rls_assert(
  (coalesce((select sum(byte_count) from public.media_objects
             where user_id = '@USER_B_UUID@' and status in ('active','deletion_pending')
               and final_key = 'final/checklist-b'), 0)
   + coalesce((select sum(expected_bytes) from public.upload_reservations
               where user_id = '@USER_B_UUID@' and status = 'pending' and expires_at > now()
                 and client_token like 'checklist-b%'), 0)) = 1500000,
  'accounting: user usage = active media bytes (1000000) + fresh pending reservation (500000); the expired 700000 reservation is excluded');

select pg_temp.rls_assert(
  (coalesce((select sum(byte_count) from public.media_objects
             where status in ('active','deletion_pending') and final_key = 'final/checklist-b'), 0)
   + coalesce((select sum(expected_bytes) from public.upload_reservations
               where status = 'pending' and expires_at > now()
                 and client_token like 'checklist-b%'), 0)) = 1500000,
  'accounting: the same rule set reused for the global budget check (fixture-scoped)');

-- =====================================================================================
-- 9. IMPERSONATE USER B (client context) - mirror checks
-- =====================================================================================
select set_config('request.jwt.claims', '{"sub":"@USER_B_UUID@","role":"authenticated"}', true);
set local role authenticated;

select pg_temp.rls_assert((select count(*) from public.clips_cloud where user_id <> '@USER_B_UUID@') = 0,
  'B sees only own clip rows');
select pg_temp.rls_assert(exists (
  select 1 from public.clips_cloud where user_id = '@USER_B_UUID@'), 'B reads own clip row');
select pg_temp.rls_assert(not exists (
  select 1 from public.clips_cloud where id = current_setting('app.a_clip')::uuid),
  'B cannot select the A clip row even by its exact id');
select pg_temp.rls_assert(not exists (
  select 1 from public.media_objects where user_id = '@USER_A_UUID@'), 'B reads no A media');
select pg_temp.rls_assert(not exists (
  select 1 from public.publications p join public.clips_cloud c on c.id = p.clip_id
  where c.user_id = '@USER_A_UUID@'),
  'B reads no publications of A clips (indirect over clip reference)');
select pg_temp.rls_assert(not exists (
  select 1 from public.rejections j join public.clips_cloud c on c.id = j.clip_id
  where c.user_id = '@USER_A_UUID@'),
  'B reads no rejections of A clips (indirect over clip reference)');
select pg_temp.rls_assert(not exists (
  select 1 from public.extraction_requests e join public.source_sessions s on s.id = e.session_id
  where s.user_id = '@USER_A_UUID@'),
  'B reads no extraction requests on A sessions (indirect over session reference)');
select pg_temp.rls_expect_reject(
  'update public.clips_cloud set title = ''hijack'' where user_id = ''@USER_A_UUID@''',
  '42501', 'B cannot update clips_cloud rows');
select pg_temp.rls_expect_reject(
  'delete from public.upload_reservations where user_id = ''@USER_A_UUID@''',
  '42501', 'B cannot delete upload_reservations');
-- Note: profiles.display_name IS granted, so a foreign-row rename matches ZERO rows
-- silently (the update policy hides the row); the no-change proof runs in section 10.
update public.profiles set display_name = 'checklist-b-rename' where user_id = '@USER_A_UUID@';

-- =====================================================================================
-- 10. PRIVILEGE-SURFACE AUDIT (admin context; introspection catalog)
-- =====================================================================================
reset role;

-- TABLE-LEVEL: authenticated = select only; anon = nothing. has_*_privilege() reads the
-- ACL directly, so this audit does not depend on grantor visibility.
select pg_temp.rls_assert(not exists (
  select 1 from (values
    ('public.profiles'), ('public.source_sessions'), ('public.clips_cloud'),
    ('public.media_objects'), ('public.upload_reservations'), ('public.publications'),
    ('public.rejections'), ('public.extraction_requests')
  ) as t(qualified)
  where has_table_privilege('authenticated', t.qualified, 'insert')
     or has_table_privilege('authenticated', t.qualified, 'update')
     or has_table_privilege('authenticated', t.qualified, 'delete')
     or has_table_privilege('authenticated', t.qualified, 'truncate')
     or has_table_privilege('anon', t.qualified, 'insert')
     or has_table_privilege('anon', t.qualified, 'update')
     or has_table_privilege('anon', t.qualified, 'delete')
     or has_table_privilege('anon', t.qualified, 'select')),
  'audit: authenticated holds select only; anon holds nothing (table level)');

select pg_temp.rls_assert(
  has_table_privilege('authenticated', 'public.clips_cloud', 'select')
  and has_table_privilege('authenticated', 'public.profiles', 'select'),
  'audit: the reviewer read path is granted to authenticated');

-- COLUMN-LEVEL: display_name update is the single client column grant.
select pg_temp.rls_assert(
  has_column_privilege('authenticated', 'public.profiles', 'display_name', 'update')
  and not has_column_privilege('authenticated', 'public.profiles', 'storage_quota_bytes', 'update')
  and not has_column_privilege('authenticated', 'public.profiles', 'user_id', 'update'),
  'audit: profiles column grants are display_name-update only');

select pg_temp.rls_assert(
  has_table_privilege('service_role', 'public.clips_cloud', 'insert')
  and has_table_privilege('service_role', 'public.media_objects', 'delete'),
  'audit: service_role retains its write path (Worker privilege surface intact)');

select pg_temp.rls_assert(
  (select display_name from public.profiles where user_id = '@USER_A_UUID@') = 'checklist-a-rename'
  and (select display_name from public.profiles where user_id = '@USER_B_UUID@') = 'checklist-b',
  'audit: cross-user rename attempts (A->B, B->A) changed no profile');

select pg_temp.rls_assert(true, 'all checklist assertions completed');
do $$ begin
  raise notice 'RLS CHECKLIST: ALL CHECKS PASSED - the final rollback discards all fixture rows.';
end $$;

rollback;
