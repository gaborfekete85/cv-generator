terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }

  # For personal / demo use, local state is fine. Switch to an S3 + DynamoDB
  # backend for team collaboration:
  #
  # backend "s3" {
  #   bucket         = "my-tfstate"
  #   key            = "cv-generator/terraform.tfstate"
  #   region         = "eu-central-1"
  #   dynamodb_table = "tf-locks"
  #   encrypt        = true
  # }
}
