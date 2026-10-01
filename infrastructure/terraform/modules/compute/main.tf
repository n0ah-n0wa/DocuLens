# Lambda compute — API (HTTP), worker (SQS), optional migrations runner (§5.4, OQ-2/OQ-23).
# Functions use container images; image URIs are supplied by CD after `docker build`.
# Secrets stay in Secrets Manager — only the secret ARN is injected into the environment.

terraform {
  required_version = "~> 1.16.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0.0, < 7.0.0"
    }
  }
}

variable "name_prefix" {
  type = string
}

variable "environment" {
  description = "APP_ENV value (staging|production)."
  type        = string
}

variable "api_image_uri" {
  description = "ECR image URI for the API Lambda (tag or digest)."
  type        = string
}

variable "worker_image_uri" {
  description = "ECR image URI for the worker Lambda."
  type        = string
}

variable "migrate_image_uri" {
  description = "ECR image URI for one-shot migrations (defaults to API image)."
  type        = string
  default     = ""
}

variable "api_role_arn" {
  type = string
}

variable "worker_role_arn" {
  type = string
}

variable "migrate_role_arn" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "sqs_queue_arn" {
  type = string
}

variable "app_secrets_arn" {
  description = "Secrets Manager ARN holding JWT_SECRET, DATABASE_URL, REDIS_URL (and extras)."
  type        = string
}

variable "environment_variables" {
  description = "Non-secret environment variables shared by runtimes."
  type        = map(string)
  default     = {}
}

variable "api_memory_mb" {
  type    = number
  default = 1024
}

variable "worker_memory_mb" {
  type    = number
  default = 2048
}

variable "api_timeout_seconds" {
  type    = number
  default = 29
}

variable "worker_timeout_seconds" {
  type    = number
  default = 900
}

variable "worker_maximum_concurrency" {
  description = "SQS event-source max concurrent worker invocations (cost / poison-storm guard)."
  type        = number
  default     = 5

  validation {
    condition     = var.worker_maximum_concurrency >= 2 && var.worker_maximum_concurrency <= 1000
    error_message = "worker_maximum_concurrency must be between 2 and 1000."
  }
}

variable "api_reserved_concurrency" {
  description = "Optional reserved concurrency for the API Lambda (null = unreserved)."
  type        = number
  default     = null
  nullable    = true
}

variable "log_group_api_name" {
  type = string
}

variable "log_group_worker_name" {
  type = string
}

variable "log_group_migrate_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  migrate_image = var.migrate_image_uri != "" ? var.migrate_image_uri : var.api_image_uri

  # Do not set AWS_REGION — it is reserved by the Lambda runtime.
  common_env = merge(
    {
      APP_ENV         = var.environment
      APP_SECRETS_ARN = var.app_secrets_arn
    },
    var.environment_variables,
  )

  # Custom Python images need the Runtime Interface Client as entrypoint (OQ-2 provisional).
  lambda_entry_point = ["/opt/venv/bin/python", "-m", "awslambdaric"]
}

resource "aws_lambda_function" "api" {
  function_name                  = "${var.name_prefix}-api"
  role                           = var.api_role_arn
  package_type                   = "Image"
  image_uri                      = var.api_image_uri
  memory_size                    = var.api_memory_mb
  timeout                        = var.api_timeout_seconds
  architectures                  = ["x86_64"]
  reserved_concurrent_executions = var.api_reserved_concurrency

  image_config {
    entry_point = local.lambda_entry_point
    command     = ["doculens_api.lambda_handler.handler"]
  }

  environment {
    variables = local.common_env
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }

  tracing_config {
    mode = "Active"
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-api" })
}

resource "aws_lambda_function" "worker" {
  function_name = "${var.name_prefix}-worker"
  role          = var.worker_role_arn
  package_type  = "Image"
  image_uri     = var.worker_image_uri
  memory_size   = var.worker_memory_mb
  timeout       = var.worker_timeout_seconds
  architectures = ["x86_64"]

  image_config {
    entry_point = local.lambda_entry_point
    command     = ["doculens_worker.lambda_handler.handler"]
  }

  environment {
    variables = local.common_env
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }

  tracing_config {
    mode = "Active"
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-worker" })
}

resource "aws_lambda_event_source_mapping" "worker_sqs" {
  event_source_arn                   = var.sqs_queue_arn
  function_name                      = aws_lambda_function.worker.arn
  batch_size                         = 1
  enabled                            = true
  function_response_types            = ["ReportBatchItemFailures"]
  bisect_batch_on_function_error     = false
  maximum_batching_window_in_seconds = 0

  scaling_config {
    maximum_concurrency = var.worker_maximum_concurrency
  }
}

resource "aws_lambda_function" "migrate" {
  function_name = "${var.name_prefix}-migrate"
  role          = var.migrate_role_arn
  package_type  = "Image"
  image_uri     = local.migrate_image
  memory_size   = 512
  timeout       = 300
  architectures = ["x86_64"]

  image_config {
    entry_point = local.lambda_entry_point
    command     = ["doculens_api.migrate_handler.handler"]
  }

  environment {
    variables = local.common_env
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-migrate" })
}

# Keep unused log group name variables referenced so the root module can pass them
# and document the dependency order without a hard depends_on cycle.
output "log_group_bindings" {
  description = "Log group names expected by these functions (observability module)."
  value = {
    api     = var.log_group_api_name
    worker  = var.log_group_worker_name
    migrate = var.log_group_migrate_name
  }
}

output "api_function_name" {
  value = aws_lambda_function.api.function_name
}

output "api_function_arn" {
  value = aws_lambda_function.api.arn
}

output "api_invoke_arn" {
  value = aws_lambda_function.api.invoke_arn
}

output "worker_function_name" {
  value = aws_lambda_function.worker.function_name
}

output "worker_function_arn" {
  value = aws_lambda_function.worker.arn
}

output "migrate_function_name" {
  value = aws_lambda_function.migrate.function_name
}

output "migrate_function_arn" {
  value = aws_lambda_function.migrate.arn
}
