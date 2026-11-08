#!/usr/bin/env python3
"""Restore a consistent logical governance backup into an offline disposable Docker database.

Source is read-only. Auth credentials/sessions, vaults and cloud connections are
excluded. No host port is published. Only hashes and validation results survive.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path
from scripts.restore_drill import fingerprint,compare

# Explicit scope: never export arbitrary tables from a shared public schema.
APP_TABLES=('action_requests','ai_budget_reservations','ai_usage_events','approvals','assurance_monitors','audit_events','audit_export_state','capability_policies','change_assessments','change_verifications','cloud_snapshots','cloud_source_health','execution_check_events','governance_settings','native_access_assessments','operator_effort','plan_approvals','proposal_receipts','request_coordination','service_entitlements','services','tenant_cloud_accounts','tenant_memberships','tenants','triage_change_context','verification_observations','workflow_events','workflow_jobs','workflow_runs')
APP_FUNCTIONS=('ai_budget_status','claim_workflow_jobs','complete_change_verification','complete_github_proposal','complete_native_access_analysis','complete_policy_evaluation','configure_entitlement_scope','coordinate_request','decide_incident','defer_governance_job','fail_workflow_job','heartbeat_workflow_job','prevent_immutable_event_mutation','record_ai_usage','record_github_delivery','record_log_search','record_operator_effort','record_servicenow_approval','record_servicenow_incident','request_change_verification','request_native_access_analysis','reserve_ai_budget','retry_read_job','reviewer_inbox','schedule_assurance_checks','set_ai_routing','set_assurance_monitor','set_capability_policy','set_governance_control','set_governance_limit','set_service_ownership','set_updated_at','suspend_workspace_member','validate_approval','workspace_members','workspace_value_metrics')


def validate_image(image):
    if not re.fullmatch(r'(?:postgres|docker\.io/library/postgres|mirror\.gcr\.io/library/postgres)(?::17(?:\.[0-9]+)?(?:-alpine[0-9.]*)?|@sha256:[a-f0-9]{64})',image):
        raise ValueError('Use an official PostgreSQL 17 image or its Google mirror, preferably pinned by digest')


def backup_command(image,envfile,snapshot,exporter):
    validate_image(image)
    return ['docker','run','--rm','--name',exporter,'--env-file',str(envfile),image,'pg_dump','--format=custom','--no-owner','--no-publications','--no-subscriptions','--snapshot='+snapshot,*['--table=public.'+t for t in (*APP_TABLES,'profiles')],'--exclude-table-data=public.profiles']


def main():
    import psycopg2
    from psycopg2.extensions import parse_dsn
    from psycopg2 import sql
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True)
    parser.add_argument('--image',default='postgres:17-alpine')
    args=parser.parse_args()
    validate_image(args.image)
    report_path=Path(args.report)
    if report_path.exists():raise ValueError('Report already exists; choose a new file')
    source=os.environ['SUPABASE_DB_URL']
    stage=Path(tempfile.mkdtemp(prefix='aegis-restore-'));os.chmod(stage,0o700)
    container='aegis-restore-'+uuid.uuid4().hex[:12]
    exporter=container+'-export'
    started=datetime.now(timezone.utc);timer=time.monotonic();created=False
    c=psycopg2.connect(source,sslmode='require',connect_timeout=15)
    c.set_session(readonly=True,isolation_level='REPEATABLE READ')
    log=open(stage/'private.log','wb')
    def run(argv,*,input=None,stdout=subprocess.PIPE):
        result=subprocess.run(argv,input=input,stdout=stdout,stderr=log,timeout=300)
        if result.returncode:
            log.flush()
            errors=[line for line in (stage/'private.log').read_text(errors='replace').splitlines() if line.startswith(('pg_restore: error:','pg_dump: error:','ERROR:'))]
            missing=re.search(r'schema "([a-z_][a-z0-9_]*)" does not exist', errors[-1] if errors else '')
            detail=('Required schema missing: '+missing.group(1)) if missing else re.sub(r'"[^"]*"|\'[^\']*\'', '[redacted]', errors[-1] if errors else 'Command failed')[:240]
            raise RuntimeError(detail)
        return result.stdout
    def local(query):
        return run(['docker','exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres','-d','aegis_restore'],input=query.encode()).decode().strip()
    try:
        with c.cursor() as q:
            q.execute('select pg_export_snapshot(),transaction_timestamp()');snapshot,checkpoint=q.fetchone()
            baseline=fingerprint(source,snapshot,table_names=APP_TABLES)
            baseline['captured_at']=checkpoint.isoformat()
            if not baseline['writes_disabled']:raise ValueError('Governed cloud writes must already be disabled')
            q.execute("select rolname,rolbypassrls from pg_roles where rolname<>'postgres' and rolname !~ '^pg_'");roles=[(sql.Identifier(name).as_string(c),bypass) for name,bypass in q.fetchall()]
            q.execute("select n.nspname,c.relname,a.attname from pg_constraint k join pg_class c on c.oid=k.conrelid join pg_namespace n on n.oid=c.relnamespace join pg_attribute a on a.attrelid=c.oid and a.attnum=k.conkey[1] where k.contype='f' and k.confrelid='auth.users'::regclass and n.nspname='public' and c.relname=any(%s)",(list(APP_TABLES),))
            refs=q.fetchall();users=set()
            for namespace,table,column in refs:
                q.execute(sql.SQL('select distinct {} from {}.{} where {} is not null').format(sql.Identifier(column),sql.Identifier(namespace),sql.Identifier(table),sql.Identifier(column)))
                users.update(str(row[0]) for row in q.fetchall())
            q.execute("select pg_get_functiondef(p.oid) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='aegis_private' or (n.nspname='public' and p.proname=any(%s)) order by n.nspname,p.proname",(list(APP_FUNCTIONS),))
            functions='\n'.join(row[0]+';' for row in q.fetchall())
            q.execute("select tenant_id,user_id from public.tenant_memberships where status='active' and role='platform_admin' limit 1");admin=q.fetchone()
        parts=parse_dsn(source)
        envfile=stage/'source.env'
        mapping={'PGHOST':parts['host'],'PGPORT':parts.get('port','5432'),'PGDATABASE':parts.get('dbname','postgres'),'PGUSER':parts['user'],'PGPASSWORD':parts['password'],'PGSSLMODE':'require','PGCONNECT_TIMEOUT':'15'}
        if any('\n' in value or '\r' in value for value in mapping.values()):raise ValueError('Invalid connection environment')
        envfile.write_text('\n'.join(k+'='+v for k,v in mapping.items())+'\n');os.chmod(envfile,0o600)
        archive=stage/'backup.dump'
        with open(archive,'wb') as out:
            os.chmod(archive,0o600)
            run(backup_command(args.image,envfile,snapshot,exporter),stdout=out)
        auth_archive=stage/'auth-schema.dump'
        with open(auth_archive,'wb') as out:
            os.chmod(auth_archive,0o600)
            run(['docker','run','--rm','--name',exporter,'--env-file',str(envfile),args.image,'pg_dump','--format=custom','--no-owner','--schema=auth','--schema-only','--snapshot='+snapshot],stdout=out)
        c.rollback();c.close();c=None
        run(['docker','run','--detach','--name',container,'--network','none','--env','POSTGRES_HOST_AUTH_METHOD=trust','--env','POSTGRES_DB=aegis_restore',args.image]);created=True
        for _ in range(60):
            check=subprocess.run(['docker','exec',container,'pg_isready','-U','postgres'],stdout=subprocess.DEVNULL,stderr=log)
            if check.returncode==0:break
            time.sleep(0.5)
        else:raise RuntimeError('Isolated database did not become ready')
        isolation=json.loads(run(['docker','inspect','--format','{{json .HostConfig}}',container]))
        if isolation['NetworkMode']!='none' or isolation.get('PortBindings') or isolation.get('Binds'):raise RuntimeError('Restore target must be offline with no published ports or host binds')
        image_id=run(['docker','inspect','--format','{{.Image}}',container]).decode().strip()
        local('create schema if not exists extensions; create extension if not exists "uuid-ossp" with schema extensions; create extension if not exists pgcrypto with schema extensions;')
        for name,bypass in roles:
            local('create role '+name+' nologin'+(' bypassrls' if bypass else '')+';')
        run(['docker','cp',str(archive),container+':/tmp/backup.dump'])
        run(['docker','cp',str(auth_archive),container+':/tmp/auth-schema.dump'])
        run(['docker','exec',container,'pg_restore','--exit-on-error','--no-owner','--section=pre-data','-U','postgres','-d','aegis_restore','/tmp/auth-schema.dump'])
        local('create schema aegis_private;')
        run(['docker','exec',container,'pg_restore','--exit-on-error','--no-owner','--section=pre-data','-U','postgres','-d','aegis_restore','/tmp/backup.dump'])
        local('set check_function_bodies=off;'+functions)
        # Restore referenced identity IDs only; never copy passwords, sessions or tokens.
        if users:local('insert into auth.users(id) values '+','.join("('"+str(uuid.UUID(u))+"')" for u in users)+';')
        run(['docker','exec',container,'pg_restore','--exit-on-error','--no-owner','--disable-triggers','--section=data','-U','postgres','-d','aegis_restore','/tmp/backup.dump'])
        run(['docker','exec',container,'pg_restore','--exit-on-error','--no-owner','--section=post-data','-U','postgres','-d','aegis_restore','/tmp/auth-schema.dump'])
        run(['docker','exec',container,'pg_restore','--exit-on-error','--no-owner','--section=post-data','-U','postgres','-d','aegis_restore','/tmp/backup.dump'])
        restored={'database_identity_sha256':hashlib.sha256(container.encode()).hexdigest(),'tables':{},'captured_at':datetime.now(timezone.utc).isoformat()}
        for table in APP_TABLES:
            rows=local('select to_jsonb(t) from public.'+table+' t order by to_jsonb(t)::text collate "C";').splitlines()
            digest=hashlib.sha256()
            for row in rows:digest.update(json.dumps(json.loads(row),sort_keys=True,separators=(',',':')).encode()+b'\n')
            restored['tables'][table]={'rows':len(rows),'sha256':digest.hexdigest()}
        restored['writes_disabled']=local('select bool_and(not writes_enabled) from public.governance_settings;')=='t'
        restored['rls_enabled']=local("select bool_and(relrowsecurity) from pg_class join pg_namespace n on n.oid=relnamespace where n.nspname='public' and relname in ("+','.join("'"+t+"'" for t in APP_TABLES)+');')=='t'
        denied_user=str(uuid.uuid4())
        visibility=local("begin;select set_config('request.jwt.claims','{\"sub\":\""+denied_user+"\",\"role\":\"authenticated\"}',true);set local role authenticated;select count(*) from public.action_requests;rollback;").splitlines()[-1]
        if visibility!='0':raise RuntimeError('Restored RLS leaked requests to a nonmember')
        if not admin:raise RuntimeError('No active admin fixture available for restored authorization checks')
        tenant_id,user_id=map(str,admin)
        local("begin;select set_config('request.jwt.claims','{\"sub\":\""+user_id+"\",\"role\":\"authenticated\"}',true);set local role authenticated;select public.workspace_value_metrics('"+tenant_id+"');select count(*) from public.reviewer_inbox('"+tenant_id+"');rollback;")
        result=compare(baseline,restored)
        result['authorization_checks']=['Nonmember request visibility denied by restored RLS','Active administrator can read restored inbox and metrics']
        result.update(image_id=image_id,scope='Offline logical governance database restore; not full Supabase authentication or infrastructure failover',backup_checkpoint=baseline['captured_at'],started_at=started.isoformat(),duration_seconds=round(time.monotonic()-timer,2),tables=restored['tables'],isolation={'network':'none','published_ports':[],'auth_credentials_copied':False})
        result['limitations']=['Only explicitly listed Aegis governance tables and functions restored. Profiles and authentication schema are metadata-only; referenced identity IDs only, no credentials or sessions. Unrelated applications excluded.','No cloud, GitHub, ServiceNow, signing-key, notification or identity-provider failover was exercised.','Duration is this local drill, not a production recovery SLA.']
    finally:
        if c:
            try:c.rollback();c.close()
            except Exception:pass
        cleanup_failed=False
        try:
            for name in (exporter,container):
                try:
                    found=subprocess.run(['docker','container','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30)
                    if found.returncode==0:
                        removed=subprocess.run(['docker','rm','--force','--volumes',name],stdout=subprocess.DEVNULL,stderr=log,timeout=30)
                        cleanup_failed=cleanup_failed or removed.returncode!=0
                    elif not any(marker in found.stderr.lower() for marker in (b'no such object',b'no such container')):
                        cleanup_failed=True
                except Exception:cleanup_failed=True
        finally:
            log.close()
            shutil.rmtree(stage)
        if cleanup_failed:raise RuntimeError('Isolated container cleanup failed; manual cleanup is required')
    result['cleanup_verified']=True
    with open(report_path,'x') as f:os.chmod(report_path,0o600);json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='tables'},indent=2))
    return 0 if result['state']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
