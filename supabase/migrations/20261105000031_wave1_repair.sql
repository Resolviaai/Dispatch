-- 20261105000031_wave1_repair.sql
-- Forward-only Wave 1 repair migration (§9.1–§9.7):
--   §9.1  Expired quota reservation byte accounting in public.cloud_quota_usage
--   §9.2  AutoCut action_type, rev-0 CAS, rev-1 undo guard, caption/hull/range_revision sync
--   §9.3  Multi-target publishing queue (publishing_job_targets, account_ids, target RPCs)
--   §9.4  Multi-platform & local_clip_id support on public.publications + owner guard update
--   §9.5  Grant/TRUNCATE revokes, pg_catalog-first search_path hardening, tenant-scoped backfill
--   §9.6  YouTube URL regex (&v=, /live/, /embed/, shorts/, youtu.be/) in cloud_submit_job
--   §9.7  High-churn operational & FK indexes across core cloud tables

-- ============================================================================
-- §9.1 Expired Quota Reservations
-- ============================================================================
create or replace function public.cloud_quota_usage(p_user_id uuid)
returns table (
  user_stored_bytes bigint,
  user_reserved_bytes bigint,
  user_outstanding int,
  global_stored_bytes bigint,
  global_reserved_bytes bigint
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select
    (coalesce(sum(case when mo.user_id = p_user_id and mo.status in ('active', 'deletion_pending') then mo.byte_count end), 0)
      + coalesce(sum(case when mo.user_id = p_user_id and mo.status = 'active' and mo.staging_key is not null then mo.byte_count end), 0))::bigint,
    (select coalesce(sum(ur.expected_bytes), 0)::bigint
       from public.upload_reservations ur
       join public.media_objects m2 on m2.id = ur.media_object_id
      where ur.user_id = p_user_id
        and ur.status = 'pending'
        and ur.expires_at > now()
        and m2.status = 'pending'),
    (select count(*)::int
       from public.upload_reservations ur
      where ur.user_id = p_user_id
        and ur.status = 'pending'
        and ur.expires_at > now()),
    (coalesce(sum(case when mo.status in ('active', 'deletion_pending') then mo.byte_count end), 0)
      + coalesce(sum(case when mo.status = 'active' and mo.staging_key is not null then mo.byte_count end), 0))::bigint,
    (select coalesce(sum(ur.expected_bytes), 0)::bigint
       from public.upload_reservations ur
       join public.media_objects m3 on m3.id = ur.media_object_id
      where ur.status = 'pending'
        and ur.expires_at > now()
        and m3.status = 'pending')
  from public.media_objects mo;
$$;

revoke all on function public.cloud_quota_usage(uuid) from public, authenticated, anon;
grant execute on function public.cloud_quota_usage(uuid) to service_role;

-- ============================================================================
-- §9.2 AutoCut & Project Document Revision Fixes
-- ============================================================================
do $$
declare
  v_con record;
begin
  for v_con in
    select conname
    from pg_catalog.pg_constraint
    where conrelid = 'public.clip_revisions'::regclass
      and contype = 'c'
      and pg_catalog.pg_get_constraintdef(oid) ilike '%action_type%'
  loop
    execute format('alter table public.clip_revisions drop constraint if exists %I', v_con.conname);
  end loop;
end
$$;

alter table public.clip_revisions
  add constraint clip_revisions_action_type_check
  check (action_type in (
    'initial', 'split', 'trim_left', 'trim_right', 'trim_edge',
    'delete_piece', 'ripple', 'undo', 'redo', 'reset_ai', 'auto_cut'
  ));

-- Heal any pre-0031 clips_cloud rows stuck with can_undo = true on a root revision (<= 1).
update public.clips_cloud c
   set can_undo = false
 where c.can_undo = true
   and c.active_state_revision <= 1
   and not exists (
     select 1
       from public.clip_revisions r
      where r.clip_id = c.id
        and r.revision = c.active_state_revision
        and r.state_parent_revision is not null
        and r.state_parent_revision >= 1
   );

-- Shared pure helpers for timeline hull and primary caption track projection.
create or replace function public._extract_project_doc_hull(p_doc jsonb)
returns table (hull_start numeric, hull_end numeric)
language sql
immutable
set search_path = pg_catalog, public
as $$
  with segs as (
    select
      case
        when pg_catalog.jsonb_typeof(seg) = 'object'
          then coalesce(
            nullif(seg->>'source_start', '')::numeric,
            nullif(seg->>'start', '')::numeric
          )
        when pg_catalog.jsonb_typeof(seg) = 'array'
          then nullif(seg->>0, '')::numeric
        else null::numeric
      end as s_start,
      case
        when pg_catalog.jsonb_typeof(seg) = 'object'
          then coalesce(
            nullif(seg->>'source_end', '')::numeric,
            nullif(seg->>'end', '')::numeric
          )
        when pg_catalog.jsonb_typeof(seg) = 'array'
          then nullif(seg->>1, '')::numeric
        else null::numeric
      end as s_end
    from pg_catalog.jsonb_array_elements(
      case
        when pg_catalog.jsonb_typeof(p_doc->'timeline'->'segments') = 'array'
          then p_doc->'timeline'->'segments'
        else '[]'::jsonb
      end
    ) as seg
  ),
  agg as (
    select
      min(s_start) filter (where s_start is not null and s_end is not null and s_start >= 0 and s_end > s_start) as min_start,
      max(s_end)   filter (where s_start is not null and s_end is not null and s_start >= 0 and s_end > s_start) as max_end
    from segs
  )
  select
    coalesce(min_start, nullif(p_doc->'timeline'->>'hull_start', '')::numeric) as hull_start,
    coalesce(max_end,   nullif(p_doc->'timeline'->>'hull_end', '')::numeric)   as hull_end
  from agg;
$$;

revoke all on function public._extract_project_doc_hull(jsonb) from public, authenticated, anon;
grant execute on function public._extract_project_doc_hull(jsonb) to service_role;

create or replace function public._extract_primary_captions(p_doc jsonb)
returns jsonb
language sql
immutable
set search_path = pg_catalog, public
as $$
  select track
    from pg_catalog.jsonb_array_elements(
      case
        when pg_catalog.jsonb_typeof(p_doc->'tracks') = 'array'
          then p_doc->'tracks'
        else '[]'::jsonb
      end
    ) as track
   where track->>'kind' = 'captions'
   order by coalesce((track->>'is_primary')::boolean, false) desc,
            (track->>'id') asc
   limit 1;
$$;

revoke all on function public._extract_primary_captions(jsonb) from public, authenticated, anon;
grant execute on function public._extract_primary_captions(jsonb) to service_role;

create or replace function public.cloud_put_project_document(
    p_user_id uuid,
    p_local_clip_id text,
    p_document jsonb,
    p_base_revision bigint,
    p_operation_id text,
    p_action_type text,
    p_action_metadata jsonb default null,
    p_author text default 'mobile'
)
returns table (revision bigint, conflict boolean, current_document jsonb)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_clip_id uuid;
    v_curr_rev bigint;
    v_curr_active bigint;
    v_curr_doc jsonb;
    v_rendered_fp text;
    v_old_start numeric;
    v_old_end numeric;
    v_old_caption jsonb;
    v_existing_rev bigint;
    v_existing_doc jsonb;
    v_media_fp text;
    v_render_fp text;
    v_kept_dur numeric;
    v_segments jsonb;
    v_primary_captions jsonb;
    v_hull_start numeric;
    v_hull_end numeric;
    v_hull_changed boolean := false;
    v_new_start numeric;
    v_new_end numeric;
    v_parent_rev bigint;
begin
    -- 1. Lock target clip row first to eliminate idempotency & CAS races
    select c.id, c.segments_revision, c.active_state_revision, c.project_document,
           c.rendered_fingerprint, c.start_time, c.end_time, c.caption_data
      into v_clip_id, v_curr_rev, v_curr_active, v_curr_doc,
           v_rendered_fp, v_old_start, v_old_end, v_old_caption
      from public.clips_cloud c
     where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
       for update;

    if v_clip_id is null then
        raise exception 'clip not found or unauthorized';
    end if;

    -- 2. Idempotency check while holding lock
    select r.revision, r.project_document into v_existing_rev, v_existing_doc
      from public.clip_revisions r
     where r.clip_id = v_clip_id and r.operation_id = p_operation_id;
    if v_existing_rev is not null then
        return query select v_existing_rev, false, v_existing_doc;
        return;
    end if;

    -- 3. Optimistic CAS check covering both uninitialized rev-0 and rev > 0
    if v_curr_rev = 0 then
        if p_base_revision is not null and p_base_revision <> 0 then
            return query select v_curr_rev, true, v_curr_doc;
            return;
        end if;
    elsif p_base_revision is null or p_base_revision <> v_curr_rev then
        return query select v_curr_rev, true, v_curr_doc;
        return;
    end if;

    -- 4. Server-side extraction & verification
    v_segments := p_document->'timeline'->'segments';
    v_kept_dur := nullif(p_document->'timeline'->>'kept_duration', '')::numeric;
    v_media_fp := coalesce(nullif(p_document->'metadata'->>'media_fingerprint', ''), '');
    v_render_fp := coalesce(nullif(p_document->'metadata'->>'render_fingerprint', ''), '');

    if v_kept_dur is null or v_kept_dur < 5.0 then
        raise exception 'Kept duration must be at least 5.0 seconds';
    end if;

    -- 5. Track & hull projection
    v_primary_captions := public._extract_primary_captions(p_document);
    select h.hull_start, h.hull_end into v_hull_start, v_hull_end
      from public._extract_project_doc_hull(p_document) h;

    if v_hull_start is not null and v_hull_end is not null
       and v_hull_start >= 0 and v_hull_end > v_hull_start then
        v_new_start := v_hull_start;
        v_new_end := v_hull_end;
        v_hull_changed := (v_new_start is distinct from v_old_start or v_new_end is distinct from v_old_end);
    else
        v_new_start := v_old_start;
        v_new_end := v_old_end;
    end if;

    -- Only record state_parent_revision when a real parent revision (>= 1) exists
    v_parent_rev := case
        when v_curr_active is not null and v_curr_active >= 1
         and exists (
             select 1 from public.clip_revisions pr
              where pr.clip_id = v_clip_id and pr.revision = v_curr_active
         ) then v_curr_active
        else null
    end;

    -- 6. Insert immutable revision log entry (forward edit resets redo_stack)
    insert into public.clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, redo_stack, operation_id, project_document,
        media_fingerprint, render_fingerprint, action_type, action_metadata, author
    ) values (
        v_clip_id, p_user_id, v_curr_rev + 1, nullif(v_curr_rev, 0), v_parent_rev,
        v_curr_rev + 1, '[]'::jsonb, p_operation_id, p_document,
        v_media_fp, v_render_fp, p_action_type, p_action_metadata, p_author
    );

    -- 7. Update cloud clip state & projections atomically
    update public.clips_cloud set
        project_document = p_document,
        segments_json = v_segments,
        caption_data = coalesce(v_primary_captions, caption_data),
        caption_revision = caption_revision + case
            when v_primary_captions is not null and v_primary_captions is distinct from v_old_caption then 1
            else 0
        end,
        caption_updated_at = case
            when v_primary_captions is not null and v_primary_captions is distinct from v_old_caption then now()
            else caption_updated_at
        end,
        kept_duration = v_kept_dur,
        start_time = v_new_start,
        end_time = v_new_end,
        range_revision = range_revision + case when v_hull_changed then 1 else 0 end,
        segments_revision = v_curr_rev + 1,
        active_state_revision = v_curr_rev + 1,
        redo_stack = '[]'::jsonb,
        can_undo = (v_parent_rev is not null),
        can_redo = false,
        media_fingerprint = v_media_fp,
        render_fingerprint = v_render_fp,
        render_stale = (v_rendered_fp is distinct from v_render_fp),
        cloud_updated_at = now()
    where id = v_clip_id;

    return query select v_curr_rev + 1, false, p_document;
