terraform {
  backend "s3" {
    # Bucket, region and state locking are supplied at init time from a git-ignored file:
    #   terraform init -backend-config=backend.hcl
    key     = "doculens/production/terraform.tfstate"
    encrypt = true
  }
}
