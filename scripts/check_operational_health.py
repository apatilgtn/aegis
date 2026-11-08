#!/usr/bin/env python3
"""Read-only database health checks suitable for external scheduled monitoring. Nonzero means investigate."""
import json
import os
import psycopg2


def check(connection):
    connection.set_session(readonly=True)
    with connection.cursor() as q:
        q.execute("set local statement_timeout='15s'")
        queries={
            'expired_job_leases':"select count(*) from public.workflow_jobs where status='leased' and lease_until<now()-interval '5 minutes'",
            'old_queued_jobs':"select count(*) from public.workflow_jobs where status='queued' and available_at<now()-interval '15 minutes'",
            'recent_terminal_failures':"select count(*) from public.workflow_jobs where status in ('failed','dead_letter') and updated_at>now()-interval '1 hour'",
            'overdue_assurance_monitors':"select count(*) from public.assurance_monitors where enabled and next_check_at<now()-interval '5 minutes'",
            'unresolved_cloud_mismatches':"select count(*) from public.change_verifications where state='failed'",
            'stale_inventory_sources':"select count(*) from public.tenant_cloud_accounts a where a.enabled and a.cloud='gcp' and not exists(select 1 from public.cloud_source_health h where h.tenant_id=a.tenant_id and h.project_id=a.project_id and h.source='inventory' and h.state='healthy' and h.checked_at>now()-interval '30 minutes')",
        }
        checks={}
        for name,query in queries.items():q.execute(query);checks[name]=q.fetchone()[0]
    return {'state':'attention' if any(checks.values()) else 'healthy','checks':checks,'limitations':'Database workflow health only; externally monitor portal availability and delivery of alerts separately.'}

if __name__=='__main__':
    c=psycopg2.connect(os.environ['SUPABASE_DB_URL'],sslmode='require',connect_timeout=15)
    try:report=check(c);print(json.dumps(report,indent=2))
    finally:c.rollback();c.close()
    raise SystemExit(0 if report['state']=='healthy' else 1)
