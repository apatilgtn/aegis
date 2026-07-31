resource "google_project_iam_member" "this" {
  project = var.project
  role    = var.role
  member  = var.member
}
