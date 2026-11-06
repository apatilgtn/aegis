output "binding_id" {
  value       = google_project_iam_member.this.id
  description = "Composite ID of the created IAM member binding, for audit trail linkage"
}

output "expires_at" {
  value       = var.expires_at
  description = "When the grant stops working, or null if it does not expire"
}
