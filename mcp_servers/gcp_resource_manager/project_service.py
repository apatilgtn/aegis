"""
GCP project resolver — stands in for a live `projects.search`/lookup call.
The payments/non-prod mapping now points at a real GCP project
(GCP_PROJECT_PAYMENTS_NONPROD in .env: ual-demo-af168); there are no GCP
credentials in this environment (no gcloud CLI, no service account key, no
ADC) to actually call the Cloud Resource Manager API, so this still can't
verify the project exists or that the caller has access to it — it only
resolves the ID. Real deployment swaps this for a live API call behind the
same function; callers don't change.

Read-only, matching the propose-don't-mutate design.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class ProjectNotFoundError(Exception):
    def __init__(self, business_unit: str):
        super().__init__(f"no GCP project mapped for business_unit {business_unit!r}")


_PROJECTS = {
    "payments": os.getenv("GCP_PROJECT_PAYMENTS_NONPROD", "bb-payments-nonprod-1a2b"),
}


def resolve_project(business_unit: str) -> str:
    try:
        return _PROJECTS[business_unit]
    except KeyError:
        raise ProjectNotFoundError(business_unit) from None
