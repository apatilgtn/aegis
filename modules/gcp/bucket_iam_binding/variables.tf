variable "project" {
  type = string
}
variable "bucket" {
  type = string
}
variable "role" {
  type = string
  validation {
    condition     = startswith(var.role, "roles/") && !contains(["roles/owner", "roles/editor", "roles/viewer"], var.role)
    error_message = "Use a reviewed predefined role, never a basic role."
  }
}
variable "member" {
  type = string
}
variable "expires_at" {
  type = string
}
