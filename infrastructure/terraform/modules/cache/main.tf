# ElastiCache Redis — private, auth token, encryption in transit and at rest (§5.4, §37).

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

variable "node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "engine_version" {
  type    = string
  default = "7.1"
}

variable "num_cache_clusters" {
  description = "Primary + replicas (1 = single node)."
  type        = number
  default     = 1
}

variable "automatic_failover_enabled" {
  type    = bool
  default = false
}

variable "multi_az_enabled" {
  type    = bool
  default = false
}

variable "snapshot_retention_limit" {
  type    = number
  default = 1
}

variable "final_snapshot_identifier" {
  description = "Final snapshot id on delete when snapshot_retention_limit > 0. Null skips."
  type        = string
  default     = null
  nullable    = true
}

variable "prevent_destroy" {
  description = "When true, block terraform destroy via a dependent null_resource guard."
  type        = bool
  default     = false
}

variable "apply_immediately" {
  description = "Apply modifications immediately (prefer false in production)."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "random_password" "auth_token" {
  length  = 64
  special = false
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name_prefix}-redis"
  subnet_ids = var.subnet_ids
  tags       = merge(var.tags, { Name = "${var.name_prefix}-redis-subnets" })
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = substr(replace("${var.name_prefix}-redis", "_", "-"), 0, 40)
  description          = "${var.name_prefix} Redis for rate limits and local-compatible queue staging"

  engine               = "redis"
  engine_version       = var.engine_version
  node_type            = var.node_type
  num_cache_clusters   = var.num_cache_clusters
  port                 = 6379
  parameter_group_name = "default.redis7"

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = var.security_group_ids

  at_rest_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn
  transit_encryption_enabled = true
  auth_token                 = random_password.auth_token.result

  automatic_failover_enabled = var.automatic_failover_enabled
  multi_az_enabled           = var.multi_az_enabled
  snapshot_retention_limit   = var.snapshot_retention_limit
  # final_snapshot_identifier is supported on aws_elasticache_replication_group.
  final_snapshot_identifier = (
    var.snapshot_retention_limit > 0 && var.final_snapshot_identifier != null
    ? var.final_snapshot_identifier
    : null
  )
  apply_immediately       = var.apply_immediately
  transit_encryption_mode = "required"

  tags = merge(var.tags, { Name = "${var.name_prefix}-redis" })
}

resource "null_resource" "prevent_destroy" {
  count = var.prevent_destroy ? 1 : 0

  triggers = {
    replication_group_arn = aws_elasticache_replication_group.this.arn
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "primary_endpoint" {
  description = "Redis primary hostname."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "port" {
  description = "Redis port."
  value       = aws_elasticache_replication_group.this.port
}

output "auth_token" {
  description = "Redis AUTH token."
  value       = random_password.auth_token.result
  sensitive   = true
}

output "rediss_url" {
  description = "redis URL with TLS (rediss://) for deployed APP_ENV."
  value = format(
    "rediss://:%s@%s:%s/0",
    urlencode(random_password.auth_token.result),
    aws_elasticache_replication_group.this.primary_endpoint_address,
    tostring(aws_elasticache_replication_group.this.port),
  )
  sensitive = true
}

output "arn" {
  description = "Replication group ARN."
  value       = aws_elasticache_replication_group.this.arn
}