end;
$$;

revoke all on function public.cloud_put_project_document(uuid, text, jsonb, bigint, text, text, jsonb, text) from public, authenticated, anon;
grant execute on function public.cloud_put_project_document(uuid, text, jsonb, bigint, text, text, jsonb, text) to service_role;

create or replace function public.cloud_undo_project_document(
    p_user_id uuid,
    p_local_clip_id text,
    p_operation_id text
)
returns table (revision bigint, conflict boolean, current_document jsonb)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_clip_id uuid;
    v_curr_rev bigint;
    v_curr_active bigint;
    v_curr_redo jsonb;
    v_can_undo boolean;
    v_rendered_fp text;
    v_old_start numeric;
    v_old_end numeric;
    v_old_caption jsonb;
    v_existing_rev bigint;
    v_existing_doc jsonb;
    v_target_rev bigint;
    v_target_doc jsonb;
    v_target_media_fp text;
    v_target_render_fp text;
    v_target_parent_rev bigint;
    v_target_segments jsonb;
    v_target_kept_dur numeric;
    v_target_captions jsonb;
    v_hull_start numeric;
    v_hull_end numeric;
    v_hull_changed boolean := false;
    v_new_start numeric;
    v_new_end numeric;
    v_new_redo jsonb;
    v_next_can_undo boolean := false;
