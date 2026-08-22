#!/bin/bash
# Final reconciliation: every analytics view vs its source table.
# METHOD: serving role aegis_app, app.tenant_id bound to a clean numeric.
export PGPASSWORD='dqQlz5sFcwhW4GPw2hNUv_sMEdNo_Sm3'
T="$1"
psql -U aegis_app -h localhost -d taif_run1 -tA -F'|' -c "
SET app.tenant_id='$T';
select 'runs_daily.runs',        (select coalesce(sum(runs),0) from analytics_runs_daily),        (select count(*) from runs where started_at is not null)
union all select 'runs_daily.cost',    (select coalesce(sum(cost_usd),0)::numeric(20,8) from analytics_runs_daily), (select coalesce(sum(cost_usd),0)::numeric(20,8) from runs where started_at is not null)
union all select 'runs_daily.cachehit',(select coalesce(sum(cache_hits),0) from analytics_runs_daily), (select count(*) from runs where cache_hit and started_at is not null)
union all select 'spend_daily.calls',  (select coalesce(sum(calls),0) from analytics_spend_daily),  (select count(*) from usage_ledger)
union all select 'spend_daily.cost',   (select coalesce(sum(cost_usd),0)::numeric(20,8) from analytics_spend_daily), (select coalesce(sum(cost_usd),0)::numeric(20,8) from usage_ledger)
union all select 'spend_daily.ptok',   (select coalesce(sum(prompt_tokens),0) from analytics_spend_daily), (select coalesce(sum(prompt_tokens),0) from usage_ledger)
union all select 'approvals.gates',    (select coalesce(sum(gates),0) from analytics_approvals_daily), (select count(*) from approvals)
union all select 'audit.events',       (select coalesce(sum(events),0) from analytics_audit_daily), (select count(*) from audit_log)
union all select 'jobs.jobs',          (select coalesce(sum(jobs),0) from analytics_jobs_daily),   (select count(*) from job_runs)
union all select 'redteam.rows',       (select count(*) from analytics_redteam_runs),              (select count(*) from redteam_runs)
;" | awk -F'|' '{m=($2==$3)?"MATCH":"** MISMATCH **"; printf "%-22s view=%-16s source=%-16s %s\n",$1,$2,$3,m}'
