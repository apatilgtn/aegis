package aegis.access_grant

import rego.v1

valid_catalog_entry := {
	"id": "azure-payments-nonprod-tier2",
	"environment": "nonprod",
	"max_ttl_hours": 24,
}

valid_request := {
	"environment": "nonprod",
	"tier": "tier2",
	"justification": "debugging a payments reconciliation ticket",
	"ttl_hours": 8,
}

test_allow_valid_request if {
	allow with input as {"request": valid_request, "catalog_entry": valid_catalog_entry}
}

test_deny_prod_environment if {
	req := object.union(valid_request, {"environment": "prod"})
	not allow with input as {"request": req, "catalog_entry": valid_catalog_entry}
}

test_deny_short_justification if {
	req := object.union(valid_request, {"justification": "pls"})
	not allow with input as {"request": req, "catalog_entry": valid_catalog_entry}
}

test_deny_ttl_exceeds_catalog_max if {
	req := object.union(valid_request, {"ttl_hours": 48})
	not allow with input as {"request": req, "catalog_entry": valid_catalog_entry}
}

test_deny_environment_mismatch_with_catalog if {
	entry := object.union(valid_catalog_entry, {"environment": "prod"})
	not allow with input as {"request": valid_request, "catalog_entry": entry}
}
