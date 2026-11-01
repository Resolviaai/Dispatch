-- 0026: Project Document Architecture and EDL v1 Revisions (Stage 2).
-- Single source of truth for clip timeline editing, persistent undo/redo state cursor,
-- and optimistic CAS concurrency for phone-only editing with headless PC execution.

-- 1. Extend clips_cloud with project document columns
alter table public.clips_cloud 
  add column if not exists project_document jsonb,
  add column if not exists segments_json jsonb,
  add column if not exists kept_duration numeric,
  add column if not exists segments_revision bigint not null default 0,
  add column if not exists active_state_revision bigint not null default 0,
  add column if not exists redo_stack jsonb not null default '[]'::jsonb,
  add column if not exists can_undo boolean not null default false,
  add column if not exists can_redo boolean not null default false,
  add column if not exists media_fingerprint text,
  add column if not exists render_fingerprint text,
  add column if not exists rendered_fingerprint text;

create index if not exists idx_clips_cloud_segments_rev 
  on public.clips_cloud (user_id, segments_revision desc);

-- 2. Create immutable append-only clip_revisions table
create table if not exists public.clip_revisions (
    id uuid primary key default gen_random_uuid(),
    clip_id uuid not null references public.clips_cloud(id) on delete cascade,
    user_id uuid not null references public.profiles(user_id) on delete cascade,
    revision bigint not null check (revision >= 1),
    log_parent_revision bigint,
    state_parent_revision bigint,
    active_state_revision bigint not null,
    undo_target_revision bigint,
    redo_of_revision bigint,
    redo_stack jsonb not null default '[]'::jsonb,
    operation_id text not null,
    project_document jsonb not null,
    media_fingerprint text not null,
    render_fingerprint text not null,
    action_type text not null check (
        action_type in ('initial', 'split', 'trim_left', 'trim_right', 'trim_edge', 'delete_piece', 'ripple', 'undo', 'redo', 'reset_ai')
    ),
    action_metadata jsonb,
    author text not null check (author in ('mobile', 'pc', 'ai', 'system')),
    created_at timestamptz not null default now(),
    unique (clip_id, revision),
    unique (clip_id, operation_id)
);

create index if not exists idx_clip_revisions_lookup 
  on public.clip_revisions (clip_id, revision desc);

-- Security: Clients cannot insert or mutate revisions directly
alter table public.clip_revisions enable row level security;

drop policy if exists "clip_revisions: users read own rows" on public.clip_revisions;
create policy "clip_revisions: users read own rows" on public.clip_revisions
  for select using (user_id = auth.uid());

revoke insert, update, delete on public.clip_revisions from public, anon, authenticated;
grant select on public.clip_revisions to authenticated, service_role;

-- 3. Atomic CAS Mutation RPC: cloud_put_project_document
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
language plpgsql security definer set search_path = public, pg_temp as $$
declare
    v_clip_id uuid;
    v_curr_rev bigint;
    v_curr_active bigint;
    v_curr_doc jsonb;
    v_rendered_fp text;
    v_existing_rev bigint;
    v_existing_doc jsonb;
    v_media_fp text;
    v_render_fp text;
    v_kept_dur numeric;
    v_segments jsonb;
    v_primary_captions jsonb;
begin
    -- 1. CRITICAL: Lock target clip row FIRST to eliminate idempotency races
    select c.id, c.segments_revision, c.active_state_revision, c.project_document, c.rendered_fingerprint
      into v_clip_id, v_curr_rev, v_curr_active, v_curr_doc, v_rendered_fp
      from clips_cloud c
     where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
       for update;

    if v_clip_id is null then
        raise exception 'clip not found or unauthorized';
    end if;

    -- 2. Idempotency Check while holding lock: return existing revision if operation_id matches
    select r.revision, r.project_document into v_existing_rev, v_existing_doc
      from clip_revisions r
     where r.clip_id = v_clip_id and r.operation_id = p_operation_id;
    if v_existing_rev is not null then
        return query select v_existing_rev, false, v_existing_doc;
        return;
    end if;

    -- 3. Optimistic CAS Check (Mandatory base revision for mutations)
    if v_curr_rev > 0 and (p_base_revision is null or p_base_revision <> v_curr_rev) then
        return query select v_curr_rev, true, v_curr_doc;
        return;
    end if;

    -- 4. Server-Side Trusted Extraction & Verification
    v_segments := p_document->'timeline'->'segments';
    v_kept_dur := (p_document->'timeline'->>'kept_duration')::numeric;
    v_media_fp := p_document->'metadata'->>'media_fingerprint';
    v_render_fp := p_document->'metadata'->>'render_fingerprint';

    if v_kept_dur is null or v_kept_dur < 5.0 then
        raise exception 'Kept duration must be at least 5.0 seconds';
    end if;

    -- 5. Track Projection: dynamically find PRIMARY caption track
    select track into v_primary_captions
      from jsonb_array_elements(p_document->'tracks') as track
     where track->>'kind' = 'captions'
     order by coalesce((track->>'is_primary')::boolean, false) desc,
              (track->>'id') asc
     limit 1;

    -- 6. Insert immutable revision log entry
    -- Branch Invalidation: forward edit resets redo_stack to empty
    insert into clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, redo_stack, operation_id, project_document,
        media_fingerprint, render_fingerprint, action_type, action_metadata, author
    ) values (
        v_clip_id, p_user_id, v_curr_rev + 1, v_curr_rev, v_curr_active,
        v_curr_rev + 1, '[]'::jsonb, p_operation_id, p_document,
        v_media_fp, v_render_fp, p_action_type, p_action_metadata, p_author
    );

    -- 7. Update cloud clip state and derive projections atomically
    update clips_cloud set
        project_document = p_document,
        segments_json = v_segments,
        caption_data = coalesce(v_primary_captions, caption_data),
        kept_duration = v_kept_dur,
        segments_revision = v_curr_rev + 1,
        active_state_revision = v_curr_rev + 1,
        redo_stack = '[]'::jsonb,
        can_undo = true,
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

