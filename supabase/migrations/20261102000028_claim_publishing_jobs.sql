-- 0028: Atomic Publishing Queue Claiming & Crash Recovery
-- Prevents race conditions and duplicate publishing across concurrent workers
-- using FOR UPDATE SKIP LOCKED, and reclaims stale in-flight jobs.

create or replace function public.cloud_claim_publishing_jobs(
    p_limit int default 5,
    p_stale_seconds int default 600
) returns setof public.publishing_jobs language plpgsql security definer set search_path = public as $$
begin
    return query
    with candidate as (
        select id
        from public.publishing_jobs
        where status = 'queued'
           or (status = 'processing' and updated_at < now() - (p_stale_seconds || ' seconds')::interval)
        order by created_at asc
        limit p_limit
        for update skip locked
    )
    update public.publishing_jobs j
    set status = 'processing',
        updated_at = now()
    from candidate c
    where j.id = c.id
    returning j.*;
end;
$$;

revoke all on function public.cloud_claim_publishing_jobs(int, int) from public, authenticated, anon;
grant execute on function public.cloud_claim_publishing_jobs(int, int) to service_role;
