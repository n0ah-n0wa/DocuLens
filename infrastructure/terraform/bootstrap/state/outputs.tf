output "state_bucket_id" {
  description = "S3 bucket for Terraform remote state."
  value       = aws_s3_bucket.state.id
}

output "state_bucket_arn" {
  value = aws_s3_bucket.state.arn
}

output "lock_table_name" {
  description = "DynamoDB table for Terraform state locking."
  value       = aws_dynamodb_table.locks.name
}

output "kms_key_arn" {
  description = "CMK used for state bucket / lock table encryption; pass as TF_STATE_KMS_KEY_ID / backend kms_key_id."
  value       = aws_kms_key.state.arn
}

output "kms_key_id" {
  value = aws_kms_key.state.key_id
}
