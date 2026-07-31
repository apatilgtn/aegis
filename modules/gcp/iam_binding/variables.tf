variable "project" {
  type        = string
  description = "GCP project ID the binding applies to"
}

variable "role" {
  type        = string
  description = "IAM role resolved from the entitlement catalog"
}

variable "member" {
  type        = string
  description = "IAM member string, e.g. user:someone@example.com"
}
