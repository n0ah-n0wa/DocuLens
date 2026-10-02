# One-time remote state bootstrap (manual apply — not wired into CD).
# Creates a versioned S3 state bucket (SSE-KMS) and DynamoDB lock table.

terraform {
  required_version = "~> 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0.0, < 7.0.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "doculens"
      Purpose   = "terraform-state"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "state" {
  description             = "DocuLens Terraform state encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name = "${var.name_prefix}-tf-state"
  }
}

resource "aws_kms_alias" "state" {
  name          = "alias/${var.name_prefix}-tf-state"
  target_key_id = aws_kms_key.state.key_id
}

resource "aws_s3_bucket" "state" {
  bucket = coalesce(var.state_bucket_name, "${var.name_prefix}-terraform-state-${data.aws_caller_identity.current.account_id}")

  tags = {
    Name = "${var.name_prefix}-terraform-state"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.state.arn,
          "${aws_s3_bucket.state.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.state]
}

resource "aws_dynamodb_table" "locks" {
  name         = coalesce(var.lock_table_name, "${var.name_prefix}-terraform-locks")
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.state.arn
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name = "${var.name_prefix}-terraform-locks"
  }
}
