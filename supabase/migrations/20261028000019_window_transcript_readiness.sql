-- 0019: Make media readiness and transcript readiness explicit.
-- A ready edit window may exist before its transcript is persisted; clients must
-- be able to tell the difference instead of rendering empty captions.
-- Additive and idempotent.

alter table public.edit_windows
  add column if not exists transcript_status text not null default 'missing'
  check (transcript_status in ('ready', 'missing', 'failed'));

alter table public.edit_windows
  add column if not exists transcript_error text;

-- Existing windows with a non-empty transcript are genuinely ready.
update public.edit_windows w
set transcript_status = 'ready'
where w.transcript_status = 'missing'
  and exists (
    select 1 from public.window_transcripts t
    where t.window_id = w.id
      and jsonb_typeof(t.words) = 'array'
      and jsonb_array_length(t.words) > 0
  );

create index if not exists idx_edit_windows_transcript_status
  on public.edit_windows(user_id, transcript_status);
