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

variable "create_github_oidc_provider" {
  description = "Create the account-level GitHub Actions OIDC identity provider (§5.5). At most one per AWS account."
  type        = bool
  default     = false
}

variable "create_github_deploy_role" {
  description = "Create this environment's dedicated GitHub Actions deploy role (§5.5, §58)."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "org/repo allowed to assume the deploy role via OIDC (e.g. n0ah-n0wa/DocuLens)."
  type        = string
  default     = ""

  validation {
    condition     = var.github_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be empty or look like org/repo."
  }
}

variable "github_deploy_environment" {
  description = "GitHub Environment name that may assume this deploy role (staging | production). Required when create_github_deploy_role is true."
  type        = string
  default     = ""

  validation {
    condition     = var.github_deploy_environment == "" || contains(["staging", "production"], var.github_deploy_environment)
    error_message = "github_deploy_environment must be empty, \"staging\", or \"production\"."
  }
}

variable "github_oidc_subjects" {
  description = "Override token.actions.githubusercontent.com:sub patterns. Empty defaults to repo:ORG/REPO:environment:ENV only (no branch wildcards)."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for s in var.github_oidc_subjects :
      length(regexall("\\*", s)) == 0
    ])
    error_message = "github_oidc_subjects must not contain wildcards (*); pin exact repository and environment subjects."
  }

  validation {
    condition = alltrue([
      for s in var.github_oidc_subjects :
      can(regex("^repo:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:environment:(staging|production)$", s))
    ])
    error_message = "github_oidc_subjects entries must match repo:ORG/REPO:environment:staging|production (Environment-gated OIDC only)."
  }
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN when create_github_oidc_provider is false. Empty looks up token.actions.githubusercontent.com in-account."
  type        = string
  default     = ""
}

variable "ecr_repository_arns" {
  description = "ECR repository ARNs this deploy role may push to (environment-scoped)."
  type        = list(string)
  default     = []
}

variable "lambda_function_arns" {
  description = "Lambda function ARNs this deploy role may update (environment-scoped)."
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

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_deploy_role && !var.create_github_oidc_provider && var.github_oidc_provider_arn == "" ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

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

  # Least privilege: only the named GitHub Environment for this Terraform env may assume.
  # Branch refs (refs/heads/*) and environment:* wildcards are intentionally excluded.
  github_subjects = length(var.github_oidc_subjects) > 0 ? var.github_oidc_subjects : [
    "repo:${var.github_repository}:environment:${var.github_deploy_environment}",
  ]

  github_oidc_provider_arn = (
    var.create_github_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : (
      var.github_oidc_provider_arn != ""
      ? var.github_oidc_provider_arn
      : try(data.aws_iam_openid_connect_provider.github[0].arn, null)
    )
  )

  # Function/repo names are deterministic (${name_prefix}-*); compute is applied after IAM, so
  # ARNs are composed here unless the caller passes explicit lists.
  default_ecr_repository_arns = [
    "arn:${data.aws_partition.current.partition}:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${var.name_prefix}-api",
    "arn:${data.aws_partition.current.partition}:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${var.name_prefix}-worker",
  ]
  default_lambda_function_arns = [
    "arn:${data.aws_partition.current.partition}:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-api",
    "arn:${data.aws_partition.current.partition}:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-worker",
    "arn:${data.aws_partition.current.partition}:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-migrate",
  ]
  deploy_ecr_repository_arns  = length(var.ecr_repository_arns) > 0 ? var.ecr_repository_arns : local.default_ecr_repository_arns
  deploy_lambda_function_arns = length(var.lambda_function_arns) > 0 ? var.lambda_function_arns : local.default_lambda_function_arns
}

check "github_deploy_role_inputs" {
  assert {
    condition = !var.create_github_deploy_role || (
      var.github_repository != "" && var.github_deploy_environment != ""
    )
    error_message = "create_github_deploy_role requires github_repository and github_deploy_environment."
  }
}

resource "aws_iam_policy" "lambda_permissions_boundary" {
  name_prefix = "${var.name_prefix}-lambda-boundary-"
  description = "Permissions boundary for ${var.name_prefix} Lambda execution roles."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAppSurface"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:HeadBucket",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
          "sqs:ChangeMessageVisibility",
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:GenerateDataKey",
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
        ]
        Resource = ["*"]
      },
      {
        Sid      = "DenyPrivilegeEscalation"
        Effect   = "Deny"
        Action   = ["iam:*", "organizations:*", "account:*"]
        Resource = ["*"]
      },
    ]
  })
  tags = merge(var.tags, { Name = "${var.name_prefix}-lambda-boundary" })
}

resource "aws_iam_role" "api" {
  name_prefix          = "${var.name_prefix}-api-"
  assume_role_policy   = local.lambda_assume
  permissions_boundary = aws_iam_policy.lambda_permissions_boundary.arn
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-api-role" })
}

resource "aws_iam_role" "worker" {
  name_prefix          = "${var.name_prefix}-worker-"
  assume_role_policy   = local.lambda_assume
  permissions_boundary = aws_iam_policy.lambda_permissions_boundary.arn
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-worker-role" })
}

resource "aws_iam_role" "migrate" {
  name_prefix          = "${var.name_prefix}-migrate-"
  assume_role_policy   = local.lambda_assume
  permissions_boundary = aws_iam_policy.lambda_permissions_boundary.arn
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-migrate-role" })
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
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub documents these intermediate CA thumbprints; AWS still requires the list on the IdP.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]

  tags = merge(var.tags, { Name = "${var.name_prefix}-github-oidc" })
}

