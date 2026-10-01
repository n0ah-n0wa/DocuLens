# Staging environment root module (SPECIFICATIONS.md §57).
# Does not deploy by itself — run terraform plan/apply from this directory after
# bootstrapping remote state (see ../../README.md).

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# CloudFront WAFv2 web ACLs must be created in us-east-1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.common_tags
  }
}

locals {
  project     = "doculens"
  environment = "staging"
  name_prefix = "${local.project}-${local.environment}"

  common_tags = {
    Project     = local.project
    Environment = local.environment
    ManagedBy   = "terraform"
  }

  api_function_name    = "${local.name_prefix}-api"
  worker_function_name = "${local.name_prefix}-worker"

  secrets_arns = compact([
    module.secrets.app_secret_arn,
    var.chroma_api_token_secret_arn != "" ? var.chroma_api_token_secret_arn : null,
  ])
}

module "networking" {
  source = "../../modules/networking"

  name_prefix                = local.name_prefix
  cidr_block                 = var.vpc_cidr
  az_count                   = var.az_count
  nat_gateway_count          = var.nat_gateway_count
  enable_interface_endpoints = var.enable_interface_endpoints
  enable_vpc_flow_logs       = var.enable_vpc_flow_logs
  flow_logs_retention_days   = var.log_retention_days
  tags                       = local.common_tags
}

module "kms" {
  source = "../../modules/kms"

  name_prefix = local.name_prefix
  tags        = local.common_tags
}

module "ecr" {
  source = "../../modules/ecr"

  name_prefix = local.name_prefix
  kms_key_arn = module.kms.key_arn
  tags        = local.common_tags
}

module "storage" {
  source = "../../modules/storage"

  name_prefix   = local.name_prefix
  kms_key_arn   = module.kms.key_arn
  force_destroy = true
  tags          = local.common_tags
}

module "queue" {
  source = "../../modules/queue"

  name_prefix = local.name_prefix
  kms_key_arn = module.kms.key_arn
  tags        = local.common_tags
}

module "database" {
  source = "../../modules/database"

  name_prefix           = local.name_prefix
  subnet_ids            = module.networking.private_subnet_ids
  security_group_ids    = [module.networking.database_security_group_id]
  kms_key_arn           = module.kms.key_arn
  instance_class        = var.db_instance_class
  multi_az              = false
  backup_retention_days = 3
  deletion_protection   = true
  skip_final_snapshot   = true
  apply_immediately     = true
  tags                  = local.common_tags
}

module "cache" {
  source = "../../modules/cache"

  name_prefix                = local.name_prefix
  subnet_ids                 = module.networking.private_subnet_ids
  security_group_ids         = [module.networking.cache_security_group_id]
  kms_key_arn                = module.kms.key_arn
  node_type                  = var.redis_node_type
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false
  snapshot_retention_limit   = 0
  apply_immediately          = true
  tags                       = local.common_tags
}

module "secrets" {
  source = "../../modules/secrets"

  name_prefix             = local.name_prefix
  kms_key_arn             = module.kms.key_arn
  database_url            = module.database.asyncpg_url
  redis_url               = module.cache.rediss_url
  extra_secret_values     = var.extra_secret_values
  recovery_window_in_days = 0
  tags                    = local.common_tags
}

module "iam" {
  source = "../../modules/iam"

  name_prefix                 = local.name_prefix
  documents_bucket_arn        = module.storage.bucket_arn
  sqs_queue_arn               = module.queue.queue_arn
  sqs_dlq_arn                 = module.queue.dlq_arn
  secrets_arns                = local.secrets_arns
  kms_key_arn                 = module.kms.key_arn
  create_github_oidc_provider = var.create_github_oidc_provider
  create_github_deploy_role   = var.create_github_deploy_role && var.github_repository != ""
  github_repository           = var.github_repository
  github_deploy_environment   = local.environment
  github_oidc_provider_arn    = var.github_oidc_provider_arn
  github_oidc_subjects        = var.github_oidc_subjects
  ecr_repository_arns = [
    module.ecr.api_repository_arn,
    module.ecr.worker_repository_arn,
  ]
  tags = local.common_tags
}

