# SQS document-processing queue and DLQ (ADR-006, §48).

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

variable "visibility_timeout_seconds" {
  description = "Must exceed the worker processing budget for a single job."
  type        = number
  default     = 900
}

variable "message_retention_seconds" {
  type    = number
  default = 1209600 # 14 days
}

variable "max_receive_count" {
  description = "Receives before redrive to the DLQ."
  type        = number
  default     = 5
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_sqs_queue" "dlq" {
  name              = "${var.name_prefix}-documents-dlq"
  kms_master_key_id = var.kms_key_arn

  message_retention_seconds = var.message_retention_seconds

  tags = merge(var.tags, { Name = "${var.name_prefix}-documents-dlq" })
}

resource "aws_sqs_queue" "documents" {
  name              = "${var.name_prefix}-documents"
  kms_master_key_id = var.kms_key_arn

  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = merge(var.tags, { Name = "${var.name_prefix}-documents" })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.documents.arn]
  })
}

output "queue_url" {
  description = "Primary documents queue URL."
  value       = aws_sqs_queue.documents.url
}

output "queue_arn" {
  description = "Primary documents queue ARN."
  value       = aws_sqs_queue.documents.arn
}

output "dlq_url" {
  description = "Dead-letter queue URL."
  value       = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.dlq.arn
}
