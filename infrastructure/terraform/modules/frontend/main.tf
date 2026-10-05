# Static Next.js frontend — private S3 origin behind CloudFront (OQ-19 provisional, §5.4).
# Secrets never land in this bucket; the SPA calls the API Gateway origin only.
# CloudFront WAFv2 ACLs must live in us-east-1 (AWS requirement).

terraform {
  required_version = "~> 1.16.0"
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      version               = ">= 6.0.0, < 7.0.0"
      configuration_aliases = [aws.us_east_1]
    }
  }
}

variable "name_prefix" {
  type = string
}

variable "kms_key_arn" {
  description = "CMK for the static web bucket (SSE-KMS)."
  type        = string
}

variable "price_class" {
  description = "CloudFront price class (Cost: PriceClass_100 for staging)."
  type        = string
  default     = "PriceClass_100"
}

variable "force_destroy" {
  type    = bool
  default = false
}

variable "api_origin" {
  description = "API origin for CSP connect-src (e.g. https://xxxx.execute-api.region.amazonaws.com)."
  type        = string
  default     = ""
}

variable "waf_rate_limit" {
  description = "WAFv2 rate-based rule limit (requests per 5-minute window per IP)."
  type        = number
  default     = 2000
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  connect_src = var.api_origin != "" ? "'self' ${trimsuffix(var.api_origin, "/")}" : "'self' https://*.execute-api.${data.aws_region.current.region}.amazonaws.com"
  # unsafe-inline retained for Next.js static export; unsafe-eval removed.
  csp = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src ${local.connect_src}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
}

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

data "aws_canonical_user_id" "current" {}

data "aws_cloudfront_log_delivery_canonical_user_id" "current" {}

