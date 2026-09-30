# Secrets Manager — application secrets generated here; never hardcoded (§54, §57).

terraform {
  required_version = "~> 1.16.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0.0, < 7.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0, < 4.0.0"
    }
  }
}

variable "name_prefix" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "redis_url" {
  type      = string
  sensitive = true
}

variable "extra_secret_values" {
  description = "Additional non-generated secret fields (e.g. LLM API keys supplied via TF_VAR)."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "recovery_window_in_days" {
  description = "Secrets Manager deletion recovery window (0 = force delete without recovery)."
  type        = number
  default     = 30

  validation {
    condition     = var.recovery_window_in_days == 0 || (var.recovery_window_in_days >= 7 && var.recovery_window_in_days <= 30)
    error_message = "recovery_window_in_days must be 0 or between 7 and 30."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name_prefix             = "${var.name_prefix}-app-"
  description             = "DocuLens application secrets for ${var.name_prefix}"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_in_days

  tags = merge(var.tags, { Name = "${var.name_prefix}-app-secrets" })
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(merge(
    {
      JWT_SECRET   = random_password.jwt_secret.result
      DATABASE_URL = var.database_url
      REDIS_URL    = var.redis_url
    },
    var.extra_secret_values,
  ))
}

output "app_secret_arn" {
  description = "ARN of the application secrets bundle."
  value       = aws_secretsmanager_secret.app.arn
}

output "app_secret_name" {
  description = "Name of the application secrets bundle."
  value       = aws_secretsmanager_secret.app.name
}

output "jwt_secret" {
  description = "Generated JWT signing secret."
  value       = random_password.jwt_secret.result
  sensitive   = true
}
