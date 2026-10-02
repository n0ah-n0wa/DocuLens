# API Gateway HTTP API in front of the API Lambda (§5.4, §57). Streaming (SSE) remains OQ-3b.

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

variable "lambda_invoke_arn" {
  type = string
}

variable "lambda_function_name" {
  type = string
}

variable "stage_name" {
  type    = string
  default = "$default"
}

variable "throttle_burst_limit" {
  type    = number
  default = 200
}

variable "throttle_rate_limit" {
  type    = number
  default = 100
}

variable "access_log_group_arn" {
  type = string
}

variable "cors_allow_origins" {
  type    = list(string)
  default = []
}

variable "enable_waf" {
  description = "Attach a regional WAFv2 Web ACL to the HTTP API stage."
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "WAFv2 rate-based rule limit (requests per 5-minute window per IP)."
  type        = number
  default     = 2000
}

variable "alarm_actions" {
  description = "SNS topic ARNs for API Gateway 5XX alarm."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name_prefix}-http"
  protocol_type = "HTTP"
  description   = "DocuLens HTTP API"

  dynamic "cors_configuration" {
    for_each = length(var.cors_allow_origins) > 0 ? [1] : []
    content {
      allow_headers = ["authorization", "content-type", "x-request-id"]
      allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
      allow_origins = var.cors_allow_origins
      max_age       = 3600
    }
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-http-api" })
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.lambda_invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = var.stage_name
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit   = var.throttle_burst_limit
    throttling_rate_limit    = var.throttle_rate_limit
    detailed_metrics_enabled = true
  }

  access_log_settings {
    destination_arn = var.access_log_group_arn
    format = jsonencode({
      requestId          = "$context.requestId"
      ip                 = "$context.identity.sourceIp"
      requestTime        = "$context.requestTime"
      httpMethod         = "$context.httpMethod"
      routeKey           = "$context.routeKey"
      path               = "$context.path"
      status             = "$context.status"
      protocol           = "$context.protocol"
      responseLength     = "$context.responseLength"
      responseLatency    = "$context.responseLatency"
      integrationLatency = "$context.integrationLatency"
      userAgent          = "$context.identity.userAgent"
      integrationErr     = "$context.integrationErrorMessage"
    })
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-http-stage" })
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

resource "aws_wafv2_web_acl" "api" {
  count = var.enable_waf ? 1 : 0

  name  = "${var.name_prefix}-api"
  scope = "REGIONAL"

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
      metric_name                = "${var.name_prefix}-api-ip-rep"
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
      metric_name                = "${var.name_prefix}-api-common"
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
      metric_name                = "${var.name_prefix}-api-bad-inputs"
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
      metric_name                = "${var.name_prefix}-api-sqli"
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
      metric_name                = "${var.name_prefix}-api-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-api"
    sampled_requests_enabled   = true
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-api-waf" })
}

resource "aws_wafv2_web_acl_association" "api" {
  count = var.enable_waf ? 1 : 0

  resource_arn = aws_apigatewayv2_stage.this.arn
  web_acl_arn  = aws_wafv2_web_acl.api[0].arn
}

resource "aws_cloudwatch_metric_alarm" "api_gateway_5xx" {
  alarm_name          = "${var.name_prefix}-apigw-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "5xx"
  namespace           = "AWS/ApiGateway"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "API Gateway HTTP API 5XX responses"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions

  dimensions = {
    ApiId = aws_apigatewayv2_api.this.id
    Stage = aws_apigatewayv2_stage.this.name
  }

  tags = var.tags
}

output "api_id" {
  value = aws_apigatewayv2_api.this.id
}

output "api_endpoint" {
  description = "Invoke URL for the default stage."
  value       = aws_apigatewayv2_stage.this.invoke_url
}

output "execution_arn" {
  value = aws_apigatewayv2_api.this.execution_arn
}

output "stage_arn" {
  value = aws_apigatewayv2_stage.this.arn
}

output "waf_acl_arn" {
  description = "Regional WAFv2 Web ACL ARN (null when enable_waf is false)."
  value       = var.enable_waf ? aws_wafv2_web_acl.api[0].arn : null
}