resource "aws_s3_bucket" "web" {
  bucket_prefix = "${var.name_prefix}-web-"
  force_destroy = var.force_destroy
  tags          = merge(var.tags, { Name = "${var.name_prefix}-web" })
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket = aws_s3_bucket.web.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "web" {
  bucket = aws_s3_bucket.web.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

# CloudFront standard access logs require SSE-S3 (not SSE-KMS) and ACL grants.
resource "aws_s3_bucket" "web_logs" {
  bucket_prefix = "${var.name_prefix}-web-logs-"
  force_destroy = var.force_destroy
  tags          = merge(var.tags, { Name = "${var.name_prefix}-web-logs" })
}

resource "aws_s3_bucket_ownership_controls" "web_logs" {
  bucket = aws_s3_bucket.web_logs.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "web_logs" {
  depends_on = [aws_s3_bucket_ownership_controls.web_logs]
  bucket     = aws_s3_bucket.web_logs.id

  access_control_policy {
    owner {
      id = data.aws_canonical_user_id.current.id
    }
    grant {
      grantee {
        id   = data.aws_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }
    grant {
      grantee {
        id   = data.aws_cloudfront_log_delivery_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "web_logs" {
  bucket = aws_s3_bucket.web_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "web_logs" {
  bucket = aws_s3_bucket.web_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

#trivy:ignore:AVD-AWS-0132 CloudFront access-log destinations must use SSE-S3 (AES256), not CMK/SSE-KMS.
resource "aws_s3_bucket_server_side_encryption_configuration" "web_logs" {
  bucket = aws_s3_bucket.web_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "web_logs" {
  bucket = aws_s3_bucket.web_logs.id

  rule {
    id     = "expire-access-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
  }
}

resource "aws_s3_bucket_policy" "web_logs" {
  bucket = aws_s3_bucket.web_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.web_logs.arn,
          "${aws_s3_bucket.web_logs.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.web_logs]
}

# Separate S3 access-logs bucket — CloudFront log ACL grants conflict with BucketOwnerEnforced
# destinations used for S3 server access logging.
resource "aws_s3_bucket" "web_s3_access_logs" {
  bucket_prefix = "${var.name_prefix}-web-s3-logs-"
  force_destroy = var.force_destroy
  tags          = merge(var.tags, { Name = "${var.name_prefix}-web-s3-access-logs" })
}

resource "aws_s3_bucket_ownership_controls" "web_s3_access_logs" {
  bucket = aws_s3_bucket.web_s3_access_logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "web_s3_access_logs" {
  bucket = aws_s3_bucket.web_s3_access_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "web_s3_access_logs" {
  bucket = aws_s3_bucket.web_s3_access_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

#trivy:ignore:AVD-AWS-0132 S3 server access-log destinations must use SSE-S3 (AES256), not CMK/SSE-KMS.
resource "aws_s3_bucket_server_side_encryption_configuration" "web_s3_access_logs" {
  bucket = aws_s3_bucket.web_s3_access_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "web_s3_access_logs" {
  bucket = aws_s3_bucket.web_s3_access_logs.id

  rule {
    id     = "expire-s3-access-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
  }
}

resource "aws_s3_bucket_policy" "web_s3_access_logs" {
  bucket = aws_s3_bucket.web_s3_access_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.web_s3_access_logs.arn,
          "${aws_s3_bucket.web_s3_access_logs.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid    = "AllowS3LogDeliveryWrite"
        Effect = "Allow"
        Principal = {
          Service = "logging.s3.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.web_s3_access_logs.arn}/*"
        Condition = {
          ArnLike = {
            "aws:SourceArn" = aws_s3_bucket.web.arn
          }
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.web_s3_access_logs]
}

resource "aws_s3_bucket_logging" "web" {
  bucket = aws_s3_bucket.web.id

  target_bucket = aws_s3_bucket.web_s3_access_logs.id
  target_prefix = "s3-access/"

  depends_on = [aws_s3_bucket_policy.web_s3_access_logs]
}

resource "aws_wafv2_web_acl" "web" {
  provider = aws.us_east_1

  name  = "${var.name_prefix}-web"
  scope = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 0

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-web-ip-rep"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-web-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-web-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-web-sqli"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "RateLimitPerIP"
    priority = 4

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-web-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-web"
    sampled_requests_enabled   = true
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-web-waf" })
}

# WAFv2 CloudFront logging requires a log group in us-east-1 with a specific name prefix.
resource "aws_cloudwatch_log_group" "waf" {
  provider = aws.us_east_1

  name              = "aws-waf-logs-${var.name_prefix}-web"
  retention_in_days = 90
  tags              = merge(var.tags, { Name = "${var.name_prefix}-web-waf-logs" })
}

resource "aws_cloudwatch_log_resource_policy" "waf" {
  provider = aws.us_east_1

  policy_name = "${var.name_prefix}-web-waf-logs"

  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSLogDeliveryWrite"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "logs:PutLogEvents"
        Resource = "${aws_cloudwatch_log_group.waf.arn}:*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:logs:us-east-1:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      },
      {
        Sid    = "AWSLogDeliveryCreateLogStream"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "logs:CreateLogStream"
        Resource = "${aws_cloudwatch_log_group.waf.arn}:*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:logs:us-east-1:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      }
    ]
  })
}

resource "aws_wafv2_web_acl_logging_configuration" "web" {
  provider = aws.us_east_1

  resource_arn            = aws_wafv2_web_acl.web.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]

  depends_on = [aws_cloudwatch_log_resource_policy.waf]
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.name_prefix}-web-oac"
  description                       = "OAC for ${var.name_prefix} static frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_response_headers_policy" "web" {
  name    = "${var.name_prefix}-web-security"
  comment = "Browser security headers for ${var.name_prefix} static frontend"

  security_headers_config {
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
    content_security_policy {
      content_security_policy = local.csp
      override                = true
    }
  }

  custom_headers_config {
    items {
      header   = "permissions-policy"
      value    = "camera=(), microphone=(), geolocation=(), payment=()"
      override = true
    }
    items {
      header   = "x-permitted-cross-domain-policies"
      value    = "none"
      override = true
    }
  }
}

resource "aws_cloudfront_function" "spa_deep_links" {
  name    = "${var.name_prefix}-spa-deep-links"
  runtime = "cloudfront-js-2.0"
  comment = "Rewrite static-export dynamic shells (chat/documents/collections UUIDs)"
  publish = true
  code    = <<-EOF
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      // Next.js output:export with trailingSlash emits /chat/_/index.html etc.
      if (uri.match(/^\/chat\/(?!_\/)[^/]+\/?$/)) {
        request.uri = "/chat/_/index.html";
        return request;
      }
      if (uri.match(/^\/documents\/(?!upload\/)(?!_\/)[^/]+\/?$/)) {
        request.uri = "/documents/_/index.html";
        return request;
      }
      if (uri.match(/^\/collections\/(?!_\/)[^/]+\/?$/)) {
        request.uri = "/collections/_/index.html";
        return request;
      }
      return request;
    }
  EOF
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name_prefix} frontend"
  default_root_object = "index.html"
  price_class         = var.price_class
  wait_for_deployment = false
  web_acl_id          = aws_wafv2_web_acl.web.arn
  tags                = merge(var.tags, { Name = "${var.name_prefix}-web-cdn" })

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "web-s3"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    response_headers_policy_id = aws_cloudfront_response_headers_policy.web.id

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_deep_links.arn
    }
  }

  logging_config {
    include_cookies = false
    bucket          = aws_s3_bucket.web_logs.bucket_domain_name
    prefix          = "cloudfront/"
  }

  # Soft fallback for unknown client routes; prefer the CloudFront Function for UUID shells.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }
}

data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid    = "AllowCloudFrontRead"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [
      aws_s3_bucket.web.arn,
      "${aws_s3_bucket.web.arn}/*",
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid     = "DenyIncorrectEncryptionHeader"
    effect  = "Deny"
    actions = ["s3:PutObject"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid     = "DenyUnencryptedObjectUploads"
    effect  = "Deny"
    actions = ["s3:PutObject"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "Null"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["true"]
    }
  }

  statement {
    sid     = "DenyIncorrectEncryptionKey"
    effect  = "Deny"
    actions = ["s3:PutObject"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [var.kms_key_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json
}

output "bucket_id" {
  value = aws_s3_bucket.web.id
}

output "bucket_arn" {
  value = aws_s3_bucket.web.arn
}

output "distribution_id" {
  value = aws_cloudfront_distribution.web.id
}

output "distribution_arn" {
  value = aws_cloudfront_distribution.web.arn
}

output "distribution_domain_name" {
  value = aws_cloudfront_distribution.web.domain_name
}

output "frontend_url" {
  description = "HTTPS URL for the CloudFront distribution."
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "web_acl_arn" {
  value = aws_wafv2_web_acl.web.arn
}
