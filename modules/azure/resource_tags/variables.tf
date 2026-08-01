variable "resource_group_name" {
  type        = string
  description = "Azure resource group under tag governance"
}

variable "location" {
  type        = string
  default     = "australiaeast"
  description = "Azure region"
}

variable "tags" {
  type        = map(string)
  description = "Full desired tag set, resolved by the tag_remediation capability agent"
}
