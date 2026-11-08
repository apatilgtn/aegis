#!/usr/bin/env python3
"""Capture a data fingerprint or verify an isolated restored database. Never restores or writes a database."""
import argparse
import hashlib
import json
import os
import re
from datetime import datetime,timezone

TABLES=('action_requests','workflow_runs','workflow_events','proposal_receipts','plan_approvals','execution_check_events','change_verifications','verification_observations','audit_events','governance_settings','tenant_memberships')


def connection_identity(parts):
    host=parts.get('host','')
    direct=re.fullmatch(r'db\.([a-z0-9]{20})\.supabase\.co',host)
    pooled=re.fullmatch(r'[^.]+\.([a-z0-9]{20})',parts.get('user',''))
    # Ignore role names: changing a database user must not disguise the source.
    resource={'supabase_project':direct.group(1) if direct else pooled.group(1)} if direct or pooled else {k:parts.get(k,'') for k in ('host','port','dbname')}
    return hashlib.sha256(json.dumps(resource,sort_keys=True).encode()).hexdigest()


def identity(dsn):
    from psycopg2.extensions import parse_dsn
    return connection_identity(parse_dsn(dsn))


def fingerprint(dsn,snapshot=None,table_names=TABLES):
    import psycopg2
    from psycopg2 import sql
    c=psycopg2.connect(dsn,sslmode='require',connect_timeout=15)
    c.set_session(readonly=True,isolation_level='REPEATABLE READ')
    try:
        tables={}
        with c.cursor() as cur:
            if snapshot:cur.execute("set transaction snapshot %s",(snapshot,))
            cur.execute("set local statement_timeout='60s'")
            for table in table_names:
                cur.execute(sql.SQL('select to_jsonb(t) from public.{} t order by to_jsonb(t)::text collate "C"').format(sql.Identifier(table)))
                digest=hashlib.sha256();count=0
                while True:
                    rows=cur.fetchmany(500)
                    if not rows:break
                    for (row,) in rows:
                        digest.update(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n');count+=1
                tables[table]={'rows':count,'sha256':digest.hexdigest()}
            cur.execute('select bool_and(not writes_enabled) from public.governance_settings')
            writes_disabled=cur.fetchone()[0] is True
            cur.execute("select relname,relrowsecurity from pg_class join pg_namespace n on n.oid=relnamespace where n.nspname='public' and relname=any(%s)",(list(table_names),))
            rls=all(enabled for _,enabled in cur.fetchall())
        return {'schema':1,'captured_at':datetime.now(timezone.utc).isoformat(),'database_identity_sha256':identity(dsn),'tables':tables,'writes_disabled':writes_disabled,'rls_enabled':rls}
    finally:c.rollback();c.close()


def compare(baseline,restored):
    if baseline['database_identity_sha256']==restored['database_identity_sha256']:raise ValueError('Restore target must be a different isolated database')
    if not set(TABLES)<=set(baseline['tables']):raise ValueError('Baseline lacks required governance tables')
    mismatches=[table for table in sorted(set(baseline['tables'])|set(restored['tables'])) if baseline['tables'].get(table)!=restored['tables'].get(table)]
    passed=not mismatches and restored['writes_disabled'] and restored['rls_enabled']
    return {'state':'passed' if passed else 'failed','mismatched_tables':mismatches,'writes_disabled':restored['writes_disabled'],'rls_enabled':restored['rls_enabled'],'target_identity_sha256':restored['database_identity_sha256'],'checked_at':restored['captured_at'],'limitations':['Validates restored data and RLS flags, not authentication, Secret Manager, GitHub, ServiceNow, or full application recovery.','Capture the manifest at the backup checkpoint with writers paused; a later capture is not a matching backup baseline.','RTO and RPO require independently recorded backup and recovery timestamps.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['capture','verify']);parser.add_argument('--manifest',required=True);args=parser.parse_args()
    if args.mode=='capture':
        report=fingerprint(os.environ['SUPABASE_DB_URL'])
        with open(args.manifest,'x') as f:os.chmod(args.manifest,0o600);json.dump(report,f,indent=2)
        print('Captured fingerprint manifest; no row contents or credentials stored')
    else:
        with open(args.manifest) as f:baseline=json.load(f)
        target=os.environ['AEGIS_RESTORE_DB_URL']
        if identity(target)==baseline['database_identity_sha256']:raise ValueError('Refusing production/source database as restore target')
        report=compare(baseline,fingerprint(target));print(json.dumps(report,indent=2));return 0 if report['state']=='passed' else 1
    return 0

if __name__=='__main__':raise SystemExit(main())
