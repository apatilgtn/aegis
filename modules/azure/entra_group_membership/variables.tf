variable "group_object_id" {
  type        = string
  description = "Entra ID group object ID resolved from the entitlement catalog"
}

variable "member_object_id" {
  type        = string
  description = "Entra ID object ID of the principal being granted membership"
}
