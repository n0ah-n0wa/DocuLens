variable "aws_region" {
  description = "AWS region for every resource in this environment."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Availability zones to use."
  type        = number
  default     = 2
}

variable "nat_gateway_count" {
  description = "NAT gateways (1 is enough for staging cost)."
  type        = number
  default     = 1
}

variable "enable_interface_endpoints" {
  description = "Create interface VPC endpoints. Staging defaults off (NAT is cheaper)."
  type        = bool
  default     = false
}

variable "enable_vpc_flow_logs" {
  description = "Enable VPC flow logs (recommended; modest CloudWatch cost)."
  type        = bool
  default     = true
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "worker_maximum_concurrency" {
  description = "Max concurrent SQS worker Lambda invocations."
  type        = number
  default     = 5
}

variable "api_image_uri" {
  description = "Full ECR image URI for the API Lambda. Leave null until the first image is pushed; apply of Lambda will then require a real digest/tag."
  type        = string
  default     = null
  nullable    = true
}

variable "worker_image_uri" {
  description = "Full ECR image URI for the worker Lambda."
  type        = string
  default     = null
  nullable    = true
}

variable "chroma_url" {
  description = "ChromaDB base URL (OQ-1: external until hosting is decided)."
  type        = string
}

variable "chroma_api_token_secret_arn" {
  description = "Optional Secrets Manager ARN for CHROMA_API_TOKEN."
  type        = string
  default     = ""
}

variable "cors_allow_origins" {
  description = "Browser origins allowed by API Gateway CORS (empty disables CORS at the gateway)."
  type        = list(string)
  default     = []
}

variable "create_github_oidc_provider" {
  description = "Create the account-level GitHub OIDC provider. Keep false in staging when production (or a bootstrap) already created it."
  type        = bool
  default     = false
}

variable "create_github_deploy_role" {
  description = "Create the staging-only GitHub Actions deploy role (OIDC, §5.5)."
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "GitHub org/repo allowed to assume the staging deploy role (e.g. n0ah-n0wa/DocuLens)."
  type        = string
  default     = ""
}

variable "github_oidc_provider_arn" {
  description = "ARN of an existing GitHub OIDC provider when create_github_oidc_provider is false. Empty looks up token.actions.githubusercontent.com."
  type        = string
  default     = ""
}

variable "github_oidc_subjects" {
  description = "Override OIDC sub patterns. Empty defaults to repo:ORG/REPO:environment:staging only."
  type        = list(string)
  default     = []
}

variable "extra_secret_values" {
  description = "Additional secret key/values merged into the app Secrets Manager secret (e.g. LLM API keys). Prefer TF_VAR_extra_secret_values — never commit real values."
  type        = map(string)
  default     = {}
  sensitive   = true
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
  description = "SNS topic ARNs for CloudWatch alarms."
  type        = list(string)
  default     = []
}
