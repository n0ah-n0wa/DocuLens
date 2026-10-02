variable "aws_region" {
  description = "Region for the state bucket and lock table."
  type        = string
}

variable "name_prefix" {
  description = "Name prefix for bootstrap resources."
  type        = string
  default     = "doculens"
}

variable "state_bucket_name" {
  description = "Optional explicit state bucket name. Empty derives doculens-terraform-state-<account>."
  type        = string
  default     = ""
}

variable "lock_table_name" {
  description = "Optional explicit DynamoDB lock table name."
  type        = string
  default     = ""
}
