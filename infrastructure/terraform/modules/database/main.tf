# RDS PostgreSQL in private subnets — encrypted, not publicly accessible (§5.4, §87).

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
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2.0, < 4.0.0"
    }
  }
}

variable "name_prefix" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "kms_key_arn" {
  type = string
}

variable "engine_version" {
  type    = string
  default = "17.4"
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  type    = number
  default = 20
}

variable "max_allocated_storage_gb" {
  type    = number
  default = 100
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_days" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "skip_final_snapshot" {
  type    = bool
  default = false
}

variable "apply_immediately" {
  description = "Apply modifications immediately (prefer false in production)."
  type        = bool
  default     = false
}

variable "cloudwatch_logs_exports" {
  description = "RDS log types to export to CloudWatch Logs."
  type        = list(string)
  default     = ["postgresql", "upgrade"]
}

variable "iam_database_authentication_enabled" {
  description = "Enable IAM database authentication (password auth remains available)."
  type        = bool
  default     = true
}

variable "performance_insights_enabled" {
  description = "Enable RDS Performance Insights (encrypted with the environment CMK)."
  type        = bool
  default     = false
}

variable "prevent_destroy" {
  description = "When true, block terraform destroy via a dependent null_resource guard (lifecycle.prevent_destroy cannot take a variable)."
  type        = bool
  default     = false
}

variable "database_name" {
  type    = string
  default = "doculens"
}

variable "master_username" {
  type    = string
  default = "doculens"
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = var.subnet_ids
  tags       = merge(var.tags, { Name = "${var.name_prefix}-db-subnets" })
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name_prefix}-pg17"
  family = "postgres17"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-pg17-params" })
}

resource "aws_db_instance" "this" {
  identifier = "${var.name_prefix}-postgres"

  engine                = "postgres"
  engine_version        = var.engine_version
  instance_class        = var.instance_class
  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  db_name  = var.database_name
  username = var.master_username
  password = random_password.master.result
  port     = 5432

  db_subnet_group_name                = aws_db_subnet_group.this.name
  vpc_security_group_ids              = var.security_group_ids
  parameter_group_name                = aws_db_parameter_group.this.name
  publicly_accessible                 = false
  multi_az                            = var.multi_az
  iam_database_authentication_enabled = var.iam_database_authentication_enabled

  backup_retention_period   = var.backup_retention_days
  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name_prefix}-postgres-final"

  copy_tags_to_snapshot           = true
  auto_minor_version_upgrade      = true
  performance_insights_enabled    = var.performance_insights_enabled
  performance_insights_kms_key_id = var.performance_insights_enabled ? var.kms_key_arn : null
  apply_immediately               = var.apply_immediately
  enabled_cloudwatch_logs_exports = var.cloudwatch_logs_exports

  tags = merge(var.tags, { Name = "${var.name_prefix}-postgres" })
}

# Terraform forbids interpolating variables into lifecycle.prevent_destroy; this guard
# blocks destroy of the instance whenever prevent_destroy is true (production).
resource "null_resource" "prevent_destroy" {
  count = var.prevent_destroy ? 1 : 0

  triggers = {
    db_arn = aws_db_instance.this.arn
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "endpoint" {
  description = "RDS hostname."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "RDS port."
  value       = aws_db_instance.this.port
}

output "database_name" {
  description = "Initial database name."
  value       = aws_db_instance.this.db_name
}

output "master_username" {
  description = "Master username."
  value       = aws_db_instance.this.username
}

output "master_password" {
  description = "Master password (sensitive; stored in Secrets Manager by the secrets module)."
  value       = random_password.master.result
  sensitive   = true
}

output "resource_id" {
  description = "RDS resource id."
  value       = aws_db_instance.this.resource_id
}

output "arn" {
  description = "RDS instance ARN."
  value       = aws_db_instance.this.arn
}

output "asyncpg_url" {
  description = "SQLAlchemy asyncpg URL for the application (ssl=require for asyncpg)."
  value = format(
    "postgresql+asyncpg://%s:%s@%s:%s/%s?ssl=require",
    aws_db_instance.this.username,
    urlencode(random_password.master.result),
    aws_db_instance.this.address,
    tostring(aws_db_instance.this.port),
    aws_db_instance.this.db_name,
  )
  sensitive = true
}
