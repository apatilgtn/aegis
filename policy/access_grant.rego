package aegis.access_grant

# Guardrail policy for the access_grant capability.
#
# The guardrail agent calls OPA with this input shape and turns the result
# into a PolicyDecision (contracts/capability_contract.py). The LLM never
# makes the allow/deny call itself — it only explains `deny` to the user.
#
# input.request       - the AccessGrantRequest fields (environment, tier,
#                        justification, ttl_hours)
# input.catalog_entry - the EntitlementCatalogEntry already resolved for this
#                        request (environment, max_ttl_hours)

default allow := false

deny contains msg if {
	input.request.environment == "prod"
	msg := "production environment access grants are not permitted through this capability"
}

deny contains msg if {
	count(input.request.justification) < 10
	msg := "justification must be at least 10 characters"
}

deny contains msg if {
	input.request.ttl_hours > input.catalog_entry.max_ttl_hours
	msg := sprintf(
		"requested duration of %dh is longer than the maximum allowed for this access (%dh)",
		[input.request.ttl_hours, input.catalog_entry.max_ttl_hours],
	)
}

deny contains msg if {
	input.request.environment != input.catalog_entry.environment
	msg := "requested environment does not match this entitlement's configured environment"
}

allow if {
	count(deny) == 0
}