begin
    -- 1. Lock clip row
    select c.id, c.segments_revision, c.active_state_revision, c.redo_stack, c.can_undo,
           c.rendered_fingerprint, c.start_time, c.end_time, c.caption_data
      into v_clip_id, v_curr_rev, v_curr_active, v_curr_redo, v_can_undo,
           v_rendered_fp, v_old_start, v_old_end, v_old_caption
      from public.clips_cloud c
     where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
       for update;

    if v_clip_id is null then
        raise exception 'clip not found';
    end if;

    -- 2. Idempotency check
    select r.revision, r.project_document into v_existing_rev, v_existing_doc
      from public.clip_revisions r
     where r.clip_id = v_clip_id and r.operation_id = p_operation_id;
    if v_existing_rev is not null then
        return query select v_existing_rev, false, v_existing_doc;
        return;
    end if;

    if not v_can_undo or v_curr_active is null or v_curr_active <= 0 then
        raise exception 'cannot undo: at oldest revision';
    end if;

    -- 3. Find logical target revision: state_parent of the active state
    select r.state_parent_revision into v_target_rev
      from public.clip_revisions r
     where r.clip_id = v_clip_id and r.revision = v_curr_active;

    -- Fail gracefully if active revision has no valid parent (>= 1)
    if v_target_rev is null or v_target_rev <= 0 then
        raise exception 'cannot undo: at oldest revision';
    end if;

    -- 4. Load target document
    select r.project_document, r.media_fingerprint, r.render_fingerprint, r.state_parent_revision
      into v_target_doc, v_target_media_fp, v_target_render_fp, v_target_parent_rev
      from public.clip_revisions r
     where r.clip_id = v_clip_id and r.revision = v_target_rev;

    if v_target_doc is null then
        raise exception 'cannot undo: target revision % not found', v_target_rev;
    end if;

    v_target_segments := v_target_doc->'timeline'->'segments';
    v_target_kept_dur := nullif(v_target_doc->'timeline'->>'kept_duration', '')::numeric;
    v_target_captions := public._extract_primary_captions(v_target_doc);

    select h.hull_start, h.hull_end into v_hull_start, v_hull_end
      from public._extract_project_doc_hull(v_target_doc) h;

    if v_hull_start is not null and v_hull_end is not null
       and v_hull_start >= 0 and v_hull_end > v_hull_start then
        v_new_start := v_hull_start;
        v_new_end := v_hull_end;
        v_hull_changed := (v_new_start is distinct from v_old_start or v_new_end is distinct from v_old_end);
    else
        v_new_start := v_old_start;
        v_new_end := v_old_end;
    end if;

    v_next_can_undo := (
        v_target_parent_rev is not null
        and v_target_parent_rev >= 1
        and exists (
            select 1 from public.clip_revisions pr
             where pr.clip_id = v_clip_id and pr.revision = v_target_parent_rev
        )
    );

    -- 5. Push current active state onto redo_stack
    v_new_redo := pg_catalog.jsonb_build_array(v_curr_active) || coalesce(v_curr_redo, '[]'::jsonb);

    -- 6. Insert undo revision record
    insert into public.clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, undo_target_revision, redo_stack, operation_id,
        project_document, media_fingerprint, render_fingerprint, action_type, author
    ) values (
        v_clip_id, p_user_id, v_curr_rev + 1, v_curr_rev, v_target_rev,
        v_target_rev, v_target_rev, v_new_redo, p_operation_id,
        v_target_doc, coalesce(v_target_media_fp, ''), coalesce(v_target_render_fp, ''), 'undo', 'mobile'
    );

    -- 7. Update clips_cloud (sync caption_data, start_time, end_time, range_revision)
    update public.clips_cloud set
        project_document = v_target_doc,
        segments_json = v_target_segments,
        caption_data = coalesce(v_target_captions, caption_data),
        caption_revision = caption_revision + case
            when v_target_captions is not null and v_target_captions is distinct from v_old_caption then 1
            else 0
        end,
        caption_updated_at = case
            when v_target_captions is not null and v_target_captions is distinct from v_old_caption then now()
            else caption_updated_at
        end,
        kept_duration = v_target_kept_dur,
        start_time = v_new_start,
        end_time = v_new_end,
        range_revision = range_revision + case when v_hull_changed then 1 else 0 end,
        segments_revision = v_curr_rev + 1,
        active_state_revision = v_target_rev,
        redo_stack = v_new_redo,
        can_undo = v_next_can_undo,
        can_redo = true,
        media_fingerprint = coalesce(v_target_media_fp, ''),
        render_fingerprint = coalesce(v_target_render_fp, ''),
        render_stale = (v_rendered_fp is distinct from coalesce(v_target_render_fp, '')),
        cloud_updated_at = now()
    where id = v_clip_id;

    return query select v_curr_rev + 1, false, v_target_doc;
end;
$$;

revoke all on function public.cloud_undo_project_document(uuid, text, text) from public, authenticated, anon;
grant execute on function public.cloud_undo_project_document(uuid, text, text) to service_role;

create or replace function public.cloud_redo_project_document(
    p_user_id uuid,
    p_local_clip_id text,
    p_operation_id text
)
returns table (revision bigint, conflict boolean, current_document jsonb)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_clip_id uuid;
    v_curr_rev bigint;
    v_curr_active bigint;
    v_curr_redo jsonb;
    v_can_redo boolean;
    v_rendered_fp text;
    v_old_start numeric;
    v_old_end numeric;
    v_old_caption jsonb;
    v_existing_rev bigint;
    v_existing_doc jsonb;
    v_target_rev bigint;
    v_target_doc jsonb;
    v_target_media_fp text;
    v_target_render_fp text;
    v_target_parent_rev bigint;
    v_target_segments jsonb;
    v_target_kept_dur numeric;
    v_target_captions jsonb;
    v_hull_start numeric;
    v_hull_end numeric;
    v_hull_changed boolean := false;
    v_new_start numeric;
    v_new_end numeric;
    v_new_redo jsonb;
    v_next_can_undo boolean := false;
