output "bucket_id" {
  value       = google_storage_bucket.this.id
  description = "Composite ID of the tagged bucket, for audit trail linkage"
}
