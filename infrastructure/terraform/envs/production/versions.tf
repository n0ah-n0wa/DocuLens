terraform {
  required_version = "~> 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.65.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
    null = {
      source  = "hashicorp/null"
      version = "3.2.4"
    }
  }
}
