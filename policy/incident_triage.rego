package aegis.incident_triage

import rego.v1

# Investigating reads logs only, so the guardrail just bounds the search.

default allow := false

deny contains "only Google Cloud logs can be searched" if {
  input.request.cloud != "gcp"
}

deny contains "describe the service or resource to search for" if {
  count(trim_space(object.get(input.request, "resource_hint", ""))) < 2
}

deny contains "the search window must be between 1 and 168 hours" if {
  hours := object.get(input.request, "time_window_hours", 0)
  not is_number(hours)
}

deny contains "the search window must be between 1 and 168 hours" if {
  hours := object.get(input.request, "time_window_hours", 0)
  is_number(hours)
  not hours >= 1
}

deny contains "the search window must be between 1 and 168 hours" if {
  hours := object.get(input.request, "time_window_hours", 0)
  is_number(hours)
  hours > 168
}

deny contains "justification must be at least 10 characters" if {
  count(trim_space(object.get(input.request, "justification", ""))) < 10
}

allow if count(deny) == 0

decision := {
  "allowed": allow,
  "reasons": sort([reason | deny[reason]]),
  "policy_ids": ["AEGIS-TRIAGE-001"],
  "risk_level": "low",
}
