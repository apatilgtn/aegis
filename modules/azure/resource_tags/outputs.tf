output "resource_group_id" {
  value       = azurerm_resource_group.this.id
  description = "Composite ID of the tagged resource group, for audit trail linkage"
}
