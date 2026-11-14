output "instance_ids" {
  value = cloudstack_instance.this[*].id
}

output "instance_names" {
  value = cloudstack_instance.this[*].name
}

output "data_disk_ids" {
  value = cloudstack_disk.data[*].id
}

output "ip_addresses" {
  value = cloudstack_instance.this[*].ip_address
}
