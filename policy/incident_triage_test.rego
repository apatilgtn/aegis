package aegis.incident_triage

import rego.v1

valid := {"cloud": "gcp", "resource_hint": "payments-api", "time_window_hours": 24, "justification": "Customers report failed payments"}

test_allows_bounded_search if decision.allowed with input as {"request": valid}

test_denies_long_window if not decision.allowed with input as {"request": object.union(valid, {"time_window_hours": 500})}

test_denies_zero_window if not decision.allowed with input as {"request": object.union(valid, {"time_window_hours": 0})}

test_denies_missing_hint if not decision.allowed with input as {"request": object.union(valid, {"resource_hint": " "})}

test_denies_short_reason if not decision.allowed with input as {"request": object.union(valid, {"justification": "why"})}

test_denies_other_cloud if not decision.allowed with input as {"request": object.union(valid, {"cloud": "azure"})}
