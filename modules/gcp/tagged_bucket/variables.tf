variable "bucket_name" {
  type        = string
  description = "GCS bucket name — the resource under tag governance"
}

variable "project" {
  type        = string
  description = "GCP project ID the bucket is created in"
}

variable "location" {
  type        = string
  default     = "us-central1"
  description = "GCS bucket location"
}

variable "labels" {
  type        = map(string)
  description = "Full desired label set, resolved by the tag_remediation capability agent"
}