-- 4. Atomic Undo RPC: cloud_undo_project_document
create or replace function public.cloud_undo_project_document(
    p_user_id uuid,
    p_local_clip_id text,
    p_operation_id text
)
returns table (revision bigint, conflict boolean, current_document jsonb)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
    v_clip_id uuid;
    v_curr_rev bigint;
    v_curr_active bigint;
    v_curr_redo jsonb;
    v_can_undo boolean;
    v_rendered_fp text;
    v_existing_rev bigint;
    v_existing_doc jsonb;
    v_target_rev bigint;
    v_target_doc jsonb;
    v_target_media_fp text;
    v_target_render_fp text;
    v_target_segments jsonb;
    v_target_kept_dur numeric;
    v_new_redo jsonb;
begin
    -- 1. Lock clip row
    select c.id, c.segments_revision, c.active_state_revision, c.redo_stack, c.can_undo, c.rendered_fingerprint
      into v_clip_id, v_curr_rev, v_curr_active, v_curr_redo, v_can_undo, v_rendered_fp
      from clips_cloud c
     where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
       for update;

    if v_clip_id is null then raise exception 'clip not found'; end if;

    -- 2. Idempotency Check
    select r.revision, r.project_document into v_existing_rev, v_existing_doc
      from clip_revisions r where r.clip_id = v_clip_id and r.operation_id = p_operation_id;
    if v_existing_rev is not null then
        return query select v_existing_rev, false, v_existing_doc;
        return;
    end if;

    if not v_can_undo then raise exception 'cannot undo: at oldest revision'; end if;

    -- 3. Find logical target revision: state_parent of the active state
    select r.state_parent_revision into v_target_rev
      from clip_revisions r
     where r.clip_id = v_clip_id and r.revision = v_curr_active;

    if v_target_rev is null then v_target_rev := 1; end if;

    -- 4. Load target document
    select r.project_document, r.media_fingerprint, r.render_fingerprint
      into v_target_doc, v_target_media_fp, v_target_render_fp
      from clip_revisions r
     where r.clip_id = v_clip_id and r.revision = v_target_rev;

    if v_target_doc is null then raise exception 'target revision document not found'; end if;

    v_target_segments := v_target_doc->'timeline'->'segments';
    v_target_kept_dur := (v_target_doc->'timeline'->>'kept_duration')::numeric;

    -- 5. Push current active state onto redo_stack
    v_new_redo := jsonb_build_array(v_curr_active) || coalesce(v_curr_redo, '[]'::jsonb);

    -- 6. Insert undo revision record
    insert into clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, undo_target_revision, redo_stack, operation_id,
        project_document, media_fingerprint, render_fingerprint, action_type, author
    ) values (
        v_clip_id, p_user_id, v_curr_rev + 1, v_curr_rev, v_target_rev,
        v_target_rev, v_target_rev, v_new_redo, p_operation_id,
        v_target_doc, v_target_media_fp, v_target_render_fp, 'undo', 'mobile'
    );

    -- 7. Update clips_cloud
    update clips_cloud set
        project_document = v_target_doc,
        segments_json = v_target_segments,
        kept_duration = v_target_kept_dur,
        segments_revision = v_curr_rev + 1,
        active_state_revision = v_target_rev,
        redo_stack = v_new_redo,
        can_undo = (v_target_rev > 1),
        can_redo = true,
        media_fingerprint = v_target_media_fp,
        render_fingerprint = v_target_render_fp,
        render_stale = (v_rendered_fp is distinct from v_target_render_fp),
        cloud_updated_at = now()
    where id = v_clip_id;

    return query select v_curr_rev + 1, false, v_target_doc;
