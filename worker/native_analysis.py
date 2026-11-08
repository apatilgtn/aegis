"""Native evidence coverage for the access capability, separate from OPA approval."""
from __future__ import annotations
from datetime import datetime, timezone

SIMULATOR_DOCUMENTATION = 'https://docs.cloud.google.com/policy-intelligence/docs/simulate-iam-policies'


def assess(gcp, account: dict, route: dict | None, beneficiary: str) -> dict:
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'simulator': {'state': 'unsupported', 'summary': 'Google historical Policy Simulator does not evaluate conditional IAM binding changes. Aegis time-bound grants use a condition.', 'documentation': SIMULATOR_DOCUMENTATION},
              'advisory': True, 'authorization': 'OPA and independent human approvals remain authoritative.'}
    if not route:
        report['analyzer'] = {'state': 'unavailable', 'summary': 'No current reviewed entitlement route is available.'}
        return report
    try:
        report['analyzer'] = gcp.analyze_access(account['project_id'], 'user:' + beneficiary, route['role_identifier'], route.get('scope_identifier') if route.get('scope_type') == 'bucket' else None)
    except Exception as exc:
        report['analyzer'] = {'state': 'unavailable', 'summary': 'Native access analysis could not complete. Verify API enablement and the read-only analyzer permissions.', 'error_type': type(exc).__name__}
    return report
