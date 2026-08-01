"""
Required-tags policy: the deterministic list of tag keys tag_remediation
checks for and proposes. Loaded from data, matching the entitlement
catalog's "never guess, look it up" discipline — the set of required keys is
a reviewed policy decision, not something the agent infers per request.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_POLICY_PATH = Path(__file__).parents[1] / "data" / "tag_policy.yaml"


def required_tag_keys() -> list[str]:
    return yaml.safe_load(_POLICY_PATH.read_text())["required_keys"]
