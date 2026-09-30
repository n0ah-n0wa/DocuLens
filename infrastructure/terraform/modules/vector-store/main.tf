# Vector store hosting placeholder (OQ-1). Provisions no Chroma servers yet;
# exposes networking hooks and documents the operator-supplied endpoint.

terraform {
  required_version = "~> 1.16.0"
}

variable "name_prefix" {
  type = string
}

variable "security_group_id" {
  description = "Security group reserved for a future Chroma workload."
  type        = string
}

variable "chroma_url" {
  description = "Operator-supplied Chroma HTTP URL until OQ-1 is closed (ECS/EC2/Cloud)."
  type        = string
  default     = ""
}

variable "chroma_api_token_secret_arn" {
  description = "Optional Secrets Manager ARN holding CHROMA_API_TOKEN."
  type        = string
  default     = ""
}

output "status" {
  description = "Hosting status for ChromaDB."
  value       = "deferred-oq-1"
}

output "security_group_id" {
  value = var.security_group_id
}

output "chroma_url" {
  value = var.chroma_url
}

output "chroma_api_token_secret_arn" {
  value = var.chroma_api_token_secret_arn
}

output "notes" {
  value = "ChromaDB hosting is open (docs/planning/open-questions.md OQ-1). Pass chroma_url for an external server; ECS/Fargate module lands when OQ-1 closes."
}
