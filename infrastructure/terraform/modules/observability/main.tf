# CloudWatch log groups and alarms (§5.4, §57).

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

variable "kms_key_arn" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "api_function_name" {
  description = "API Lambda function name (must match the compute module)."
  type        = string
}

variable "worker_function_name" {
  description = "Worker Lambda function name (must match the compute module)."
  type        = string
}

variable "dlq_name" {
  description = "SQS DLQ name for ApproximateNumberOfMessagesVisible alarm."
  type        = string
}

variable "queue_name" {
  description = "Primary SQS queue name for ApproximateAgeOfOldestMessage alarm. Empty skips the alarm."
  type        = string
  default     = ""
}

variable "alarm_actions" {
  description = "SNS topic ARNs (or empty until alerting is wired)."
  type        = list(string)
  default     = []
}

variable "create_alarm_topic" {
  description = "Create an SNS topic for alarm notifications (KMS-encrypted)."
  type        = bool
  default     = false
}

variable "require_alarm_actions" {
  description = "Fail plan/apply when effective alarm actions would be empty."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  create_topic = var.create_alarm_topic || (var.require_alarm_actions && length(var.alarm_actions) == 0)

  effective_alarm_actions = concat(
    var.alarm_actions,
    local.create_topic ? [aws_sns_topic.alarms[0].arn] : [],
  )
}

check "alarm_actions_required" {
  assert {
    condition     = !var.require_alarm_actions || length(local.effective_alarm_actions) > 0
    error_message = "require_alarm_actions is true but effective alarm_actions is empty; set alarm_actions or create_alarm_topic."
  }
}

resource "aws_sns_topic" "alarms" {
  count = local.create_topic ? 1 : 0

  name              = "${var.name_prefix}-alarms"
  kms_master_key_id = var.kms_key_arn
  tags              = merge(var.tags, { Name = "${var.name_prefix}-alarms" })
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.api_function_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = merge(var.tags, { Name = "${var.name_prefix}-api-logs" })
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${var.worker_function_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = merge(var.tags, { Name = "${var.name_prefix}-worker-logs" })
}

resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/aws/lambda/${var.name_prefix}-migrate"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = merge(var.tags, { Name = "${var.name_prefix}-migrate-logs" })
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${var.name_prefix}-http"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = merge(var.tags, { Name = "${var.name_prefix}-apigw-logs" })
}

resource "aws_cloudwatch_log_resource_policy" "api_gateway" {
  policy_name = "${var.name_prefix}-apigw-logs"

  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowAPIGatewayDelivery"
      Effect = "Allow"
      Principal = {
        Service = "apigateway.amazonaws.com"
      }
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "${aws_cloudwatch_log_group.api_gateway.arn}:*"
      Condition = {
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:apigateway:${data.aws_region.current.region}::/apis/*"
        }
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${var.name_prefix}-api-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "API Lambda errors"
  alarm_actions       = local.effective_alarm_actions
  ok_actions          = local.effective_alarm_actions

  dimensions = {
    FunctionName = var.api_function_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "api_throttles" {
  alarm_name          = "${var.name_prefix}-api-throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "API Lambda throttles"
  alarm_actions       = local.effective_alarm_actions
  ok_actions          = local.effective_alarm_actions

  dimensions = {
    FunctionName = var.api_function_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "worker_errors" {
  alarm_name          = "${var.name_prefix}-worker-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "Worker Lambda errors"
  alarm_actions       = local.effective_alarm_actions
  ok_actions          = local.effective_alarm_actions

  dimensions = {
    FunctionName = var.worker_function_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${var.name_prefix}-documents-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Document processing DLQ is non-empty"
  alarm_actions       = local.effective_alarm_actions
  ok_actions          = local.effective_alarm_actions

  dimensions = {
    QueueName = var.dlq_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  count = var.queue_name != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-documents-queue-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 900
  treat_missing_data  = "notBreaching"
  alarm_description   = "Primary documents queue has an old message (>15m)"
  alarm_actions       = local.effective_alarm_actions
  ok_actions          = local.effective_alarm_actions

  dimensions = {
    QueueName = var.queue_name
  }

  tags = var.tags
}

output "api_log_group_name" {
  value = aws_cloudwatch_log_group.api.name
}

output "worker_log_group_name" {
  value = aws_cloudwatch_log_group.worker.name
}

output "migrate_log_group_name" {
  value = aws_cloudwatch_log_group.migrate.name
}

output "api_gateway_log_group_arn" {
  value = aws_cloudwatch_log_group.api_gateway.arn
}

output "api_gateway_log_group_name" {
  value = aws_cloudwatch_log_group.api_gateway.name
}

output "alarm_topic_arn" {
  description = "SNS alarm topic ARN when create_alarm_topic created one; otherwise null."
  value       = local.create_topic ? aws_sns_topic.alarms[0].arn : null
}

output "effective_alarm_actions" {
  description = "Alarm action ARNs used by this module (input topics + created topic)."
  value       = local.effective_alarm_actions
}
