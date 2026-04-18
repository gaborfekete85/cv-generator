# App Runner needs permission to pull the image from our private ECR repo.
# It assumes this role when starting a deployment.

data "aws_iam_policy_document" "apprunner_ecr_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_ecr_access" {
  name               = "${var.service_name}-apprunner-ecr-access"
  description        = "Lets AWS App Runner pull the ${var.service_name} image from private ECR"
  assume_role_policy = data.aws_iam_policy_document.apprunner_ecr_trust.json
}

# Managed policy from AWS granting the minimum ECR read permissions needed
# by App Runner. No custom policy required.
resource "aws_iam_role_policy_attachment" "apprunner_ecr_access" {
  role       = aws_iam_role.apprunner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}
