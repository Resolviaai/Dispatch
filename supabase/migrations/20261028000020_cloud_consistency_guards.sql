-- 0020: Owner-scoped integrity guards + read-only consistency report.
-- Additive. No data deletion. Existing inconsistent rows are reported by
-- cloud_consistency_report(), not silently modified.

-- 1. An edit window may only reference media owned by the same user.
create or replace function public.enforce_edit_window_media_owner()
returns trigger language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if new.media_object_id is not null then
    if not exists (
      select 1 from media_objects mo
      where mo.id = new.media_object_id and mo.user_id = new.user_id
    ) then
      raise exception 'edit window % media object % is not owned by user %',
        new.id, new.media_object_id, new.user_id;
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_edit_window_media_owner on public.edit_windows;
create trigger trg_edit_window_media_owner
  before insert or update of media_object_id, user_id on public.edit_windows
  for each row execute function public.enforce_edit_window_media_owner();

-- 2. Window transcripts may only be written for a window owned by that user.
create or replace function public.enforce_window_transcript_owner()
returns trigger language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if not exists (
    select 1 from edit_windows w
    where w.id = new.window_id and w.user_id = new.user_id
  ) then
    raise exception 'window transcript % does not match the window owner', new.window_id;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_window_transcript_owner on public.window_transcripts;
create trigger trg_window_transcript_owner
  before insert or update of window_id, user_id on public.window_transcripts
  for each row execute function public.enforce_window_transcript_owner();

-- 3. Read-only consistency report (counts + sample ids). No writes.
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
        and transcript_status in ('missing', 'failed')
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
        select local_clip_id, window_start, window_end, count(*) c
        from edit_windows
        where user_id = p_user_id and status = 'ready'
        group by local_clip_id, window_start, window_end
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