module "observability" {
  source = "../../modules/observability"

  name_prefix          = local.name_prefix
  kms_key_arn          = module.kms.key_arn
  log_retention_days   = var.log_retention_days
  api_function_name    = local.api_function_name
  worker_function_name = local.worker_function_name
  dlq_name             = "${local.name_prefix}-documents-dlq"
  alarm_actions        = var.alarm_actions
  tags                 = local.common_tags
}

module "vector_store" {
  source = "../../modules/vector-store"

  name_prefix                 = local.name_prefix
  security_group_id           = module.networking.vector_store_security_group_id
  chroma_url                  = var.chroma_url
  chroma_api_token_secret_arn = var.chroma_api_token_secret_arn
}

module "frontend" {
  source = "../../modules/frontend"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  name_prefix   = local.name_prefix
  kms_key_arn   = module.kms.key_arn
  force_destroy = true
  price_class   = "PriceClass_100"
  tags          = local.common_tags
}

module "compute" {
  source = "../../modules/compute"

  name_prefix                = local.name_prefix
  environment                = local.environment
  api_image_uri              = coalesce(var.api_image_uri, "${module.ecr.api_repository_url}:pending")
  worker_image_uri           = coalesce(var.worker_image_uri, "${module.ecr.worker_repository_url}:pending")
  api_role_arn               = module.iam.api_role_arn
  worker_role_arn            = module.iam.worker_role_arn
  migrate_role_arn           = module.iam.migrate_role_arn
  subnet_ids                 = module.networking.private_subnet_ids
  security_group_ids         = [module.networking.lambda_security_group_id]
  sqs_queue_arn              = module.queue.queue_arn
  app_secrets_arn            = module.secrets.app_secret_arn
  worker_maximum_concurrency = var.worker_maximum_concurrency

  log_group_api_name     = module.observability.api_log_group_name
  log_group_worker_name  = module.observability.worker_log_group_name
  log_group_migrate_name = module.observability.migrate_log_group_name

  environment_variables = merge(
    {
      STORAGE_BACKEND               = "s3"
      STORAGE_BUCKET                = module.storage.bucket_id
      STORAGE_REGION                = var.aws_region
      STORAGE_ENCRYPTION            = "aws:kms"
      STORAGE_KMS_KEY_ID            = module.kms.key_arn
      STORAGE_EXPECTED_BUCKET_OWNER = data.aws_caller_identity.current.account_id
      QUEUE_BACKEND                 = "sqs"
      QUEUE_SQS_URL                 = module.queue.queue_url
      QUEUE_SQS_DLQ_URL             = module.queue.dlq_url
      QUEUE_SQS_REGION              = var.aws_region
      RATE_LIMIT_BACKEND            = "redis"
      VECTOR_STORE                  = "chroma"
      CHROMA_URL                    = var.chroma_url
      LLM_PROVIDER                  = var.llm_provider
      EMBEDDING_PROVIDER            = var.embedding_provider
      RERANKER_PROVIDER             = "none"
    },
    var.chroma_api_token_secret_arn != "" ? {
      CHROMA_API_TOKEN_SECRET_ARN = var.chroma_api_token_secret_arn
    } : {},
  )

  tags = local.common_tags

  depends_on = [module.observability]
}

module "api_gateway" {
  source = "../../modules/api-gateway"

  name_prefix          = local.name_prefix
  lambda_invoke_arn    = module.compute.api_invoke_arn
  lambda_function_name = module.compute.api_function_name
  access_log_group_arn = module.observability.api_gateway_log_group_arn
  cors_allow_origins = length(var.cors_allow_origins) > 0 ? var.cors_allow_origins : [
    module.frontend.frontend_url,
  ]
  tags = local.common_tags
}

data "aws_caller_identity" "current" {}
