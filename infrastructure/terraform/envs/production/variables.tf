variable "aws_region" {
  description = "AWS region for every resource in this environment."
  type        = string
}

variable "aws_account_id" {
  description = "Optional expected AWS account ID; when set, providers refuse other accounts."
  type        = string
  default     = ""
}

variable "vpc_cidr" {
  type    = string
  default = "10.30.0.0/16"
}

variable "az_count" {
  type    = number
  default = 3
}

variable "nat_gateway_count" {
  description = "NAT gateways for production HA (raised to az_count when lower)."
  type        = number
  default     = 3

  validation {
    condition     = var.nat_gateway_count >= 2 && var.nat_gateway_count <= 3
    error_message = "Production nat_gateway_count must be 2 or 3."
  }
}

variable "enable_interface_endpoints" {
  description = "Must remain true in production (forced in main.tf)."
  type        = bool
  default     = true

  validation {
    condition     = var.enable_interface_endpoints
    error_message = "Production requires enable_interface_endpoints = true."
  }
}

variable "enable_vpc_flow_logs" {
  description = "Must remain true in production (forced in main.tf)."
  type        = bool
  default     = true

  validation {
    condition     = var.enable_vpc_flow_logs
    error_message = "Production requires enable_vpc_flow_logs = true."
  }
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 50
}

variable "db_max_allocated_storage_gb" {
  type    = number
  default = 500
}

variable "db_backup_retention_days" {
  type    = number
  default = 14
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "s3_noncurrent_version_expiration_days" {
  type    = number
  default = 180
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "api_memory_mb" {
  type    = number
  default = 1536
}

variable "worker_memory_mb" {
  type    = number
  default = 3008
}

variable "worker_maximum_concurrency" {
  description = "Max concurrent SQS worker Lambda invocations."
  type        = number
  default     = 10
}

variable "api_reserved_concurrency" {
  description = "Reserved concurrency for the API Lambda."
  type        = number
  default     = 50
}

variable "api_throttle_burst_limit" {
  type    = number
  default = 500
}

variable "api_throttle_rate_limit" {
  type    = number
  default = 200
}

variable "api_image_uri" {
  type     = string
  default  = null
  nullable = true
}

variable "worker_image_uri" {
  type     = string
  default  = null
  nullable = true
}

variable "chroma_url" {
  description = "ChromaDB base URL (OQ-1)."
  type        = string
}

variable "chroma_api_token_secret_arn" {
  type    = string
  default = ""
}

variable "cors_allow_origins" {
  description = "API Gateway CORS allow-origins. Set to the CloudFront frontend_url after the first deploy (avoids a Terraform cycle with module.frontend)."
  type        = list(string)
  default     = []
}

variable "create_github_oidc_provider" {
  description = "Create the account-level GitHub OIDC provider (once per AWS account)."
  type        = bool
  default     = true
}

variable "create_github_deploy_role" {
  description = "Create the production-only GitHub Actions deploy role (OIDC, §5.5)."
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "GitHub org/repo allowed to assume the production deploy role."
  type        = string
  default     = ""
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN when create_github_oidc_provider is false."
  type        = string
  default     = ""
}

variable "github_oidc_subjects" {
  description = "Override OIDC sub patterns. Empty defaults to repo:ORG/REPO:environment:production only."
  type        = list(string)
  default     = []
}

variable "extra_secret_values" {
  type      = map(string)
  default   = {}
  sensitive = true
}

variable "llm_provider" {
  description = "LLM_PROVIDER for deployed API/worker (must not be fake)."
  type        = string
  default     = "openai"
}

variable "embedding_provider" {
  description = "EMBEDDING_PROVIDER for deployed API/worker (must not be fake)."
  type        = string
  default     = "openai"
}

variable "alarm_actions" {
  type    = list(string)
  default = []
}
