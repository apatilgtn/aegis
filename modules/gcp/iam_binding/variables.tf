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

variable "expires_at" {
  type        = string
  default     = null
  description = "UTC time the grant stops working, RFC 3339 (e.g. 2026-11-06T08:00:00Z). Null means no expiry."

  validation {
    condition     = var.expires_at == null || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at))
    error_message = "expires_at must be a UTC RFC 3339 timestamp such as 2026-11-06T08:00:00Z."
  }

  validation {
    condition     = var.expires_at == null || !contains(["roles/owner", "roles/editor", "roles/viewer"], var.role)
    error_message = "GCP does not support expiry conditions on basic roles; use a predefined role."
  }
}
