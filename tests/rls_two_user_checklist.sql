-- Two-user RLS verification (run in Supabase SQL editor as postgres; MANUAL until a real project exists).
-- Setup: create two auth users via dashboard, then run as each JWT:
--   set request.jwt.claims = '{"sub":"<USER_A_UUID>","role":"authenticated"}';
-- Expected results (1=pass):
select 'A sees only own clips'  as check, count(*) = 0 as pass from public.clips_cloud where user_id <> '<USER_A_UUID>';
select 'A cannot read B media'  as check, count(*) = 0 as pass from public.media_objects where user_id <> '<USER_A_UUID>';
-- As USER_A, expect error (RLS violation):
--   insert into public.clips_cloud (user_id, local_clip_id, title, status, ai_suggested_start_time, ai_suggested_end_time, start_time, end_time)
--   values ('<USER_B_UUID>','x','x','ready_review',0,1,0,1);
-- As USER_A, expect 0 rows updated (ownership + no matching row):
--   update public.clips_cloud set title='hijack' where id='<B_CLIP_UUID>';
-- Cross-user via related row: A selecting publications of B's clip returns 0 rows.
-- Then reset: set request.jwt.claims = '{"sub":"<USER_B_UUID>","role":"authenticated"}'; repeat the inserts expecting success.
