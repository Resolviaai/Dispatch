-- Fix cloud_submit_job ambiguous column reference (SQLSTATE 42702)
create or replace function public.cloud_submit_job(p_user_id uuid, p_url text)
returns table (id uuid, status text)
language plpgsql security definer set search_path = public as $$
#variable_conflict use_column
declare
  v_job uuid;
  v_existing uuid;
begin
  -- dedupe: one active job per identical URL per user
  select pj.id into v_existing from public.processing_jobs pj
    where pj.user_id = p_user_id and pj.youtube_url = p_url and pj.status in ('queued','claimed')
    limit 1;
  if found then
    return query select v_existing as id, 'queued'::text as status;
    return;
  end if;
  insert into public.processing_jobs (user_id, youtube_url) values (p_user_id, p_url)
    returning processing_jobs.id into v_job;
  return query select v_job as id, 'queued'::text as status;
end;
$$;

revoke all on function public.cloud_submit_job(uuid, text) from public, authenticated, anon;
grant execute on function public.cloud_submit_job(uuid, text) to service_role;
