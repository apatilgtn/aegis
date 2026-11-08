#!/usr/bin/env python3
"""Print a bounded pilot-executor IAM proposal. This tool never applies IAM changes."""
import argparse
import json
import re
from datetime import datetime,timedelta,timezone

PERMISSIONS=('storage.buckets.getIamPolicy','storage.buckets.setIamPolicy')


def plan(project,project_number,minutes=120,now=None):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]',project or ''):raise ValueError('Invalid project ID')
    if not re.fullmatch(r'[0-9]{6,20}',str(project_number)):raise ValueError('Invalid project number')
    if isinstance(minutes,bool) or not isinstance(minutes,int) or not 5<=minutes<=120:raise ValueError('Executor duration must be 5 to 120 minutes')
    current=now or datetime.now(timezone.utc)
    if current.tzinfo is None:raise ValueError('An explicit timezone is required')
    expires=(current+timedelta(minutes=minutes)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    role_id='aegisPilotBucketIam';role=f'projects/{project}/roles/{role_id}'
    bucket='aegis-assurance-pilot-'+str(project_number)
    member=f'serviceAccount:aegis-pipeline@{project}.iam.gserviceaccount.com'
    condition={'title':'aegis-pilot-executor','expression':f'request.time < timestamp("{expires}")','description':'Temporary executor access for the isolated empty-bucket pilot'}
    return {'schema':1,'project_id':project,'bucket':bucket,'member':member,'role':role,'permissions':list(PERMISSIONS),'expires_at':expires,'condition':condition,'requires_explicit_operator_approval':True,'scope':'Dedicated pilot bucket only; no object-data or project-wide IAM permissions','cleanup':'Remove this exact conditional binding after the pilot; expiry limits authority even if cleanup is delayed.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--project-number',required=True);p.add_argument('--minutes',type=int,default=120);args=p.parse_args()
    print(json.dumps(plan(args.project,args.project_number,args.minutes),indent=2))

if __name__=='__main__':main()
