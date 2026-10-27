-- 0014: Cloud caption editing storage (offline edit + offline load).
-- Saved/edited caption tracks are stored per cloud clip. Shape matches the
-- PC-side captions contract (groups + default_style/default_layout/...).
-- Absent caption_data means "seed from window_transcripts.words instead".

alter table public.clips_cloud add column if not exists caption_data jsonb;
create index if not exists idx_clips_cloud_caption on public.clips_cloud (user_id, cloud_updated_at desc)
  where caption_data is not null;

create or replace function public.cloud_put_clip_captions(p_user_id uuid, p_local_clip_id text, p_caption jsonb)
returns void language plpgsql security definer set search_path = public as $$
begin
  if p_caption is null then
    raise exception 'caption payload required';
  end if;
  if pg_column_size(p_caption) > 262144 then
    raise exception 'caption payload too large (256 KiB cap)';
  end if;
  update clips_cloud set caption_data = p_caption
    where user_id = p_user_id and local_clip_id = p_local_clip_id;
  if not found then
    raise exception 'unknown clip';
  end if;
end;
$$;

revoke all on function public.cloud_put_clip_captions(uuid, text, jsonb) from public, anon, authenticated;
grant execute on function public.cloud_put_clip_captions(uuid, text, jsonb) to service_role;