begin
    -- 1. Lock clip row
    select c.id, c.segments_revision, c.active_state_revision, c.redo_stack, c.can_redo,
           c.rendered_fingerprint, c.start_time, c.end_time, c.caption_data
      into v_clip_id, v_curr_rev, v_curr_active, v_curr_redo, v_can_redo,
           v_rendered_fp, v_old_start, v_old_end, v_old_caption
      from public.clips_cloud c
     where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
       for update;

    if v_clip_id is null then
        raise exception 'clip not found';
    end if;

    -- 2. Idempotency check
    select r.revision, r.project_document into v_existing_rev, v_existing_doc
      from public.clip_revisions r
     where r.clip_id = v_clip_id and r.operation_id = p_operation_id;
    if v_existing_rev is not null then
        return query select v_existing_rev, false, v_existing_doc;
        return;
    end if;

    if not v_can_redo or v_curr_redo is null
       or pg_catalog.jsonb_typeof(v_curr_redo) <> 'array'
       or pg_catalog.jsonb_array_length(v_curr_redo) = 0 then
        raise exception 'cannot redo: redo stack is empty';
    end if;

    -- 3. Pop target revision from head of redo_stack
    v_target_rev := (v_curr_redo->>0)::bigint;
    v_new_redo := v_curr_redo - 0;

    if v_target_rev is null or v_target_rev <= 0 then
        raise exception 'cannot redo: invalid target revision';
    end if;

    -- 4. Load target document
    select r.project_document, r.media_fingerprint, r.render_fingerprint, r.state_parent_revision
      into v_target_doc, v_target_media_fp, v_target_render_fp, v_target_parent_rev
      from public.clip_revisions r
     where r.clip_id = v_clip_id and r.revision = v_target_rev;

    if v_target_doc is null then
        raise exception 'cannot redo: target redo revision % not found', v_target_rev;
    end if;

    v_target_segments := v_target_doc->'timeline'->'segments';
    v_target_kept_dur := nullif(v_target_doc->'timeline'->>'kept_duration', '')::numeric;
    v_target_captions := public._extract_primary_captions(v_target_doc);

    select h.hull_start, h.hull_end into v_hull_start, v_hull_end
      from public._extract_project_doc_hull(v_target_doc) h;

    if v_hull_start is not null and v_hull_end is not null
       and v_hull_start >= 0 and v_hull_end > v_hull_start then
        v_new_start := v_hull_start;
        v_new_end := v_hull_end;
        v_hull_changed := (v_new_start is distinct from v_old_start or v_new_end is distinct from v_old_end);
    else
        v_new_start := v_old_start;
        v_new_end := v_old_end;
    end if;

    v_next_can_undo := (
        v_target_parent_rev is not null
        and v_target_parent_rev >= 1
        and exists (
            select 1 from public.clip_revisions pr
             where pr.clip_id = v_clip_id and pr.revision = v_target_parent_rev
        )
    );

    -- 5. Insert redo revision record
    insert into public.clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, redo_of_revision, redo_stack, operation_id,
        project_document, media_fingerprint, render_fingerprint, action_type, author
    ) values (
        v_clip_id, p_user_id, v_curr_rev + 1, v_curr_rev, v_target_rev,
        v_target_rev, v_target_rev, v_new_redo, p_operation_id,
        v_target_doc, coalesce(v_target_media_fp, ''), coalesce(v_target_render_fp, ''), 'redo', 'mobile'
    );

    -- 6. Update clips_cloud (sync caption_data, start_time, end_time, range_revision)
    update public.clips_cloud set
        project_document = v_target_doc,
        segments_json = v_target_segments,
        caption_data = coalesce(v_target_captions, caption_data),
        caption_revision = caption_revision + case
            when v_target_captions is not null and v_target_captions is distinct from v_old_caption then 1
            else 0
        end,
        caption_updated_at = case
            when v_target_captions is not null and v_target_captions is distinct from v_old_caption then now()
            else caption_updated_at
        end,
        kept_duration = v_target_kept_dur,
        start_time = v_new_start,
        end_time = v_new_end,
        range_revision = range_revision + case when v_hull_changed then 1 else 0 end,
        segments_revision = v_curr_rev + 1,
        active_state_revision = v_target_rev,
        redo_stack = v_new_redo,
        can_undo = v_next_can_undo,
        can_redo = (pg_catalog.jsonb_array_length(v_new_redo) > 0),
        media_fingerprint = coalesce(v_target_media_fp, ''),
        render_fingerprint = coalesce(v_target_render_fp, ''),
        render_stale = (v_rendered_fp is distinct from coalesce(v_target_render_fp, '')),
        cloud_updated_at = now()
    where id = v_clip_id;

    return query select v_curr_rev + 1, false, v_target_doc;
end;
$$;

revoke all on function public.cloud_redo_project_document(uuid, text, text) from public, authenticated, anon;
grant execute on function public.cloud_redo_project_document(uuid, text, text) to service_role;

-- ============================================================================
-- §9.3–§9.4 Multi-Target Publishing Schema & Publications Normalization
-- ============================================================================
alter table public.publishing_jobs
  add column if not exists account_ids jsonb not null default '{}'::jsonb,
  add column if not exists attempt_count int not null default 0;

create table if not exists public.publishing_job_targets (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.publishing_jobs(id) on delete cascade,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  platform text not null check (platform in ('youtube', 'instagram', 'linkedin', 'twitter', 'facebook')),
  account_id text not null default '',
  status text not null default 'queued' check (status in (
    'queued', 'uploading', 'processing', 'published', 'failed', 'blocked_needs_auth', 'cancelled'
  )),
  attempt_count int not null default 0 check (attempt_count >= 0),
  next_retry_at timestamptz,
  remote_id text,
  remote_url text,
  last_error text,
  upload_session_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (job_id, platform, account_id)
);

-- Owner guard: a target row may never reference a publishing_jobs row owned by another user.
create or replace function public.publishing_job_targets_owner_guard()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if not exists (
    select 1
      from public.publishing_jobs j
     where j.id = new.job_id and j.user_id = new.user_id
  ) then
    raise exception 'publishing_job_targets: job_id must reference a publishing_jobs row owned by the same user';
  end if;
  return new;
