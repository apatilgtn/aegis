resource "google_project_iam_member" "this" {
  project = var.project
  role    = var.role
  member  = var.member

  # When set, GCP itself stops honouring the grant at this time — no
  # clean-up job has to run for access to end. GCP does not allow
  # conditions on basic roles (owner/editor/viewer), so time-bound
  # grants must use predefined or custom roles.
  dynamic "condition" {
    for_each = var.expires_at == null ? [] : [var.expires_at]
    content {
      title       = "aegis-expiry"
      description = "Granted by Aegis; expires automatically"
      expression  = "request.time < timestamp(\"${condition.value}\")"
    }
  }
}
