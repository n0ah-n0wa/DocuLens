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

variable "create_github_oidc" {
  description = "Create GitHub Actions OIDC provider + deploy role."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub org/repo allowed to assume the deploy role (e.g. doculens/doculens)."
  type        = string
  default     = ""
}

variable "github_oidc_subjects" {
  description = "Override OIDC sub patterns (empty uses main branch + environments)."
  type        = list(string)
  default     = []
}

variable "extra_secret_values" {
  description = "Additional secret key/values merged into the app Secrets Manager secret (e.g. LLM API keys). Prefer TF_VAR_extra_secret_values — never commit real values."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "alarm_actions" {
  description = "SNS topic ARNs for CloudWatch alarms."
  type        = list(string)
  default     = []
}
