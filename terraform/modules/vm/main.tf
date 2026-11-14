locals {
  # One instance keeps the plain name, several get a numeric suffix.
  names = var.instance_count == 1 ? [var.name] : [for i in range(var.instance_count) : "${var.name}-${i + 1}"]
}

resource "cloudstack_instance" "this" {
  count = var.instance_count

  name             = local.names[count.index]
  display_name     = local.names[count.index]
  zone             = var.zone
  service_offering = var.service_offering
  template         = var.template
  network_id       = var.network_id
  root_disk_size   = var.root_disk_size_gb
  keypair          = var.keypair
  group            = var.group
  user_data        = var.user_data
  start_vm         = var.start_vm
  tags             = var.tags
  expunge          = var.expunge
}

# Optional extra volume, one per instance.
resource "cloudstack_disk" "data" {
  count = var.data_disk_offering == null ? 0 : var.instance_count

  name               = "${local.names[count.index]}-data"
  zone               = var.zone
  disk_offering      = var.data_disk_offering
  size               = var.data_disk_size_gb
  attach             = true
  virtual_machine_id = cloudstack_instance.this[count.index].id
  tags               = var.tags
}