end;
$$;

revoke all on function public.cloud_undo_project_document(uuid, text, text) from public, authenticated, anon;
grant execute on function public.cloud_undo_project_document(uuid, text, text) to service_role;

-- 5. Atomic Redo RPC: cloud_redo_project_document
create or replace function public.cloud_redo_project_document(
    p_user_id uuid,
    p_local_clip_id text,
    p_operation_id text
)
returns table (revision bigint, conflict boolean, current_document jsonb)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
    v_clip_id uuid;
    v_curr_rev bigint;
    v_curr_active bigint;
    v_curr_redo jsonb;
    v_can_redo boolean;
    v_rendered_fp text;
    v_existing_rev bigint;
    v_existing_doc jsonb;
    v_target_rev bigint;
    v_target_doc jsonb;
    v_target_media_fp text;
    v_target_render_fp text;
    v_target_segments jsonb;
    v_target_kept_dur numeric;
    v_new_redo jsonb;
begin
    -- 1. Lock clip row
    select c.id, c.segments_revision, c.active_state_revision, c.redo_stack, c.can_redo, c.rendered_fingerprint
      into v_clip_id, v_curr_rev, v_curr_active, v_curr_redo, v_can_redo, v_rendered_fp
      from clips_cloud c
     where c.user_id = p_user_id and c.local_clip_id = p_local_clip_id
       for update;

    if v_clip_id is null then raise exception 'clip not found'; end if;

    -- 2. Idempotency Check
    select r.revision, r.project_document into v_existing_rev, v_existing_doc
      from clip_revisions r where r.clip_id = v_clip_id and r.operation_id = p_operation_id;
    if v_existing_rev is not null then
        return query select v_existing_rev, false, v_existing_doc;
        return;
    end if;

    if not v_can_redo or jsonb_array_length(v_curr_redo) = 0 then
        raise exception 'cannot redo: redo stack is empty';
    end if;

    -- 3. Pop target revision from head of redo_stack
    v_target_rev := (v_curr_redo->>0)::bigint;
    v_new_redo := v_curr_redo - 0;

    -- 4. Load target document
    select r.project_document, r.media_fingerprint, r.render_fingerprint
      into v_target_doc, v_target_media_fp, v_target_render_fp
      from clip_revisions r
     where r.clip_id = v_clip_id and r.revision = v_target_rev;

    if v_target_doc is null then raise exception 'target redo revision document not found'; end if;

    v_target_segments := v_target_doc->'timeline'->'segments';
    v_target_kept_dur := (v_target_doc->'timeline'->>'kept_duration')::numeric;

    -- 5. Insert redo revision record
    insert into clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, redo_of_revision, redo_stack, operation_id,
        project_document, media_fingerprint, render_fingerprint, action_type, author
    ) values (
        v_clip_id, p_user_id, v_curr_rev + 1, v_curr_rev, v_target_rev,
        v_target_rev, v_target_rev, v_new_redo, p_operation_id,
        v_target_doc, v_target_media_fp, v_target_render_fp, 'redo', 'mobile'
    );

    -- 6. Update clips_cloud
    update clips_cloud set
        project_document = v_target_doc,
        segments_json = v_target_segments,
        kept_duration = v_target_kept_dur,
        segments_revision = v_curr_rev + 1,
        active_state_revision = v_target_rev,
        redo_stack = v_new_redo,
        can_undo = true,
        can_redo = (jsonb_array_length(v_new_redo) > 0),
        media_fingerprint = v_target_media_fp,
        render_fingerprint = v_target_render_fp,
        render_stale = (v_rendered_fp is distinct from v_target_render_fp),
        cloud_updated_at = now()
    where id = v_clip_id;

    return query select v_curr_rev + 1, false, v_target_doc;
end;
$$;

revoke all on function public.cloud_redo_project_document(uuid, text, text) from public, authenticated, anon;
grant execute on function public.cloud_redo_project_document(uuid, text, text) to service_role;

