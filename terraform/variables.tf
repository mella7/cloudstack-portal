# Values arrive from the portal as terraform.tfvars.json. The backend has
# already validated them; the checks here are a last line of defence.

variable "name" {
  type        = string
  description = "Base name of the instances."

  validation {
    condition     = can(regex("^[a-z]([a-z0-9-]{0,48}[a-z0-9])?$", var.name))
    error_message = "Name must be a lowercase DNS label of at most 50 characters."
  }
}

variable "zone" {
  type        = string
  description = "Zone ID."
}

variable "service_offering" {
  type        = string
  description = "Compute offering ID."
}

variable "template" {
  type        = string
  description = "Template ID."
}

variable "network_id" {
  type        = string
  description = "ID of the existing network the instances attach to."
}

variable "root_disk_size_gb" {
  type        = number
  description = "Root disk size in GB."

  validation {
    condition     = var.root_disk_size_gb >= 1 && floor(var.root_disk_size_gb) == var.root_disk_size_gb
    error_message = "Root disk size must be a positive whole number of GB."
  }
}

variable "keypair" {
  type        = string
  default     = null
  nullable    = true
  description = "Name of an existing SSH keypair to install, or null for none."

  validation {
    condition     = var.keypair == null || can(regex("^[A-Za-z0-9._-]{1,64}$", var.keypair))
    error_message = "Keypair must be a plain keypair name."
  }
}

variable "start_vm" {
  type        = bool
  default     = true
  description = "Start the instances after creating them."
}

variable "group" {
  type        = string
  default     = null
  description = "Instance group, or null for none."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags to put on every instance and data disk."
}

variable "user_data" {
  type        = string
  default     = null
  sensitive   = true
  description = "cloud-init or script passed to the instances. Kept out of plan output."
}

variable "data_disk_offering" {
  type        = string
  default     = null
  description = "Disk offering ID for an extra volume per instance, or null for none."
}

variable "data_disk_size_gb" {
  type        = number
  default     = null
  description = "Size of the extra volume in GB. Only for custom-size disk offerings."
}

variable "instance_count" {
  type        = number
  description = "Number of instances to create."

  validation {
    condition     = var.instance_count >= 1 && var.instance_count <= 100 && floor(var.instance_count) == var.instance_count
    error_message = "Instance count must be a whole number between 1 and 100."
  }
}
