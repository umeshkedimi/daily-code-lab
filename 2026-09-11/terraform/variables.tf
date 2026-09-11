variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "genai-gateway"
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "container_image" {
  type        = string
  description = "Full image URI (repo:tag) already pushed to the ECR repo this stack creates."
}

variable "openai_model" {
  type    = string
  default = "gpt-4o-mini"
}

variable "openai_api_key" {
  type        = string
  sensitive   = true
  description = <<-EOT
    Set via TF_VAR_openai_api_key or a -var-file kept out of version control,
    never as a literal in a .tf file. NOTE (trade-off, see notes.md): passing
    it through a Terraform variable means it lands in Terraform state, which
    must itself be encrypted and access-controlled (e.g. an S3 backend with
    SSE and a restrictive bucket policy). A stricter setup would create the
    secret out-of-band (CLI/console/a separate secrets pipeline) and have
    this stack only reference its ARN via a data source.
  EOT
}

variable "cpu" {
  type    = number
  default = 256 # 0.25 vCPU -- smallest Fargate size, fine for a low-traffic demo service
}

variable "memory" {
  type    = number
  default = 512 # MiB
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "log_retention_days" {
  type    = number
  default = 14
}
