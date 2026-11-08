"""Transaction-only assertions for continuous assurance and reviewer authorization.

Called from validate_governance_db; its caller always rolls the transaction back.
"""
import json
import uuid


def validate(cur,tenant,user):
    def claims(actor,role='authenticated'):
        cur.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':actor,'role':role}),))
    def denied(sql,args=()):
        cur.execute('savepoint assurance_denial')
        try:cur.execute(sql,args)
        except Exception:cur.execute('rollback to savepoint assurance_denial')
        else:raise AssertionError('Expected denial: '+sql)
        finally:cur.execute('release savepoint assurance_denial')
    claims(str(uuid.uuid4()));cur.execute('set local role authenticated')
    denied('select public.reviewer_inbox(%s)',(tenant,))
    denied('select public.workspace_value_metrics(%s)',(tenant,))
    denied('select public.workspace_members(%s)',(tenant,))
    cur.execute('reset role');claims(user);cur.execute('set local role authenticated')
    for sql in ['select * from public.reviewer_inbox(%s)','select public.workspace_value_metrics(%s)','select * from public.workspace_members(%s)']:
        cur.execute(sql,(tenant,));cur.fetchall()
    denied('select public.suspend_workspace_member(%s,%s)',(tenant,user))
    denied('select public.schedule_assurance_checks()')
    cur.execute('reset role');claims(user,'service_role')
    request=str(uuid.uuid4())
    cur.execute("insert into public.action_requests(id,tenant_id,requester_id,beneficiary_id,service_id,capability,resource,justification,status,idempotency_key,policy_decision) select %s,tenant_id,requester_id,beneficiary_id,service_id,'access_grant',resource,'Rollback assurance validation','completed',%s,jsonb_build_object('proposed_expires_at',now()+interval '1 day') from public.action_requests where tenant_id=%s and capability='access_grant' limit 1",(request,str(uuid.uuid4()),tenant))
    assert cur.rowcount==1,'An existing access fixture is required'
    cur.execute("insert into public.proposal_receipts(action_request_id,tenant_id,project_id,artifact_path,artifact_sha256,policy_sha256,expires_at,manifest,manifest_sha256) values(%s,%s,'validation','validation',%s,%s,now()+interval '1 hour','{}',%s)",(request,tenant,'a'*64,'b'*64,'c'*64))
    cur.execute("insert into public.change_verifications(action_request_id,tenant_id,state) values(%s,%s,'verified')",(request,tenant))
    claims(user);cur.execute('set local role authenticated')
    cur.execute("select public.coordinate_request(%s,now()+interval '1 day')",(request,))
    denied("select public.coordinate_request(%s,now()-interval '1 day')",(request,))
    cur.execute('select public.set_assurance_monitor(%s,true,5)',(request,))
    cur.execute("select public.record_operator_effort(%s,5,'Reviewed the exact pilot scope')",(request,))
    cur.execute('reset role');claims(user,'service_role');cur.execute('set local role service_role')
    cur.execute('select public.schedule_assurance_checks()')
    cur.execute('select public.schedule_assurance_checks()')
    cur.execute("select count(*) from public.workflow_jobs j join public.workflow_runs w on w.id=j.workflow_run_id where w.action_request_id=%s and j.job_type='verify_change'",(request,))
    assert cur.fetchone()[0]==1,'Scheduler created duplicate read jobs'
    cur.execute("select j.id from public.workflow_jobs j join public.workflow_runs w on w.id=j.workflow_run_id where w.action_request_id=%s and j.job_type='verify_change'",(request,))
    job=str(cur.fetchone()[0])
    cur.execute("update public.workflow_jobs set status='leased',locked_by='validation',attempts=max_attempts where id=%s",(job,))
    cur.execute("select public.fail_workflow_job(%s,'validation','Read unavailable')",(job,))
    cur.execute('select status from public.action_requests where id=%s',(request,))
    assert cur.fetchone()[0]=='completed','Failed read overwrote successful delivery'
    for state in ['failed','unavailable','verified','expired']:
        cur.execute('update public.change_verifications set state=%s,checked_at=clock_timestamp() where action_request_id=%s',(state,request))
    cur.execute('select count(*) from public.verification_observations where action_request_id=%s',(request,))
    assert cur.fetchone()[0]==5,'Observation history was overwritten'
    cur.execute("select count(*) from public.audit_events where object_id=%s and action='assurance.recovered'",(request,))
    assert cur.fetchone()[0]==1,'Recovery evidence was not recorded'
    cur.execute('select enabled from public.assurance_monitors where action_request_id=%s',(request,))
    assert cur.fetchone()[0] is False,'Expired grant monitoring did not stop'
    denied("update public.verification_observations set state='verified' where action_request_id=%s",(request,))
    cur.execute('reset role')
    member=str(uuid.uuid4())
    cur.execute('insert into auth.users(id,email) values(%s,%s)',(member,'assurance-'+member+'@example.invalid'))
    cur.execute("insert into public.tenant_memberships(tenant_id,user_id,role,status) values(%s,%s,'developer','active')",(tenant,member))
    claims(user);cur.execute('set local role authenticated')
    cur.execute('select public.suspend_workspace_member(%s,%s)',(tenant,member))
    cur.execute('reset role');claims(member);cur.execute('set local role authenticated')
    cur.execute('select count(*) from public.action_requests where tenant_id=%s',(tenant,))
    assert cur.fetchone()[0]==0,'Suspended member retained workspace visibility'
    denied('select public.reviewer_inbox(%s)',(tenant,))
    cur.execute('reset role')
    print('PASS: reviewer RPCs, ownership, metrics, self-suspension denial, scheduler deduplication, mismatch/recovery history and failed-read lifecycle isolation')
