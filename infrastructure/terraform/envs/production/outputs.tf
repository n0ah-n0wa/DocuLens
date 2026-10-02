output "vpc_id" {
  value = module.networking.vpc_id
}

output "private_subnet_ids" {
  value = module.networking.private_subnet_ids
}

output "documents_bucket_id" {
  value = module.storage.bucket_id
}

output "documents_bucket_arn" {
  value = module.storage.bucket_arn
}

output "kms_key_arn" {
  value = module.kms.key_arn
}

output "rds_endpoint" {
  value = module.database.endpoint
}

output "redis_primary_endpoint" {
  value = module.cache.primary_endpoint
}

output "sqs_queue_url" {
  value = module.queue.queue_url
}

output "sqs_dlq_url" {
  value = module.queue.dlq_url
}

output "app_secrets_arn" {
  value = module.secrets.app_secret_arn
}

output "ecr_api_repository_url" {
  value = module.ecr.api_repository_url
}

output "ecr_worker_repository_url" {
  value = module.ecr.worker_repository_url
}

output "api_lambda_function_name" {
  value = module.compute.api_function_name
}

output "worker_lambda_function_name" {
  value = module.compute.worker_function_name
}

output "migrate_lambda_function_name" {
  value = module.compute.migrate_function_name
}

output "api_gateway_endpoint" {
  description = "HTTPS invoke URL for the production HTTP API."
  value       = module.api_gateway.api_endpoint
}

output "frontend_bucket_id" {
  value = module.frontend.bucket_id
}

output "frontend_distribution_id" {
  value = module.frontend.distribution_id
}

output "frontend_url" {
  description = "CloudFront URL for the production web app."
  value       = module.frontend.frontend_url
}

output "deploy_role_arn" {
  description = "GitHub Actions OIDC deploy role for production (null if disabled)."
  value       = module.iam.deploy_role_arn
}

output "deploy_role_name" {
  description = "GitHub Actions OIDC deploy role name for production (null if disabled)."
  value       = module.iam.deploy_role_name
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider ARN (created here when create_github_oidc_provider is true)."
  value       = module.iam.github_oidc_provider_arn
}

output "github_oidc_subjects" {
  description = "OIDC sub claim patterns trusted by the production deploy role."
  value       = module.iam.github_oidc_subjects
}

output "vector_store_status" {
  value = module.vector_store.status
}

output "vector_store_notes" {
  value = module.vector_store.notes
}

output "alarm_topic_arn" {
  description = "SNS topic for CloudWatch alarms (subscribe operators before go-live)."
  value       = module.observability.alarm_topic_arn
}

output "api_waf_acl_arn" {
  description = "Regional WAFv2 Web ACL ARN for the HTTP API."
  value       = module.api_gateway.waf_acl_arn
}
