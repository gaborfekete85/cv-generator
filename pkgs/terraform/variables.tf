variable "aws_region" {
  description = "AWS region to deploy into. App Runner is available in most major regions; eu-central-1 (Frankfurt) is closest to Zurich."
  type        = string
  default     = "eu-central-1"
}

variable "service_name" {
  description = "Prefix for all resource names (ECR repo, IAM role, App Runner service)."
  type        = string
  default     = "cv-generator"
}

# --- Container settings -----------------------------------------------------

variable "image_tag" {
  description = "Tag of the image in ECR to deploy. `:latest` + auto_deploy = CI-style continuous deployment on every push."
  type        = string
  default     = "latest"
}

variable "container_port" {
  description = "Port the FastAPI app listens on inside the container (matches the Dockerfile CMD)."
  type        = number
  default     = 8000
}

variable "environment_variables" {
  description = "Runtime environment variables passed to the container."
  type        = map(string)
  default = {
    CV_PDF_BACKEND = "auto"
  }
}

# --- Instance sizing --------------------------------------------------------
#
# App Runner billable CPU/memory combos:
#   https://docs.aws.amazon.com/apprunner/latest/dg/manage-configure.html
# The 0.25 vCPU / 0.5 GB tier is ~$0.007/hour (~$5/month 24×7) and fine for
# this CV generator. Bump memory to 1 GB if you see WeasyPrint OOMs.

variable "cpu" {
  description = "App Runner CPU size. Valid values include '0.25 vCPU', '0.5 vCPU', '1 vCPU', '2 vCPU', '4 vCPU'."
  type        = string
  default     = "0.25 vCPU"
}

variable "memory" {
  description = "App Runner memory size. Valid values include '0.5 GB', '1 GB', '2 GB', '3 GB', '4 GB', '6 GB', '8 GB', '10 GB', '12 GB'."
  type        = string
  default     = "0.5 GB"
}

variable "auto_deploy" {
  description = "Redeploy automatically when a new image is pushed to the ECR tag."
  type        = bool
  default     = true
}

variable "create_service" {
  description = "Gate the creation of the App Runner service. First-time deploys: leave false, apply, push the image, then set to true and apply again. After that, leave it true."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    Project = "cv-generator"
  }
}