resource "aws_iam_role" "deploy" {
  count = var.create_github_deploy_role ? 1 : 0

  name_prefix          = "${var.name_prefix}-deploy-"
  description          = "GitHub Actions OIDC deploy role for ${var.github_deploy_environment} (${var.github_repository})."
  max_session_duration = 3600
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-deploy-role" })

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "GitHubActionsOidc"
      Effect = "Allow"
      Principal = {
        Federated = local.github_oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        # Exact Environment-scoped subjects only (ADR-022); never refs/heads/* or environment:*.
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
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
        Resource = ["*"] # GetAuthorizationToken cannot be resource-scoped
      },
      {
        Sid    = "ECRRepo"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages",
          "ecr:DescribeRepositories",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = local.deploy_ecr_repository_arns
      },
      {
        Sid    = "EcrKms"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:Encrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [var.kms_key_arn]
      },
      {
        Sid    = "LambdaUpdate"
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:PublishVersion",
          "lambda:InvokeFunction",
        ]
        Resource = local.deploy_lambda_function_arns
      },
      {
        # Terraform plan/apply + frontend sync. Assumable only via Environment-scoped OIDC.
        # Resource "*" is required for many control-plane APIs; shared-account blast radius is
        # reduced by prefixed IAM/Secrets statements, Lambda permissions boundaries, and an
        # explicit deny on mutating sibling environment prefixes.
        Sid    = "TerraformAndFrontendDeploy"
        Effect = "Allow"
        Action = [
          "apigateway:*",
          "cloudfront:*",
          "dynamodb:*",
          "ec2:*",
          "ecr:*",
          "elasticache:*",
          "kms:*",
          "lambda:*",
          "logs:*",
          "rds:*",
          "s3:*",
          "sns:*",
          "sqs:*",
          "ssm:GetParameter",
          "ssm:GetParameters",
          "wafv2:*",
          "xray:GetTraceSummaries",
          "xray:BatchGetTraces",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "DenySiblingEnvironmentBlastRadius"
        Effect = "Deny"
        Action = [
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:DeleteFunction",
          "lambda:PublishVersion",
          "lambda:InvokeFunction",
          "s3:DeleteObject",
          "s3:DeleteObjectVersion",
          "s3:PutBucketPolicy",
          "s3:DeleteBucket",
          "rds:DeleteDBInstance",
          "rds:ModifyDBInstance",
          "elasticache:DeleteReplicationGroup",
          "elasticache:ModifyReplicationGroup",
          "secretsmanager:DeleteSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
        ]
        NotResource = [
          "arn:${data.aws_partition.current.partition}:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-*",
          "arn:${data.aws_partition.current.partition}:s3:::${var.name_prefix}-*",
          "arn:${data.aws_partition.current.partition}:s3:::${var.name_prefix}-*/*",
          "arn:${data.aws_partition.current.partition}:rds:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:db:${var.name_prefix}-*",
          "arn:${data.aws_partition.current.partition}:elasticache:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:replicationgroup:${var.name_prefix}-*",
          "arn:${data.aws_partition.current.partition}:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}-*",
        ]
      },
      {
        Sid    = "DeployIamRead"
        Effect = "Allow"
        Action = [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:GetOpenIDConnectProvider",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicyVersions",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "DeployIamMutatePrefixedRoles"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:UpdateAssumeRolePolicy",
          "iam:PutRolePermissionsBoundary",
          "iam:DeleteRolePermissionsBoundary",
          "iam:CreatePolicy",
          "iam:DeletePolicy",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicyVersion",
          "iam:TagPolicy",
          "iam:UntagPolicy",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.name_prefix}-*",
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${var.name_prefix}-*",
        ]
      },
      {
        Sid    = "DeployDenyCreateRoleWithoutBoundary"
        Effect = "Deny"
        Action = ["iam:CreateRole"]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.name_prefix}-*",
        ]
        Condition = {
          Null = {
            "iam:PermissionsBoundary" = "true"
          }
        }
      },
      {
        Sid    = "DeployRequireLambdaPermissionsBoundary"
        Effect = "Deny"
        Action = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.name_prefix}-*",
        ]
        Condition = {
          StringNotEquals = {
            "iam:PermissionsBoundary" = aws_iam_policy.lambda_permissions_boundary.arn
          }
        }
      },
      {
        Sid    = "DeployAttachOnlyLambdaServiceRoles"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.name_prefix}-*",
        ]
        Condition = {
          ArnEquals = {
            "iam:PolicyARN" = [
              "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
              "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            ]
          }
        }
      },
      {
        Sid    = "DeployPassRoleToLambdaOnly"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.name_prefix}-*",
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "lambda.amazonaws.com"
          }
        }
      },
      {
        Sid    = "DeploySecretsManagerPrefixed"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:TagResource",
          "secretsmanager:UntagResource",
          "secretsmanager:GetResourcePolicy",
          "secretsmanager:PutResourcePolicy",
          "secretsmanager:DeleteResourcePolicy",
          "secretsmanager:ListSecretVersionIds",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}-*",
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

output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider ARN used by the deploy role (null when unused)."
  value       = var.create_github_deploy_role || var.create_github_oidc_provider ? local.github_oidc_provider_arn : null
}

output "deploy_role_arn" {
  description = "GitHub Actions deploy role ARN (null when the role is not created)."
  value       = try(aws_iam_role.deploy[0].arn, null)
}

output "deploy_role_name" {
  description = "GitHub Actions deploy role name (null when the role is not created)."
  value       = try(aws_iam_role.deploy[0].name, null)
}

output "github_oidc_subjects" {
  description = "Effective OIDC sub claim patterns bound to the deploy role."
  value       = var.create_github_deploy_role ? local.github_subjects : []
}
