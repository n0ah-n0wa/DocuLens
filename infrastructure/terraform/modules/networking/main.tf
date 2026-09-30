# Networking — VPC, subnets, NAT, security groups, VPC endpoints (§57, §87).

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
  description = "Prefix for resource names (e.g. doculens-staging)."
  type        = string
}

variable "cidr_block" {
  description = "VPC IPv4 CIDR."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to span (minimum 2)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "nat_gateway_count" {
  description = "NAT gateways to create (1 is cheaper for staging; match az_count for HA)."
  type        = number
  default     = 1

  validation {
    condition     = var.nat_gateway_count >= 1 && var.nat_gateway_count <= 3
    error_message = "nat_gateway_count must be between 1 and 3."
  }
}

variable "enable_interface_endpoints" {
  description = "Create interface VPC endpoints (costly per AZ). Staging can disable and use NAT."
  type        = bool
  default     = true
}

variable "interface_endpoint_services" {
  description = "Interface endpoint service suffixes when enable_interface_endpoints is true."
  type        = set(string)
  default = [
    "secretsmanager",
    "logs",
    "sqs",
    "ecr.api",
    "ecr.dkr",
    "kms",
    "sts",
  ]
}

variable "enable_vpc_flow_logs" {
  description = "Ship VPC flow logs to CloudWatch (security visibility; modest cost)."
  type        = bool
  default     = false
}

variable "flow_logs_retention_days" {
  type    = number
  default = 14
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # /20 public and /20 private per AZ inside the /16.
  public_subnet_cidrs  = [for i, _ in local.azs : cidrsubnet(var.cidr_block, 4, i)]
  private_subnet_cidrs = [for i, _ in local.azs : cidrsubnet(var.cidr_block, 4, i + 8)]

  nat_gateway_count = min(var.nat_gateway_count, var.az_count)
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  for_each = { for idx, az in local.azs : az => { index = idx, cidr = local.public_subnet_cidrs[idx] } }

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = each.value.cidr
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-${each.key}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  for_each = { for idx, az in local.azs : az => { index = idx, cidr = local.private_subnet_cidrs[idx] } }

  vpc_id            = aws_vpc.this.id
  availability_zone = each.key
  cidr_block        = each.value.cidr

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-private-${each.key}"
    Tier = "private"
  })
}

resource "aws_eip" "nat" {
  count  = local.nat_gateway_count
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.name_prefix}-nat-eip-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_nat_gateway" "this" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = values(aws_subnet.public)[count.index].id

  tags = merge(var.tags, { Name = "${var.name_prefix}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name_prefix}-public-rt" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count = var.az_count

  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name_prefix}-private-rt-${count.index}" })
}

resource "aws_route" "private_nat" {
  count = var.az_count

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[min(count.index, local.nat_gateway_count - 1)].id
}

resource "aws_route_table_association" "private" {
  for_each = { for idx, az in local.azs : az => idx }

  subnet_id      = aws_subnet.private[each.key].id
  route_table_id = aws_route_table.private[each.value].id
}

# --- Security groups -----------------------------------------------------------------

resource "aws_security_group" "lambda" {
  name_prefix = "${var.name_prefix}-lambda-"
  description = "Egress for API and worker Lambda functions in the VPC."
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name_prefix}-lambda-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_egress_rule" "lambda_https" {
  security_group_id = aws_security_group.lambda.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS to AWS APIs (endpoints/NAT) and external providers."
}

resource "aws_vpc_security_group_egress_rule" "lambda_dns_udp" {
  security_group_id = aws_security_group.lambda.id
  cidr_ipv4         = var.cidr_block
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  description       = "DNS to VPC resolver."
}

resource "aws_vpc_security_group_egress_rule" "lambda_dns_tcp" {
  security_group_id = aws_security_group.lambda.id
  cidr_ipv4         = var.cidr_block
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
  description       = "DNS (TCP) to VPC resolver."
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_database" {
  security_group_id            = aws_security_group.lambda.id
  referenced_security_group_id = aws_security_group.database.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL to RDS."
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_cache" {
  security_group_id            = aws_security_group.lambda.id
  referenced_security_group_id = aws_security_group.cache.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "Redis to ElastiCache."
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_vector_store" {
  security_group_id            = aws_security_group.lambda.id
  referenced_security_group_id = aws_security_group.vector_store.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  description                  = "ChromaDB HTTP (OQ-1)."
}

