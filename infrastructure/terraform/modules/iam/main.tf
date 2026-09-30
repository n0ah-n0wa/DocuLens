# Least-privilege IAM roles for API, worker, migrations, and optional GitHub OIDC deploy (§58).

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

variable "documents_bucket_arn" {
  type = string
}

variable "sqs_queue_arn" {
  description = "Primary documents queue ARN (API send + worker consume)."
  type        = string
}

variable "sqs_dlq_arn" {
  description = "Dead-letter queue ARN (worker read for ops; no send from API)."
  type        = string
}

variable "secrets_arns" {
  type = list(string)
}

variable "kms_key_arn" {
  type = string
}

variable "create_github_oidc" {
  description = "Create GitHub Actions OIDC provider and deploy role (§5.5)."
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "org/repo allowed to assume the deploy role via OIDC."
  type        = string
  default     = ""
}

variable "github_oidc_subjects" {
  description = "Allowed token.actions.githubusercontent.com:sub patterns (least privilege)."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  lambda_assume = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  github_subjects = length(var.github_oidc_subjects) > 0 ? var.github_oidc_subjects : [
    "repo:${var.github_repository}:ref:refs/heads/main",
    "repo:${var.github_repository}:environment:*",
  ]
}

resource "aws_iam_role" "api" {
  name_prefix        = "${var.name_prefix}-api-"
  assume_role_policy = local.lambda_assume
  tags               = merge(var.tags, { Name = "${var.name_prefix}-api-role" })
}

resource "aws_iam_role" "worker" {
  name_prefix        = "${var.name_prefix}-worker-"
  assume_role_policy = local.lambda_assume
  tags               = merge(var.tags, { Name = "${var.name_prefix}-worker-role" })
}

resource "aws_iam_role" "migrate" {
  name_prefix        = "${var.name_prefix}-migrate-"
  assume_role_policy = local.lambda_assume
  tags               = merge(var.tags, { Name = "${var.name_prefix}-migrate-role" })
}

resource "aws_iam_role_policy_attachment" "api_basic" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "api_vpc" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "worker_basic" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "worker_vpc" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "migrate_basic" {
  role       = aws_iam_role.migrate.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "migrate_vpc" {
  role       = aws_iam_role.migrate.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "api" {
  name = "${var.name_prefix}-api"
  role = aws_iam_role.api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DocumentsListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:HeadBucket"]
        Resource = [var.documents_bucket_arn]
      },
      {
        Sid      = "DocumentsObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = ["${var.documents_bucket_arn}/*"]
      },
      {
        Sid      = "EnqueueJobs"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
        Resource = [var.sqs_queue_arn]
      },
      {
        Sid      = "ReadSecrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = var.secrets_arns
      },
      {
        Sid      = "UseKms"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey"]
        Resource = [var.kms_key_arn]
      },
      {
        Sid      = "XRay"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_iam_role_policy" "worker" {
  name = "${var.name_prefix}-worker"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DocumentsListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:HeadBucket"]
        Resource = [var.documents_bucket_arn]
      },
      {
        Sid      = "DocumentsObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${var.documents_bucket_arn}/*"]
      },
      {
        Sid    = "ConsumeAndRequeueJobs"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
          "sqs:SendMessage",
        ]
        Resource = [var.sqs_queue_arn]
      },
      {
        Sid      = "InspectDlq"
        Effect   = "Allow"
        Action   = ["sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ReceiveMessage"]
        Resource = [var.sqs_dlq_arn]
      },
      {
        Sid      = "ReadSecrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = var.secrets_arns
      },
      {
        Sid      = "UseKms"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey"]
        Resource = [var.kms_key_arn]
      },
      {
        Sid      = "XRay"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_iam_role_policy" "migrate" {
  name = "${var.name_prefix}-migrate"
  role = aws_iam_role.migrate.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadSecrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = var.secrets_arns
      },
      {
        Sid      = "DecryptSecrets"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = [var.kms_key_arn]
      }
    ]
  })
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]

  tags = merge(var.tags, { Name = "${var.name_prefix}-github-oidc" })
}

resource "aws_iam_role" "deploy" {
  count = var.create_github_oidc && var.github_repository != "" ? 1 : 0

  name_prefix = "${var.name_prefix}-deploy-"
  tags        = merge(var.tags, { Name = "${var.name_prefix}-deploy-role" })

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github[0].arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = local.github_subjects
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  count = length(aws_iam_role.deploy)

  name = "${var.name_prefix}-deploy"
  role = aws_iam_role.deploy[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ECRPushAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = ["*"]
      },
      {
        Sid    = "ECRRepo"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
          "ecr:DescribeRepositories",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${var.name_prefix}-api",
          "arn:${data.aws_partition.current.partition}:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${var.name_prefix}-worker",
        ]
      },
      {
        Sid    = "LambdaUpdate"
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:PublishVersion",
          "lambda:InvokeFunction",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-api",
          "arn:${data.aws_partition.current.partition}:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-worker",
          "arn:${data.aws_partition.current.partition}:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-migrate",
        ]
      }
    ]
  })
}

output "api_role_arn" {
  value = aws_iam_role.api.arn
}

output "api_role_name" {
  value = aws_iam_role.api.name
}

output "worker_role_arn" {
  value = aws_iam_role.worker.arn
}

output "migrate_role_arn" {
  value = aws_iam_role.migrate.arn
}

output "deploy_role_arn" {
  description = "GitHub Actions deploy role ARN (null when OIDC is disabled)."
  value       = try(aws_iam_role.deploy[0].arn, null)
}
