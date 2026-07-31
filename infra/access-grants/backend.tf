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
