package aegis.label_fix

import rego.v1

# Adds only missing required labels to a non-production GCP bucket.

default allow := false

required_labels := {"owner", "cost-center", "environment"}

deny contains "only Google Cloud buckets can be fixed" if {
  input.request.cloud != "gcp"
}

deny contains "production resources are not changed through this capability" if {
  input.request.environment != "nonprod"
}

deny contains "choose a bucket" if {
  count(object.get(input.request, "bucket", "")) < 3
}

deny contains "cost center must be 2 to 63 characters" if {
  cost_center := object.get(input.request, "cost_center", "")
  not regex.match(`^[a-z0-9][a-z0-9_-]{1,62}$`, cost_center)
}

deny contains "justification must be at least 10 characters" if {
  count(trim_space(object.get(input.request, "justification", ""))) < 10
}

deny contains sprintf("label %q is not a required label", [key]) if {
  some key in object.get(input.request, "labels_to_add", [])
  not key in required_labels
}

deny contains "the bucket already has every required label" if {
  count(object.get(input.request, "labels_to_add", [])) == 0
}

allow if count(deny) == 0

decision := {
  "allowed": allow,
  "reasons": sort([reason | deny[reason]]),
  "policy_ids": ["AEGIS-LABEL-001", "AEGIS-PROD-001"],
  "risk_level": "low",
}
