package aegis.tag_remediation

import rego.v1

valid_request := {
	"environment": "nonprod",
	"cost_center": "PMT-01",
	"justification": "quarterly tag compliance sweep for payments buckets",
}

test_allow_valid_request if {
	allow with input as {"request": valid_request}
}

test_deny_prod_environment if {
	req := object.union(valid_request, {"environment": "prod"})
	not allow with input as {"request": req}
}

test_deny_short_justification if {
	req := object.union(valid_request, {"justification": "pls"})
	not allow with input as {"request": req}
}

test_deny_short_cost_center if {
	req := object.union(valid_request, {"cost_center": "x"})
	not allow with input as {"request": req}
}
