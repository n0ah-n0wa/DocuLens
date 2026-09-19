# Production environment root module.
# Infrastructure modules (networking, compute, database, storage, cache, IAM, secrets,
# observability, API Gateway) are added in the infrastructure phase; see ../../modules/README.md.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

locals {
  project     = "doculens"
  environment = "production"

  common_tags = {
    Project     = local.project
    Environment = local.environment
    ManagedBy   = "terraform"
  }
}
