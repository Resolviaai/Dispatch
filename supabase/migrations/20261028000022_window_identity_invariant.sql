-- 0022: Window identity invariant + honest duplicate detection.
--
-- Root cause (found while backfilling transcripts, 2026-10-28): edit_windows
-- bounded idempotency by exact float8 equality, but PostgREST/Postgres renders
-- float8 with extra_float_digits=0 (15 significant digits). A value read back
-- from the state endpoint can therefore be a DIFFERENT double than the one
-- stored (318.03999999999996 renders as "318.04"), so the reuse lookup missed
-- and a duplicate ready window was inserted. The Worker now keys window
-- identity on the row's primary key; this migration makes the report see the
-- duplicates that exact-equality grouping hid, and forbids them outright.
--
-- Apply only after any existing duplicates have been collapsed, otherwise the
-- unique index creation fails and the transaction rolls back (this file deletes
-- nothing). The repair for the 2026-10-28 pair was an explicit operator-
-- authorized DELETE that kept each clip's transcript-bearing window.

-- 1. Ready windows are unique per account + clip + bounds (rounded to sub-frame
--    tolerance so float drift cannot smuggle a second row past the constraint).
create unique index if not exists uix_edit_windows_ready_bounds
  on public.edit_windows (
    user_id,
    local_clip_id,
    round(window_start::numeric, 3),
    round(window_end::numeric, 3)
  )
  where status = 'ready';

-- 2. Consistency report: group duplicates on the rounded bounds, and report a
--    ready window that has no transcript row even when transcript_status claims
--    ready (a status/data split can only happen through a partial write).
create or replace function public.cloud_consistency_report(p_user_id uuid)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_result jsonb;
begin
  select jsonb_build_object(
    'user_id', p_user_id,
    'clips_missing_owner', (
      select count(*) from clips_cloud where user_id is null
    ),
    'ready_windows_without_transcript', (
      select count(*) from edit_windows
      where user_id = p_user_id and status = 'ready'
        and (transcript_status in ('missing', 'failed')
             or (transcript_status = 'ready' and not exists (
                  select 1 from window_transcripts t where t.window_id = edit_windows.id
                    and jsonb_array_length(t.words) > 0)))
    ),
    'ready_windows_missing_media', (
      select count(*) from edit_windows w
      where w.user_id = p_user_id and w.status = 'ready' and w.media_object_id is null
    ),
    'windows_with_foreign_media', (
      select count(*) from edit_windows w
      join media_objects mo on mo.id = w.media_object_id
      where w.user_id = p_user_id and mo.user_id <> w.user_id
    ),
    'foreign_window_transcripts', (
      select count(*) from window_transcripts t
      join edit_windows w on w.id = t.window_id
      where w.user_id = p_user_id and t.user_id <> w.user_id
    ),
    'duplicate_ready_windows', (
      select count(*) from (
        select local_clip_id
        from edit_windows
        where user_id = p_user_id and status = 'ready'
        group by local_clip_id, round(window_start::numeric, 3), round(window_end::numeric, 3)
        having count(*) > 1
      ) d
    ),
    'stale_reservations', (
      select count(*) from upload_reservations ur
      where ur.user_id = p_user_id and ur.status = 'pending'
        and ur.created_at < now() - interval '1 hour'
    ),
    'pending_commands_without_attempts', (
      select count(*) from clip_commands c
      where c.user_id = p_user_id and c.status = 'pending' and c.attempts = 0
        and c.created_at < now() - interval '15 minutes'
    ),
    'sample_missing_transcript_windows', (
      select coalesce(jsonb_agg(w.id), '[]'::jsonb) from (
        select id from edit_windows
        where user_id = p_user_id and status = 'ready'
          and transcript_status in ('missing', 'failed')
        order by created_at asc limit 20
      ) w
    )
  ) into v_result;
  return v_result;
end;
$$;

revoke all on function public.cloud_consistency_report(uuid) from public, anon, authenticated;
grant execute on function public.cloud_consistency_report(uuid) to service_role;
