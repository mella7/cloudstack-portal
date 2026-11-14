module "vm" {
  source = "./modules/vm"

  name              = var.name
  zone              = var.zone
  service_offering  = var.service_offering
  template          = var.template
  network_id        = var.network_id
  root_disk_size_gb = var.root_disk_size_gb
  instance_count    = var.instance_count
  keypair           = var.keypair
  start_vm          = var.start_vm
  group             = var.group
  tags              = var.tags
  user_data         = var.user_data

  data_disk_offering = var.data_disk_offering
  data_disk_size_gb  = var.data_disk_size_gb
}
