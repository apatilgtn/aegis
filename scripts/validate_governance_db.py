"""Run pending governance migrations and authorization checks, then roll back everything.

Set SUPABASE_DB_URL through the environment; never run migrations against a
production database by importing this module. This script does not persist data.
"""
from pathlib import Path
import os
import re
import uuid
import psycopg2


def validate(connection):
    with connection.cursor() as cur:
        for path in sorted([*Path('supabase/migrations').glob('2026110613*.sql'), *Path('supabase/migrations').glob('20261108*.sql'), *Path('supabase/migrations').glob('20261109*.sql')]):
            cur.execute("select 1 from information_schema.tables where table_schema='supabase_migrations' and table_name='schema_migrations'")
            exists = cur.fetchone()
            if exists:
                cur.execute('select 1 from supabase_migrations.schema_migrations where version=%s', (path.name.split('_')[0],))
                if cur.fetchone():
                    continue
            cur.execute(re.sub(r'(?mi)^\s*(begin|commit);\s*$', '', path.read_text()))
        cur.execute("select tenant_id,user_id from public.tenant_memberships where status='active' and role='platform_admin' limit 1")
        tenant, user = map(str, cur.fetchone())
        def claims(actor, role):
            cur.execute("select set_config('request.jwt.claims',%s,true)", ('{"sub":"'+actor+'","role":"'+role+'"}',))
        def denied(sql, args=()):
            cur.execute('savepoint rejection')
            try:
                cur.execute(sql, args)
            except psycopg2.Error:
                cur.execute('rollback to savepoint rejection')
            else:
                raise AssertionError('Expected permission or policy denial')
            finally:
                cur.execute('release savepoint rejection')
        claims(str(uuid.uuid4()), 'authenticated')
        cur.execute('set local role authenticated')
        cur.execute('select count(*) from public.governance_settings')
        assert cur.fetchone()[0] == 0, 'Non-member saw workspace settings'
        denied('select public.reserve_ai_budget(%s,100)', (tenant,))
        denied("select public.set_governance_control(%s,1,'writes_enabled',true)", (tenant,))
        cur.execute('reset role')
        claims(user,'authenticated')
        cur.execute('set local role authenticated')
        cur.execute('select public.reserve_ai_budget(%s,100)', (tenant,))
        reservation = str(cur.fetchone()[0])
        denied("select public.record_ai_usage(%s,'test/model',10,5,1,true,'validation')", (reservation,))
        denied('update public.governance_settings set writes_enabled=true where tenant_id=%s',(tenant,))
        denied('select public.reserve_ai_budget(%s,1000000)',(tenant,))
        cur.execute('reset role')
        claims(user,'service_role')
        cur.execute('set local role service_role')
        cur.execute("select public.record_ai_usage(%s,'test/model',10,5,1,true,'validation')", (reservation,))
        cur.execute('select reserved_tokens from public.ai_budget_reservations where id=%s',(reservation,))
        assert cur.fetchone()[0] == 15
        denied("select public.record_ai_usage(%s,'test/model',0,0,1,true,'validation')", (reservation,))
        cur.execute("select count(*) from public.audit_events where tenant_id=%s and action='ai.success' and metadata->>'feature'='validation'",(tenant,))
        assert cur.fetchone()[0] >= 1, 'Usage audit trigger did not run'
        cur.execute('reset role')
        cur.execute('select id from public.action_requests where tenant_id=%s limit 1',(tenant,))
        request=str(cur.fetchone()[0])
        cur.execute("insert into public.proposal_receipts(action_request_id,tenant_id,project_id,artifact_path,artifact_sha256,policy_sha256,expires_at) values(%s,%s,'validation','validation',%s,%s,now()) on conflict do nothing",(request,tenant,'a'*64,'b'*64))
        denied("update public.proposal_receipts set project_id='changed' where action_request_id=%s",(request,))
        cur.execute("select to_regclass('public.execution_check_events')")
        if cur.fetchone()[0]:
            cur.execute("insert into public.execution_check_events(tenant_id,action_request_id,plan_sha256,commit_sha,checkpoint,verdict,checks) values(%s,%s,%s,%s,'pre_apply','denied','[]') returning id",(tenant,request,'c'*64,'d'*40))
            evidence=str(cur.fetchone()[0])
            denied("update public.execution_check_events set verdict='authorized' where id=%s",(evidence,))
            claims(str(uuid.uuid4()),'authenticated');cur.execute('set local role authenticated')
            cur.execute('select count(*) from public.execution_check_events where id=%s',(evidence,))
            assert cur.fetchone()[0]==0,'Non-member saw execution evidence'
            cur.execute('reset role');claims(user,'authenticated');cur.execute('set local role authenticated')
            denied("insert into public.execution_check_events(tenant_id,action_request_id,plan_sha256,commit_sha,checkpoint,verdict,checks) values(%s,%s,%s,%s,'pre_apply','authorized','[]')",(tenant,request,'e'*64,'f'*40))
            cur.execute('reset role')
            cur.execute("select id,status from public.action_requests where tenant_id=%s and capability='access_grant' limit 1",(tenant,))
            access=cur.fetchone()
            if access:
                claims(user,'authenticated');cur.execute('set local role authenticated')
                cur.execute('select public.request_native_access_analysis(%s)',(access[0],))
                denied('select public.request_native_access_analysis(%s)',(access[0],))
                cur.execute('select status from public.action_requests where id=%s',(access[0],))
                assert cur.fetchone()[0]==access[1],'Read-only analysis changed request lifecycle'
                cur.execute('reset role')
                cur.execute("select j.id from public.workflow_jobs j join public.workflow_runs w on w.id=j.workflow_run_id where w.action_request_id=%s and j.job_type='analyze_access' order by j.created_at desc limit 1",(access[0],))
                analysis_job=str(cur.fetchone()[0])
                cur.execute("update public.workflow_jobs set status='leased',locked_by='assurance-validation',lease_until=now()+interval '1 minute' where id=%s",(analysis_job,))
                claims(user,'service_role');cur.execute('set local role service_role')
                denied("select public.complete_native_access_analysis(%s,'wrong-worker','{}')",(analysis_job,))
                cur.execute("select public.complete_native_access_analysis(%s,'assurance-validation',%s::jsonb)",(analysis_job,'{"analyzer":{"state":"partial"},"simulator":{"state":"unsupported"}}'))
                cur.execute('reset role')
                cur.execute('select status from public.action_requests where id=%s',(access[0],))
                assert cur.fetchone()[0]==access[1],'Analysis completion changed request lifecycle'
                cur.execute('select id from public.native_access_assessments where action_request_id=%s order by checked_at desc limit 1',(access[0],))
                assessment=str(cur.fetchone()[0])
                denied("update public.native_access_assessments set report='{}' where id=%s",(assessment,))
        from scripts.validate_assurance_db import validate as validate_assurance
        validate_assurance(cur,tenant,user)
        print('PASS: migrations, tenant isolation, admin controls, budget limits, service-only settlement, audit triggers, immutable receipts/checkpoints and read-only analysis enqueue')

if __name__ == '__main__':
    conn=psycopg2.connect(os.environ['SUPABASE_DB_URL'],sslmode='require',connect_timeout=15)
    try: validate(conn)
    finally: conn.rollback();conn.close()