-- 6. Deterministic Legacy Backfill Function with FOR UPDATE OF c
create or replace function public.backfill_legacy_clip_to_project_document(p_clip_id uuid)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
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
      from clips_cloud c
      left join source_sessions s on s.id = c.session_id
     where c.id = p_clip_id and c.project_document is null
       for update of c;

    if r.id is null then return; end if;

    -- Stable master C3 source asset identifier
    v_source_asset_id := coalesce(r.youtube_video_id, r.session_id::text, r.local_clip_id);
    v_dur := round(r.end_time - r.start_time, 3);

    -- Inspect coordinate system safely
    v_coord_sys := coalesce(r.caption_data->>'coordinate_system', 'clip_relative');
    if v_coord_sys = 'clip_relative' then
        v_offset := coalesce(r.start_time, 0.0);
    elsif v_coord_sys = 'window_relative' then
        select window_start into v_offset
          from edit_windows
         where local_clip_id = r.local_clip_id and status = 'ready'
         order by created_at desc limit 1;
        if v_offset is null then
            raise exception 'unambiguous edit window not found for window_relative clip %', r.local_clip_id;
        end if;
    elsif v_coord_sys = 'source_relative' then
        v_offset := 0.0;
    end if;

    -- Migrate caption groups & words preserving every original metadata field
    v_raw_groups := r.caption_data->'groups';
    if v_raw_groups is not null and jsonb_typeof(v_raw_groups) = 'array' then
        for v_grp in select jsonb_array_elements(v_raw_groups) loop
            v_migrated_words := '[]'::jsonb;
            if v_grp->'words' is not null and jsonb_typeof(v_grp->'words') = 'array' then
                for v_w in select jsonb_array_elements(v_grp->'words') loop
                    v_migrated_words := v_migrated_words || jsonb_build_array(
                        v_w || jsonb_build_object(
                            'start', round((v_w->>'start')::numeric + v_offset, 3),
                            'end', round((v_w->>'end')::numeric + v_offset, 3)
                        )
                    );
                end loop;
            end if;

            v_migrated_groups := v_migrated_groups || jsonb_build_array(
                v_grp || jsonb_build_object(
                    'start', round((v_grp->>'start')::numeric + v_offset, 3),
                    'end', round((v_grp->>'end')::numeric + v_offset, 3),
                    'words', v_migrated_words
                )
            );
        end loop;
    end if;

    -- Canonical millisecond spans and fingerprints
    v_spans_str := '[' || to_char(round(r.start_time, 3), 'FM999999990.000') || '-' || to_char(round(r.end_time, 3), 'FM999999990.000') || ']';
    v_media_fp := encode(sha256(('v1:media:' || v_source_asset_id || ':' || v_spans_str)::bytea), 'hex');

    v_layers_hash := encode(sha256(v_migrated_groups::text::bytea), 'hex');
    v_render_fp := encode(sha256(('v1:render:' || v_media_fp || ':portrait_9_16:0.500:' || v_layers_hash)::bytea), 'hex');

    -- Build authoritative project document
    v_doc := jsonb_build_object(
        'schema_version', 1,
        'clip_id', r.local_clip_id,
        'source_asset_id', v_source_asset_id,
        'revision', 1,
        'timeline', jsonb_build_object(
            'segments', jsonb_build_array(jsonb_build_object('id', 'seg-1', 'source_start', round(r.start_time, 3), 'source_end', round(r.end_time, 3))),
            'kept_duration', v_dur,
            'hull_start', round(r.start_time, 3),
            'hull_end', round(r.end_time, 3),
            'timebase', 'source_seconds'
        ),
        'tracks', jsonb_build_array(
            jsonb_build_object('id', 'track_video_main', 'kind', 'video', 'source_asset_id', v_source_asset_id),
            jsonb_build_object(
                'id', 'track_captions_primary',
                'kind', 'captions',
                'is_primary', true,
                'coordinate_system', 'source_relative',
                'groups', v_migrated_groups
            )
        ),
        'layout', jsonb_build_object('mode', 'fit_blur', 'crop_anchor_x', 0.5),
        'metadata', jsonb_build_object(
            'media_fingerprint', v_media_fp,
            'render_fingerprint', v_render_fp,
            'action_type', 'initial'
        )
    );

    -- Insert baseline Revision 1
    insert into clip_revisions (
        clip_id, user_id, revision, log_parent_revision, state_parent_revision,
        active_state_revision, redo_stack, operation_id, project_document,
        media_fingerprint, render_fingerprint, action_type, author
    ) values (
        r.id, r.user_id, 1, null, null,
        1, '[]'::jsonb, 'migration_' || r.id::text, v_doc,
        v_media_fp, v_render_fp, 'initial', 'system'
    ) on conflict (clip_id, revision) do nothing;

    -- Update cloud clip
    update clips_cloud set
        project_document = v_doc,
        segments_json = jsonb_build_array(jsonb_build_object('id', 'seg-1', 'source_start', round(r.start_time, 3), 'source_end', round(r.end_time, 3))),
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
