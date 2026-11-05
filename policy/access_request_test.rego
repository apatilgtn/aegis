package aegis.access_request

import rego.v1

valid_request := {"cloud": "gcp", "environment": "nonprod", "tier": "tier1", "ttl_hours": 8, "justification": "Investigate a customer support incident"}

test_allows_bounded_nonprod_request if {
  decision.allowed with input as {"request": valid_request}
}

test_denies_production if {
  request := object.union(valid_request, {"environment": "prod"})
  not decision.allowed with input as {"request": request}
}

test_denies_excessive_ttl if {
  request := object.union(valid_request, {"tier": "tier3", "ttl_hours": 24})
  not decision.allowed with input as {"request": request}
}

test_denies_unknown_tier if {
  request := object.union(valid_request, {"tier": "owner"})
  not decision.allowed with input as {"request": request}
}
