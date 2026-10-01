variable "aws_region" {
  description = "AWS region for every resource in this environment."
  type        = string
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
  description = "NAT gateways (2 covers multi-AZ without paying for a third)."
  type        = number
  default     = 2
}

variable "enable_interface_endpoints" {
  description = "Create interface VPC endpoints (recommended in production)."
  type        = bool
  default     = true
}

variable "enable_vpc_flow_logs" {
  description = "Enable VPC flow logs."
  type        = bool
  default     = true
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
  description = "Optional reserved concurrency for the API Lambda."
  type        = number
  default     = null
  nullable    = true
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
  type    = list(string)
  default = []
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