resource "aws_security_group" "database" {
  name_prefix = "${var.name_prefix}-db-"
  description = "PostgreSQL access from Lambda only."
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name_prefix}-db-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_lambda" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.lambda.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from Lambda."
}

resource "aws_security_group" "cache" {
  name_prefix = "${var.name_prefix}-redis-"
  description = "Redis access from Lambda only."
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name_prefix}-redis-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_lambda" {
  security_group_id            = aws_security_group.cache.id
  referenced_security_group_id = aws_security_group.lambda.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "Redis from Lambda."
}

resource "aws_security_group" "vpc_endpoints" {
  name_prefix = "${var.name_prefix}-vpce-"
  description = "Interface VPC endpoints."
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name_prefix}-vpce-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "vpce_https_from_lambda" {
  security_group_id            = aws_security_group.vpc_endpoints.id
  referenced_security_group_id = aws_security_group.lambda.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
  description                  = "HTTPS from Lambda to interface endpoints."
}

resource "aws_security_group" "vector_store" {
  name_prefix = "${var.name_prefix}-chroma-"
  description = "Reserved for ChromaDB when OQ-1 is decided (ECS/EC2/Cloud)."
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name_prefix}-chroma-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "vector_store_from_lambda" {
  security_group_id            = aws_security_group.vector_store.id
  referenced_security_group_id = aws_security_group.lambda.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  description                  = "ChromaDB HTTP from Lambda (OQ-1)."
}

# --- VPC endpoints -------------------------------------------------------------------

data "aws_region" "current" {}

locals {
  interface_services = var.enable_interface_endpoints ? var.interface_endpoint_services : toset([])
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat([aws_route_table.public.id], aws_route_table.private[*].id)

  tags = merge(var.tags, { Name = "${var.name_prefix}-s3-endpoint" })
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_services

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [for s in aws_subnet.private : s.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(var.tags, { Name = "${var.name_prefix}-${replace(each.value, ".", "-")}-endpoint" })
}

resource "aws_cloudwatch_log_group" "vpc_flow" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  name              = "/aws/vpc/${var.name_prefix}/flow-logs"
  retention_in_days = var.flow_logs_retention_days
  tags              = merge(var.tags, { Name = "${var.name_prefix}-vpc-flow-logs" })
}

resource "aws_iam_role" "vpc_flow" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  name_prefix = "${var.name_prefix}-flow-"
  tags        = merge(var.tags, { Name = "${var.name_prefix}-vpc-flow-role" })

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "vpc-flow-logs.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "vpc_flow" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  name = "${var.name_prefix}-vpc-flow"
  role = aws_iam_role.vpc_flow[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
      ]
      Resource = ["${aws_cloudwatch_log_group.vpc_flow[0].arn}:*"]
    }]
  })
}

resource "aws_flow_log" "this" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  vpc_id               = aws_vpc.this.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.vpc_flow[0].arn
  iam_role_arn         = aws_iam_role.vpc_flow[0].arn
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-vpc-flow" })
}

output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs."
  value       = [for s in aws_subnet.public : s.id]
}

output "private_subnet_ids" {
  description = "Private subnet IDs."
  value       = [for s in aws_subnet.private : s.id]
}

output "lambda_security_group_id" {
  description = "Security group for Lambda ENIs."
  value       = aws_security_group.lambda.id
}

output "database_security_group_id" {
  description = "Security group for RDS."
  value       = aws_security_group.database.id
}

output "cache_security_group_id" {
  description = "Security group for ElastiCache."
  value       = aws_security_group.cache.id
}

output "vector_store_security_group_id" {
  description = "Security group reserved for ChromaDB (OQ-1)."
  value       = aws_security_group.vector_store.id
}
