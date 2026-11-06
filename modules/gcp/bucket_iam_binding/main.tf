resource "google_storage_bucket_iam_member" "this" {
  bucket = var.bucket
  role   = var.role
  member = var.member
  condition {
    title       = "aegis-expiry"
    description = "Granted by Aegis; expires automatically"
    expression  = "request.time < timestamp(\"${var.expires_at}\")"
  }
}
