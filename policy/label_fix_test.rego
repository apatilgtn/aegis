package aegis.label_fix

import rego.v1

valid := {"cloud": "gcp", "environment": "nonprod", "bucket": "team-exports", "cost_center": "pmt-01",
  "labels_to_add": ["owner", "cost-center"], "justification": "Cost reporting needs owner and cost center"}

test_allows_missing_required_labels if decision.allowed with input as {"request": valid}

test_denies_production if not decision.allowed with input as {"request": object.union(valid, {"environment": "prod"})}

test_denies_unrequired_label if not decision.allowed with input as {"request": object.union(valid, {"labels_to_add": ["owner", "team"]})}

test_denies_bad_cost_center if not decision.allowed with input as {"request": object.union(valid, {"cost_center": "Bad Value!"})}

test_denies_short_reason if not decision.allowed with input as {"request": object.union(valid, {"justification": "x"})}

test_denies_when_nothing_is_missing if not decision.allowed with input as {"request": object.union(valid, {"labels_to_add": []})}
