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

variable "alarm_actions" {
  description = "SNS topic ARNs (or empty until alerting is wired)."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

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
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions

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
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions

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
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions

  dimensions = {
    QueueName = var.dlq_name
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
