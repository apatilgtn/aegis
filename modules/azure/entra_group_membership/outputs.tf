output "membership_id" {
  value       = azuread_group_member.this.id
  description = "Composite ID of the created group membership, for audit trail linkage"
}
