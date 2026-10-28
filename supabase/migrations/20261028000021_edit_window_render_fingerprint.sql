-- 0021: Track the exact rendered proxy a ready edit window represents.
-- Existence of a ready window is not proof that it contains the current render.
-- Additive only; legacy windows get NULL and are re-staged once to establish it.

alter table public.edit_windows
  add column if not exists render_fingerprint text;

create index if not exists idx_edit_windows_render_fingerprint
  on public.edit_windows(user_id, local_clip_id, render_fingerprint);
