-- Fix cloud_claim_job ambiguous user_id column references
create or replace function public.cloud_claim_job(p_worker_id text)
returns table (job_id uuid, user_id uuid, youtube_url text)
language plpgsql security definer set search_path = public as $$
declare
  v_job uuid;
  v_recent_same_user uuid;
  v_recent_count int;
begin
  -- Step A: Reclaim leases older than 45 minutes
  update processing_jobs pj
  set status = 'queued', claimed_at = null
  where pj.status = 'claimed' and pj.claimed_at < now() - interval '45 minutes';

  -- Step B: Check fairness: has the same user claimed the last 3 consecutive jobs?
  select pj_agg.user_id, count(*) into v_recent_same_user, v_recent_count
  from (
    select pj_inner.user_id from processing_jobs pj_inner
    where pj_inner.status in ('claimed', 'done')
    order by pj_inner.claimed_at desc nulls last limit 3
  ) pj_agg
  group by pj_agg.user_id
  having count(*) >= 3;

  -- If same user had 3 consecutive claims AND there are jobs from OTHER users queued,
  -- prioritize other users to prevent starvation.
  if v_recent_count >= 3 and exists (
    select 1 from processing_jobs pj_q where pj_q.status = 'queued' and pj_q.user_id != v_recent_same_user
  ) then
    update processing_jobs pj_target set status = 'claimed', claimed_at = now()
    where pj_target.id = (
      select j.id from processing_jobs j
      join profiles p on p.user_id = j.user_id
      where j.status = 'queued' and j.user_id != v_recent_same_user
      order by p.queue_priority asc, j.created_at asc limit 1 for update skip locked
    )
    returning pj_target.id into v_job;
  else
    update processing_jobs pj_target set status = 'claimed', claimed_at = now()
    where pj_target.id = (
      select j.id from processing_jobs j
      join profiles p on p.user_id = j.user_id
      where j.status = 'queued'
      order by p.queue_priority asc, j.created_at asc limit 1 for update skip locked
    )
    returning pj_target.id into v_job;
  end if;

  if v_job is null then return; end if;
  return query select pj_res.id, pj_res.user_id, pj_res.youtube_url from processing_jobs pj_res where pj_res.id = v_job;
end;
$$;

revoke all on function public.cloud_claim_job(text) from public, authenticated, anon;
grant execute on function public.cloud_claim_job(text) to service_role;
