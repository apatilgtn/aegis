package aegis.tag_remediation

# Guardrail policy for the tag_remediation capability.
#
# The guardrail agent calls OPA with this input shape and turns the result
# into a PolicyDecision (contracts/capability_contract.py). The LLM never
# makes the allow/deny call itself — it only explains `deny` to the user.
#
# input.request - the TagComplianceRequest fields (environment, cost_center,
#                  justification)

default allow := false

deny contains msg if {
	input.request.environment == "prod"
	msg := "production resources are not remediated through this capability"
}

deny contains msg if {
	count(input.request.justification) < 10
	msg := "justification must be at least 10 characters"
}

deny contains msg if {
	count(input.request.cost_center) < 2
	msg := "cost_center must be at least 2 characters"
}

allow if {
	count(deny) == 0
}
