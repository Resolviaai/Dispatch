-- Latest per-destination metrics, cached on the existing publication row.
-- account_id is the publisher's stored account reference (UUID, channel id, or
-- email as recorded by the PC outbox); it is not a credential.
alter table public.publications
  add column if not exists account_id text not null default '',
  add column if not exists publish_mode text
    check (publish_mode is null or publish_mode in ('private', 'unlisted', 'public')),
  add column if not exists view_count bigint,
  add column if not exists like_count bigint,
  add column if not exists comment_count bigint,
  add column if not exists stats_updated_at timestamptz,
  add column if not exists stats_checked_at timestamptz;

-- Fail closed before creating the upsert key rather than deleting ambiguous
-- history inside a schema migration.
do $$
declare
  duplicate_count bigint;
begin
  select count(*) into duplicate_count
  from (
    select 1
    from public.publications
    group by clip_id, platform, account_id
    having count(*) > 1
  ) duplicates;
  if duplicate_count > 0 then
    raise exception
      'publications contains % duplicate clip/platform/account destinations; resolve them before applying 0024',
      duplicate_count;
  end if;
end
$$;

-- One destination per clip/platform/account.
create unique index if not exists publications_clip_platform_account_uidx
  on public.publications (clip_id, platform, account_id);

create index if not exists idx_publications_user_clip
  on public.publications (user_id, clip_id);