end;
$$;

drop trigger if exists publishing_job_targets_owner_guard on public.publishing_job_targets;
create trigger publishing_job_targets_owner_guard
  before insert or update on public.publishing_job_targets
  for each row execute function public.publishing_job_targets_owner_guard();

revoke all on function public.publishing_job_targets_owner_guard() from public, anon, authenticated;

alter table public.publishing_job_targets enable row level security;

drop policy if exists "publishing_job_targets: own rows readable" on public.publishing_job_targets;
create policy "publishing_job_targets: own rows readable" on public.publishing_job_targets
  for select to authenticated using (user_id = auth.uid());

-- Fix public.publications: widen platform CHECK & support nullable clip_id + local_clip_id
do $$
declare
  v_con record;
begin
  for v_con in
    select conname
    from pg_catalog.pg_constraint
    where conrelid = 'public.publications'::regclass
      and contype = 'c'
      and pg_catalog.pg_get_constraintdef(oid) ilike '%platform%'
  loop
    execute format('alter table public.publications drop constraint if exists %I', v_con.conname);
  end loop;
end
$$;

alter table public.publications
  add constraint publications_platform_check
  check (platform in ('youtube', 'instagram', 'linkedin', 'twitter', 'facebook'));

alter table public.publications
  alter column clip_id drop not null;

alter table public.publications
  add column if not exists local_clip_id text;

do $$
begin
  alter table public.publications drop constraint if exists publications_clip_ref_check;
  alter table public.publications drop constraint if exists publications_local_clip_id_nonempty_check;
end
$$;

alter table public.publications
  add constraint publications_clip_ref_check
  check (clip_id is not null or local_clip_id is not null);

alter table public.publications
  add constraint publications_local_clip_id_nonempty_check
  check (local_clip_id is null or length(trim(local_clip_id)) > 0);

create or replace function public.publications_owner_guard()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if new.clip_id is not null then
    if not exists (
      select 1
        from public.clips_cloud c
       where c.id = new.clip_id and c.user_id = new.user_id
    ) then
      raise exception 'publications: clip_id must reference a clips_cloud row owned by the same user';
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.publications_owner_guard() from public, anon, authenticated;

-- Upgrade cloud_enqueue_publishing_job: preserve 8-arg call compatibility via optional 9th default arg,
-- drop prior overload to avoid PostgREST ambiguity, and seed publishing_job_targets atomically.
drop function if exists public.cloud_enqueue_publishing_job(uuid, text, text, text, jsonb, text, text, text);
drop function if exists public.cloud_enqueue_publishing_job(uuid, text, text, text, jsonb, text, text, text, jsonb);

create or replace function public.cloud_enqueue_publishing_job(
    p_user_id uuid,
    p_clip_id text,
    p_title text,
    p_description text,
    p_platforms jsonb,
    p_publish_mode text,
    p_r2_key text,
    p_rendered_fingerprint text,
    p_account_ids jsonb default '{}'::jsonb
)
returns table (id uuid, status text)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_job_id uuid;
    v_platforms jsonb;
    v_account_ids jsonb;
    v_plat_raw text;
    v_plat text;
    v_acct_val jsonb;
    v_acct_id text;
    v_inserted_targets int := 0;
