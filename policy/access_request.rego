package aegis.access_request

import rego.v1

default allow := false

valid_clouds := {"gcp"}
valid_environments := {"nonprod", "prod"}
max_ttl_by_tier := {"tier1": 72, "tier2": 24, "tier3": 8}

deny contains "cloud provider is not supported" if {
  not input.request.cloud in valid_clouds
}

deny contains "environment is not supported" if {
  not input.request.environment in valid_environments
}

deny contains "access tier is not supported" if {
  not input.request.tier in object.keys(max_ttl_by_tier)
}

deny contains "production access is not enabled in the current policy" if {
  input.request.environment == "prod"
  not object.get(input.request, "production_authorized", false)
}

deny contains "justification must be at least 10 characters" if {
  count(input.request.justification) < 10
}

deny contains msg if {
  maximum := max_ttl_by_tier[input.request.tier]
  input.request.ttl_hours > maximum
  msg := sprintf("requested duration of %dh exceeds the %dh maximum for %s", [input.request.ttl_hours, maximum, input.request.tier])
}

allow if count(deny) == 0

risk_level := "critical" if input.request.environment == "prod"
risk_level := "high" if { input.request.environment != "prod"; input.request.tier == "tier3" }
risk_level := "medium" if { input.request.environment != "prod"; input.request.tier == "tier2" }
default risk_level := "low"

decision := {
  "allowed": allow,
  "reasons": sort([reason | deny[reason]]),
  "policy_ids": ["AEGIS-ACCESS-001", "AEGIS-TTL-001", "AEGIS-PROD-001"],
  "risk_level": risk_level,
}


deny contains "production duration exceeds the capability limit" if {
 input.request.environment == "prod"
 input.request.ttl_hours > object.get(input.request, "production_max_ttl", 1)
}

deny contains "production tier3 access is not supported" if {
 input.request.environment == "prod"
 input.request.tier == "tier3"
}
