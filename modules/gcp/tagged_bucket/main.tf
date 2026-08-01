resource "google_storage_bucket" "this" {
  name          = var.bucket_name
  project       = var.project
  location      = var.location
  force_destroy = true
  labels        = var.labels
}
