#!/usr/bin/env python3
"""Read-only GCP deployment preflight. Never grants permissions or enables writes."""
import argparse
import json
import re
import subprocess
import urllib.error
import urllib.request


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Command unavailable or permission denied: ' + ' '.join(args[:3]))
    return result.stdout.strip()


def check(project):
    result = {'project': project, 'read_only': True, 'checks': []}
    services = {}
    for name in ('aegis-worker', 'aegis-mcp', 'aegis-portal'):
        try:
            service = json.loads(command('gcloud', 'run', 'services', 'describe', name, '--project='+project, '--region=australia-southeast1', '--format=json'))
            services[name] = service
            ready = next((c.get('status') == 'True' for c in service['status']['conditions'] if c['type'] == 'Ready'), False)
            result['checks'].append({'check': name, 'state': 'ready' if ready else 'action_required', 'url': service['status'].get('url')})
        except Exception as exc:
            result['checks'].append({'check': name, 'state': 'unknown', 'reason': str(exc)})
    if 'aegis-worker' in services:
        worker = services['aegis-worker']
        reader = worker['spec']['template']['spec']['serviceAccountName']
        try:
            policy = json.loads(command('gcloud', 'run', 'services', 'get-iam-policy', 'aegis-worker', '--project='+project, '--region=australia-southeast1', '--format=json'))
            invokers = [member for binding in policy.get('bindings', []) if binding['role'] == 'roles/run.invoker' for member in binding['members']]
            expected = 'serviceAccount:aegis-pipeline@'+project+'.iam.gserviceaccount.com'
            result['checks'].append({'check':'gate_invocation_policy','state':'ready' if expected in invokers and 'allUsers' not in invokers and 'allAuthenticatedUsers' not in invokers else 'action_required','scope':'Service-level policy only; inherited permissions are not assessed.'})
            token = command('gcloud','auth','print-access-token','--impersonate-service-account='+reader,'--project='+project)
            permissions = ['cloudasset.assets.analyzeIamPolicy','cloudasset.assets.searchAllIamPolicies','cloudasset.assets.searchAllResources','serviceusage.services.use']
            request = urllib.request.Request('https://cloudresourcemanager.googleapis.com/v1/projects/'+project+':testIamPermissions',data=json.dumps({'permissions':permissions}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=30) as response: granted=json.load(response).get('permissions',[])
            missing=sorted(set(permissions)-set(granted))
            result['checks'].append({'check':'native_iam_reader','state':'action_required' if missing else 'ready','identity':reader,'missing_permissions':missing})
        except Exception as exc:
            result['checks'].append({'check':'native_iam_reader','state':'unknown','reason':type(exc).__name__,'remedy':'Run with existing reader impersonation rights, or test from the worker identity. This script never grants them.'})
    result['checks'].append({'check':'pipeline_identity_and_approvals','state':'not_tested','remedy':'Run the protected CI --smoke checkpoint, then review a scoped proposal. Configuration checks do not prove execution authorization.'})
    return result

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]',args.project):parser.error('Invalid GCP project ID')
    print(json.dumps(check(args.project),indent=2))
