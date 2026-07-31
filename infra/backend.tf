terraform {
  backend "gcs" {
    bucket = "aegis-tfstate-ual-demo-af168"
    prefix = "terraform/state"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = "ual-demo-af168"
  region  = "us-central1"
}

# Smoke-test marker: proves the Cloud Build pipeline (init/plan/apply) runs
# end-to-end as aegis-pipeline before any real IAM resource is ever added.
output "pipeline_smoke_test" {
  value = "aegis-pipeline is alive"
}
