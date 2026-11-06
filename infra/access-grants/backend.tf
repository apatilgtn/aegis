terraform {
  backend "gcs" {
    bucket = "aegis-tfstate-project-4f0a7dac-0503-4e87-abe"
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
  project = "project-4f0a7dac-0503-4e87-abe"
  region  = "us-central1"
}
