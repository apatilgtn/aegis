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

test_allows_explicitly_authorized_production if {
 request := object.union(valid_request, {"environment":"prod","ttl_hours":1,"production_authorized":true,"production_max_ttl":4})
 decision.allowed with input as {"request":request}
}
test_denies_production_over_capability_duration if {
 request := object.union(valid_request, {"environment":"prod","ttl_hours":8,"production_authorized":true,"production_max_ttl":4})
 not decision.allowed with input as {"request":request}
}
test_denies_production_privileged_tier_even_when_enabled if {
 request := object.union(valid_request, {"environment":"prod","tier":"tier3","ttl_hours":1,"production_authorized":true,"production_max_ttl":4})
 not decision.allowed with input as {"request":request}
}
