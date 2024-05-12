variable "aws_region" {
  description = "AWS region"
  default     = "us-west-2"
}

variable "s3_bucket_name" {
  description = "Name of the S3 bucket"
}

variable "db_username" {
  description = "Database username"
}

variable "db_password" {
  description = "Database password"
}

variable "stripe_api_key" {
  description = "Stripe API key"
}

variable "aws_access_key_id" {
  description = "AWS access key ID"
}

variable "aws_secret_access_key" {
  description = "AWS secret access key"
}