begin
    v_platforms := case
        when p_platforms is not null and pg_catalog.jsonb_typeof(p_platforms) = 'array'
             and pg_catalog.jsonb_array_length(p_platforms) > 0
            then p_platforms
        else '["youtube"]'::jsonb
    end;
    v_account_ids := case
        when p_account_ids is not null and pg_catalog.jsonb_typeof(p_account_ids) = 'object'
            then p_account_ids
        else '{}'::jsonb
    end;

    insert into public.publishing_jobs (
        user_id, clip_id, title, description, platforms, publish_mode,
        r2_key, rendered_fingerprint, account_ids, status
    ) values (
        p_user_id, p_clip_id, p_title, coalesce(p_description, ''), v_platforms,
        coalesce(p_publish_mode, 'private'), p_r2_key, p_rendered_fingerprint, v_account_ids, 'queued'
    ) returning publishing_jobs.id into v_job_id;

    for v_plat_raw in
        select pg_catalog.jsonb_array_elements_text(v_platforms)
    loop
        v_plat := lower(trim(coalesce(v_plat_raw, '')));
        if v_plat = 'x' then
            v_plat := 'twitter';
        end if;
        if v_plat not in ('youtube', 'instagram', 'linkedin', 'twitter', 'facebook') then
            continue;
        end if;

        v_acct_val := coalesce(
            v_account_ids->v_plat,
            case when v_plat = 'twitter' then v_account_ids->'x' else null::jsonb end
        );

        if v_acct_val is not null and pg_catalog.jsonb_typeof(v_acct_val) = 'array'
           and pg_catalog.jsonb_array_length(v_acct_val) > 0 then
            for v_acct_id in
                select trim(coalesce(el, ''))
                  from pg_catalog.jsonb_array_elements_text(v_acct_val) as el
            loop
                insert into public.publishing_job_targets (
                    job_id, user_id, platform, account_id, status
                ) values (
                    v_job_id, p_user_id, v_plat, coalesce(v_acct_id, ''), 'queued'
                ) on conflict (job_id, platform, account_id) do nothing;
                v_inserted_targets := v_inserted_targets + 1;
            end loop;
        else
            v_acct_id := case
                when v_acct_val is not null and pg_catalog.jsonb_typeof(v_acct_val) = 'string'
                    then trim(coalesce(v_acct_val #>> '{}', ''))
                else ''
            end;
            insert into public.publishing_job_targets (
                job_id, user_id, platform, account_id, status
            ) values (
                v_job_id, p_user_id, v_plat, v_acct_id, 'queued'
            ) on conflict (job_id, platform, account_id) do nothing;
            v_inserted_targets := v_inserted_targets + 1;
        end if;
    end loop;

    if v_inserted_targets = 0 then
        raise exception 'at least one supported publishing platform is required';
    end if;

    return query select v_job_id, 'queued'::text;
end;
$$;

revoke all on function public.cloud_enqueue_publishing_job(uuid, text, text, text, jsonb, text, text, text, jsonb) from public, authenticated, anon;
grant execute on function public.cloud_enqueue_publishing_job(uuid, text, text, text, jsonb, text, text, text, jsonb) to service_role;

-- Upgrade cloud_claim_publishing_jobs with bounded limit, safe interval, and attempt cap
drop function if exists public.cloud_claim_publishing_jobs(int, int);
drop function if exists public.cloud_claim_publishing_jobs(int, int, int);

create or replace function public.cloud_claim_publishing_jobs(
    p_limit int default 5,
    p_stale_seconds int default 600,
    p_max_attempts int default 5
)
returns setof public.publishing_jobs
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_stale interval;
    v_max_attempts int;
    v_limit int;
begin
    v_stale := pg_catalog.make_interval(secs => greatest(30, least(coalesce(p_stale_seconds, 600), 86400)));
    v_max_attempts := greatest(1, least(coalesce(p_max_attempts, 5), 20));
    v_limit := greatest(1, least(coalesce(p_limit, 5), 50));

    -- Terminal-fail stale jobs that exhausted their retry budget instead of looping forever.
    update public.publishing_jobs j
       set status = 'failed',
           last_error = coalesce(nullif(j.last_error, ''), 'max publishing attempts exceeded'),
           updated_at = now()
     where j.status = 'processing'
       and j.updated_at < now() - v_stale
       and j.attempt_count >= v_max_attempts;

    return query
    with candidate as (
        select pj.id
        from public.publishing_jobs pj
        where pj.status = 'queued'
           or (
               pj.status = 'processing'
               and pj.updated_at < now() - v_stale
               and pj.attempt_count < v_max_attempts
           )
        order by pj.created_at asc
        limit v_limit
        for update skip locked
    )
    update public.publishing_jobs j
    set status = 'processing',
        attempt_count = j.attempt_count + 1,
        updated_at = now()
    from candidate c
    where j.id = c.id
    returning j.*;
end;
$$;

revoke all on function public.cloud_claim_publishing_jobs(int, int, int) from public, authenticated, anon;
grant execute on function public.cloud_claim_publishing_jobs(int, int, int) to service_role;

-- Atomic per-target claim & state transition RPC helpers
create or replace function public.cloud_claim_publishing_targets(
    p_job_id uuid default null,
    p_limit int default 10,
    p_max_attempts int default 5
)
returns setof public.publishing_job_targets
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_max_attempts int;
    v_limit int;
begin
    v_max_attempts := greatest(1, least(coalesce(p_max_attempts, 5), 20));
    v_limit := greatest(1, least(coalesce(p_limit, 10), 100));

    -- Mark any queued target whose attempt count already hit cap as failed.
    update public.publishing_job_targets t
       set status = 'failed',
           last_error = coalesce(nullif(t.last_error, ''), 'max publishing attempts exceeded'),
           next_retry_at = null,
           updated_at = now()
     where (p_job_id is null or t.job_id = p_job_id)
       and t.status = 'queued'
       and t.attempt_count >= v_max_attempts;

    return query
    with candidate as (
        select pt.id
          from public.publishing_job_targets pt
         where (p_job_id is null or pt.job_id = p_job_id)
           and pt.status = 'queued'
           and pt.attempt_count < v_max_attempts
           and (pt.next_retry_at is null or pt.next_retry_at <= now())
         order by pt.created_at asc
         limit v_limit
         for update skip locked
    )
    update public.publishing_job_targets t
       set status = 'uploading',
           attempt_count = t.attempt_count + 1,
           next_retry_at = null,
           updated_at = now()
      from candidate c
     where t.id = c.id
    returning t.*;
end;
$$;

revoke all on function public.cloud_claim_publishing_targets(uuid, int, int) from public, authenticated, anon;
grant execute on function public.cloud_claim_publishing_targets(uuid, int, int) to service_role;

create or replace function public.cloud_update_publishing_target(
    p_target_id uuid,
    p_status text,
    p_remote_id text default null,
    p_remote_url text default null,
    p_last_error text default null,
    p_upload_session_url text default null,
    p_next_retry_at timestamptz default null,
    p_increment_attempt boolean default false,
    p_max_attempts int default 5
)
returns public.publishing_job_targets
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_row public.publishing_job_targets;
    v_cap int;
    v_next_attempts int;
    v_final_status text;
    v_final_error text;
    v_final_retry timestamptz;
begin
    if p_status not in (
        'queued', 'uploading', 'processing', 'published', 'failed', 'blocked_needs_auth', 'cancelled'
    ) then
        raise exception 'TARGET: unsupported status %', p_status;
    end if;

    select * into v_row
      from public.publishing_job_targets t
     where t.id = p_target_id
       for update;
    if not found then
        raise exception 'TARGET: publishing target not found';
    end if;

    -- Idempotent terminal protection: never overwrite a confirmed publication.
    if v_row.status = 'published' and p_status <> 'published' then
        return v_row;
    end if;

    v_cap := greatest(1, least(coalesce(p_max_attempts, 5), 20));
    v_next_attempts := v_row.attempt_count + case when coalesce(p_increment_attempt, false) then 1 else 0 end;
    v_final_status := p_status;
    v_final_error := p_last_error;
    v_final_retry := p_next_retry_at;

    if p_status = 'queued' and v_next_attempts >= v_cap then
        v_final_status := 'failed';
        v_final_error := coalesce(nullif(p_last_error, ''), v_row.last_error, 'max publishing attempts exceeded');
        v_final_retry := null;
    elsif p_status in ('uploading', 'processing') and v_next_attempts > v_cap then
        v_final_status := 'failed';
        v_next_attempts := v_row.attempt_count;
        v_final_error := coalesce(nullif(p_last_error, ''), v_row.last_error, 'max publishing attempts exceeded');
        v_final_retry := null;
    end if;

    update public.publishing_job_targets t
       set status = v_final_status,
           attempt_count = v_next_attempts,
           remote_id = coalesce(p_remote_id, t.remote_id),
           remote_url = coalesce(p_remote_url, t.remote_url),
           last_error = case
               when v_final_status = 'published' then null
               else coalesce(v_final_error, t.last_error)
           end,
           upload_session_url = coalesce(p_upload_session_url, t.upload_session_url),
           next_retry_at = case
               when v_final_status = 'queued' then v_final_retry
               else null
           end,
           updated_at = now()
     where t.id = p_target_id
     returning * into v_row;

    return v_row;
end;
$$;

revoke all on function public.cloud_update_publishing_target(uuid, text, text, text, text, text, timestamptz, boolean, int) from public, authenticated, anon;
grant execute on function public.cloud_update_publishing_target(uuid, text, text, text, text, text, timestamptz, boolean, int) to service_role;

-- ============================================================================
-- §9.5 Grants, Search Path Hardening & Tenant-Isolated Legacy Backfill
-- ============================================================================
revoke all on public.clip_revisions from public, anon, authenticated;
revoke truncate on public.clip_revisions from public, anon, authenticated;
grant select on public.clip_revisions to authenticated;
grant all on public.clip_revisions to service_role;

revoke all on public.publishing_jobs from public, anon, authenticated;
revoke truncate on public.publishing_jobs from public, anon, authenticated;
grant select on public.publishing_jobs to authenticated;
grant all on public.publishing_jobs to service_role;

revoke all on public.publishing_job_targets from public, anon, authenticated;
revoke truncate on public.publishing_job_targets from public, anon, authenticated;
grant select on public.publishing_job_targets to authenticated;
grant all on public.publishing_job_targets to service_role;

-- Defense-in-depth: strip TRUNCATE across every public table for client roles.
revoke truncate on all tables in schema public from public, anon, authenticated;

-- Harden search_path on untouched 0030 SECURITY DEFINER function
alter function public.cloud_submit_direct_job(uuid, uuid, text)
  set search_path = pg_catalog, public;
revoke all on function public.cloud_submit_direct_job(uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.cloud_submit_direct_job(uuid, uuid, text) to service_role;

create or replace function public.backfill_legacy_clip_to_project_document(p_clip_id uuid)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    r record;
    v_source_asset_id text;
    v_coord_sys text;
    v_offset numeric := 0.0;
    v_raw_groups jsonb;
    v_migrated_groups jsonb := '[]'::jsonb;
    v_grp jsonb;
    v_migrated_words jsonb;
    v_w jsonb;
    v_dur numeric;
    v_media_fp text;
    v_render_fp text;
    v_doc jsonb;
    v_spans_str text;
    v_layers_hash text;
begin
    -- 1. Safely lock clips_cloud across outer join using FOR UPDATE OF c
    select c.*, s.youtube_video_id into r
      from public.clips_cloud c
      left join public.source_sessions s on s.id = c.session_id
     where c.id = p_clip_id and c.project_document is null
       for update of c;

    if r.id is null then return; end if;

    -- Stable master C3 source asset identifier
    v_source_asset_id := coalesce(r.youtube_video_id, r.session_id::text, r.local_clip_id);
    v_dur := round(r.end_time - r.start_time, 3);

    -- Inspect coordinate system safely (strictly tenant-scoped on edit_windows)
    v_coord_sys := coalesce(r.caption_data->>'coordinate_system', 'clip_relative');
    if v_coord_sys = 'clip_relative' then
        v_offset := coalesce(r.start_time, 0.0);
    elsif v_coord_sys = 'window_relative' then
        select ew.window_start into v_offset
          from public.edit_windows ew
         where ew.user_id = r.user_id
           and ew.local_clip_id = r.local_clip_id
           and ew.status = 'ready'
         order by ew.created_at desc limit 1;
        if v_offset is null then
            raise exception 'unambiguous edit window not found for window_relative clip %', r.local_clip_id;
        end if;
    elsif v_coord_sys = 'source_relative' then
        v_offset := 0.0;
    end if;

    -- Migrate caption groups & words preserving every original metadata field
    v_raw_groups := r.caption_data->'groups';
    if v_raw_groups is not null and pg_catalog.jsonb_typeof(v_raw_groups) = 'array' then
        for v_grp in select pg_catalog.jsonb_array_elements(v_raw_groups) loop
            v_migrated_words := '[]'::jsonb;
            if v_grp->'words' is not null and pg_catalog.jsonb_typeof(v_grp->'words') = 'array' then
                for v_w in select pg_catalog.jsonb_array_elements(v_grp->'words') loop
                    v_migrated_words := v_migrated_words || pg_catalog.jsonb_build_array(
                        v_w || pg_catalog.jsonb_build_object(
                            'start', round((v_w->>'start')::numeric + v_offset, 3),
                            'end', round((v_w->>'end')::numeric + v_offset, 3)
                        )
                    );
                end loop;
            end if;

            v_migrated_groups := v_migrated_groups || pg_catalog.jsonb_build_array(
                v_grp || pg_catalog.jsonb_build_object(
                    'start', round((v_grp->>'start')::numeric + v_offset, 3),
                    'end', round((v_grp->>'end')::numeric + v_offset, 3),
                    'words', v_migrated_words
                )
            );
        end loop;
    end if;

    -- Canonical millisecond spans and fingerprints
    v_spans_str := '[' || pg_catalog.to_char(round(r.start_time, 3), 'FM999999990.000') || '-' || pg_catalog.to_char(round(r.end_time, 3), 'FM999999990.000') || ']';
    v_media_fp := pg_catalog.encode(pg_catalog.sha256(('v1:media:' || v_source_asset_id || ':' || v_spans_str)::bytea), 'hex');

    v_layers_hash := pg_catalog.encode(pg_catalog.sha256(v_migrated_groups::text::bytea), 'hex');
    v_render_fp := pg_catalog.encode(pg_catalog.sha256(('v1:render:' || v_media_fp || ':portrait_9_16:0.500:' || v_layers_hash)::bytea), 'hex');

    -- Build authoritative project document
    v_doc := pg_catalog.jsonb_build_object(
        'schema_version', 1,
        'clip_id', r.local_clip_id,
        'source_asset_id', v_source_asset_id,
        'revision', 1,
        'timeline', pg_catalog.jsonb_build_object(
            'segments', pg_catalog.jsonb_build_array(
                pg_catalog.jsonb_build_object(
                    'id', 'seg-1',
                    'source_start', round(r.start_time, 3),
                    'source_end', round(r.end_time, 3)
                )
            ),
            'kept_duration', v_dur,
            'hull_start', round(r.start_time, 3),
            'hull_end', round(r.end_time, 3),
            'timebase', 'source_seconds'
        ),
        'tracks', pg_catalog.jsonb_build_array(
            pg_catalog.jsonb_build_object('id', 'track_video_main', 'kind', 'video', 'source_asset_id', v_source_asset_id),
            pg_catalog.jsonb_build_object(
                'id', 'track_captions_primary',
                'kind', 'captions',
                'is_primary', true,
                'coordinate_system', 'source_relative',
                'groups', v_migrated_groups
            )
        ),
        'layout', pg_catalog.jsonb_build_object('mode', 'fit_blur', 'crop_anchor_x', 0.5),
        'metadata', pg_catalog.jsonb_build_object(
            'media_fingerprint', v_media_fp,
            'render_fingerprint', v_render_fp,
            'action_type', 'initial'
        )
    );

    -- Insert baseline Revision 1
    insert into public.clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, redo_stack, operation_id, project_document,
        media_fingerprint, render_fingerprint, action_type, author
    ) values (
        r.id, r.user_id, 1, null, null,
        1, '[]'::jsonb, 'migration_' || r.id::text, v_doc,
        v_media_fp, v_render_fp, 'initial', 'system'
    ) on conflict (clip_id, revision) do nothing;

    -- Update cloud clip
    update public.clips_cloud set
        project_document = v_doc,
        segments_json = pg_catalog.jsonb_build_array(
            pg_catalog.jsonb_build_object(
                'id', 'seg-1',
                'source_start', round(r.start_time, 3),
                'source_end', round(r.end_time, 3)
            )
        ),
        kept_duration = v_dur,
        segments_revision = 1,
        active_state_revision = 1,
        redo_stack = '[]'::jsonb,
        can_undo = false,
        can_redo = false,
        media_fingerprint = v_media_fp,
        render_fingerprint = v_render_fp,
        rendered_fingerprint = case when not r.render_stale then v_render_fp else null end,
        cloud_updated_at = now()
    where id = r.id;
end;
$$;

revoke all on function public.backfill_legacy_clip_to_project_document(uuid) from public, authenticated, anon;
grant execute on function public.backfill_legacy_clip_to_project_document(uuid) to service_role;

-- ============================================================================
-- §9.6 YouTube URL Parsing in cloud_submit_job
-- ============================================================================
create or replace function public.cloud_submit_job(
  p_user_id uuid,
  p_url text,
  p_source_type text default 'youtube_import',
  p_session_id text default null
)
returns table (id uuid, status text)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
#variable_conflict use_column
declare
  v_job uuid;
  v_existing uuid;
  v_existing_status text;
  v_extracted_vid text;
  v_valid_source_type text;
begin
  v_valid_source_type := case
    when p_source_type in ('camera', 'youtube_import', 'direct_upload') then p_source_type
    else 'youtube_import'
  end;

  -- Supports ?v=, &v=, /shorts/, /live/, /embed/, youtu.be/
  v_extracted_vid := substring(
    p_url from '(?:[?&]v=|shorts/|live/|embed/|youtu\.be/)([A-Za-z0-9_-]{11})'
  );

  select pj.id, pj.status into v_existing, v_existing_status
    from public.processing_jobs pj
   where pj.user_id = p_user_id
     and pj.status in ('queued', 'claimed')
     and (
       pj.youtube_url = p_url
       or (v_extracted_vid is not null and pj.video_id = v_extracted_vid)
     )
   limit 1;
  if found then
    return query select v_existing as id, v_existing_status as status;
    return;
  end if;

  insert into public.processing_jobs (
    user_id,
    youtube_url,
    status,
    source,
    source_type,
    session_id,
    video_id
  )
  values (
    p_user_id,
    p_url,
    'queued',
    'youtube',
    v_valid_source_type,
    p_session_id,
    v_extracted_vid
  )
  returning processing_jobs.id into v_job;

  return query select v_job as id, 'queued'::text as status;
end;
$$;

revoke all on function public.cloud_submit_job(uuid, text, text, text) from public, authenticated, anon;
grant execute on function public.cloud_submit_job(uuid, text, text, text) to service_role;

-- ============================================================================
-- §9.7 High-Churn Operational & FK Indexes
-- ============================================================================
-- publishing_jobs
create index if not exists idx_pub_jobs_processing_updated
  on public.publishing_jobs (updated_at)
  where status = 'processing';
create index if not exists idx_pub_jobs_user_created
  on public.publishing_jobs (user_id, created_at desc);

-- publishing_job_targets
create index if not exists idx_pub_targets_job_status
  on public.publishing_job_targets (job_id, status);
create index if not exists idx_pub_targets_retry_queue
  on public.publishing_job_targets (next_retry_at, created_at)
  where status = 'queued';
create index if not exists idx_pub_targets_user_status
  on public.publishing_job_targets (user_id, status, updated_at desc);

-- clip_revisions
create index if not exists idx_clip_revisions_user_created
  on public.clip_revisions (user_id, created_at desc);

-- clips_cloud
create index if not exists idx_clips_cloud_session_id
  on public.clips_cloud (session_id)
  where session_id is not null;
create index if not exists idx_clips_cloud_user_updated
  on public.clips_cloud (user_id, cloud_updated_at desc);

-- media_objects
create index if not exists idx_media_objects_clip_id
  on public.media_objects (clip_id)
  where clip_id is not null;
create index if not exists idx_media_objects_user_active_created
  on public.media_objects (user_id, created_at desc)
  where status = 'active';
create index if not exists idx_media_objects_active_staging
  on public.media_objects (id)
  where status = 'active' and staging_key is not null;

-- upload_reservations
create index if not exists idx_upload_reservations_media_obj
  on public.upload_reservations (media_object_id);
create index if not exists idx_upload_reservations_user_status_expires
  on public.upload_reservations (user_id, expires_at)
  where status = 'pending';

-- edit_windows
create index if not exists idx_edit_windows_user_clip_created
  on public.edit_windows (user_id, local_clip_id, created_at desc);
create index if not exists idx_edit_windows_media_obj
  on public.edit_windows (media_object_id)
  where media_object_id is not null;

-- processing_jobs
create index if not exists idx_pjobs_claimed_at
  on public.processing_jobs (claimed_at)
  where status = 'claimed';
create index if not exists idx_pjobs_user_video
  on public.processing_jobs (user_id, video_id)
  where video_id is not null;
create index if not exists idx_pjobs_user_created
  on public.processing_jobs (user_id, created_at desc);

-- publications
create unique index if not exists publications_user_local_clip_platform_account_uidx
  on public.publications (user_id, local_clip_id, platform, account_id)
  where clip_id is null and local_clip_id is not null;
create index if not exists idx_publications_user_local_clip
  on public.publications (user_id, local_clip_id)
  where local_clip_id is not null;
create index if not exists idx_publications_user_published_at
  on public.publications (user_id, published_at desc);
