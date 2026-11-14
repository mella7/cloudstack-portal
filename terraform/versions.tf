terraform {
  required_version = ">= 1.5.0"

  required_providers {
    cloudstack = {
      source  = "cloudstack/cloudstack"
      version = "~> 0.6.0"
    }
  }

  # The backend passes -backend-config=path=<data>/state/<job id>/terraform.tfstate
  # at init, so state survives cleanup of the per-job working directory.
  backend "local" {}
}

# Credentials come from the environment only:
# CLOUDSTACK_API_URL, CLOUDSTACK_API_KEY, CLOUDSTACK_SECRET_KEY
provider "cloudstack" {}
