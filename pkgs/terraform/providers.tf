provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(var.tags, {
      ManagedBy = "terraform"
      Stack     = var.service_name
    })
  }
}
