output "bucket_id" {
  value       = data.google_storage_bucket.existing.id
  description = "Read-only bucket ID for audit trail linkage"
}
