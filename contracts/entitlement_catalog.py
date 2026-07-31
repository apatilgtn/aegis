"""
Entitlement catalog: the deterministic lookup table that stands between a
capability request and a real cloud entitlement.

The whole point of this module is that resolution is a dict lookup, not an
LLM guess. If a (business_unit, environment, tier, cloud) tuple isn't in the
catalog, resolution fails closed — the agent must not synthesize a group
name or IAM role that "looks right".
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from contracts.capability_contract import Cloud, Environment, EntitlementRef, Tier


class EntitlementCatalogEntry(BaseModel):
    id: str
    cloud: Cloud
    business_unit: str
    environment: Environment
    tier: Tier
    resource_type: Literal["entra_group", "iam_binding"]
    resource_id: str
    display_name: str
    max_ttl_hours: int = Field(gt=0, le=168)
    terraform_module: str


class EntitlementNotFoundError(Exception):
    def __init__(self, business_unit: str, environment: Environment, tier: Tier, cloud: Cloud):
        super().__init__(
            f"No catalog entry for business_unit={business_unit!r} "
            f"environment={environment.value!r} tier={tier.value!r} cloud={cloud.value!r} — "
            "refusing to synthesize an entitlement"
        )


class EntitlementCatalog:
    def __init__(self, entries: list[EntitlementCatalogEntry]):
        self._by_key = {
            (e.business_unit, e.environment, e.tier, e.cloud): e for e in entries
        }

    @classmethod
    def load(cls, path: Path | str) -> "EntitlementCatalog":
        raw = yaml.safe_load(Path(path).read_text())
        entries = [EntitlementCatalogEntry(**entry) for entry in raw["entries"]]
        return cls(entries)

    def resolve(
        self, business_unit: str, environment: Environment, tier: Tier, cloud: Cloud
    ) -> EntitlementCatalogEntry:
        key = (business_unit, environment, tier, cloud)
        entry = self._by_key.get(key)
        if entry is None:
            raise EntitlementNotFoundError(business_unit, environment, tier, cloud)
        return entry

    def business_units(self) -> set[str]:
        return {key[0] for key in self._by_key}

    def all_entries(self) -> list[EntitlementCatalogEntry]:
        return list(self._by_key.values())

    def to_entitlement_ref(self, entry: EntitlementCatalogEntry) -> EntitlementRef:
        return EntitlementRef(
            cloud=entry.cloud,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            catalog_entry_id=entry.id,
            display_name=entry.display_name,
        )
