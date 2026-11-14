variable "name" {
  type = string
}

variable "zone" {
  type = string
}

variable "service_offering" {
  type = string
}

variable "template" {
  type = string
}

variable "network_id" {
  type = string
}

variable "root_disk_size_gb" {
  type = number
}

variable "instance_count" {
  type    = number
  default = 1
}

variable "keypair" {
  type        = string
  default     = null
  description = "Name of an existing SSH keypair, or null for none."
}

variable "start_vm" {
  type    = bool
  default = true
}

variable "group" {
  type    = string
  default = null
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "user_data" {
  type      = string
  default   = null
  sensitive = true
}

variable "data_disk_offering" {
  type        = string
  default     = null
  description = "Disk offering for an extra volume per instance, or null for none."
}

variable "data_disk_size_gb" {
  type        = number
  default     = null
  description = "Only for custom-size disk offerings."
}

variable "expunge" {
  type        = bool
  default     = true
  description = "Expunge instances on destroy instead of leaving them in the Destroyed state."
}
