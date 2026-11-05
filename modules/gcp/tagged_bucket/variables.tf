variable "bucket_name" {
  type        = string
  description = "GCS bucket name — the resource under tag governance"
}

variable "project" {
  type        = string
  description = "Retained for compatibility; this module never creates a bucket"
}

variable "location" {
  type        = string
  default     = "us-central1"
  description = "Retained for compatibility; this module never creates a bucket"
}

variable "labels" {
  type        = map(string)
  description = "Must be empty; changes require a registered owning Terraform state"
}
