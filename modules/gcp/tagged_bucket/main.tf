# Read-only safety guard. Mutations must be routed to the Terraform state
# that already owns the bucket; this module can never create one.
data "google_storage_bucket" "existing" {
  name = var.bucket_name
}

check "managed_by_registered_owner" {
  assert {
    condition     = length(var.labels) == 0
    error_message = "Register the bucket's owning Terraform state before changing labels."
  }
}
