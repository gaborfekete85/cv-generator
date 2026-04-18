# The App Runner service itself.
#
# Chicken-and-egg note: App Runner refuses to create a service if the image
# doesn't exist in ECR. On a cold deploy:
#   1. Run `terraform apply -var=create_service=false` to create the ECR
#      repository + IAM role only.
#   2. Build and push the image (see `outputs.tf` for copy-pasteable commands
#      or run `./deploy.sh`).
#   3. Run `terraform apply` (create_service defaults to true) — the service
#      comes up pulling the image you just pushed.

resource "aws_apprunner_service" "this" {
  count = var.create_service ? 1 : 0

  service_name = var.service_name

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }

    auto_deployments_enabled = var.auto_deploy

    image_repository {
      image_identifier      = "${aws_ecr_repository.this.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port                          = tostring(var.container_port)
        runtime_environment_variables = var.environment_variables
      }
    }
  }

  instance_configuration {
    cpu    = var.cpu
    memory = var.memory
  }

  # Health check uses the /health endpoint exposed by the FastAPI app.
  health_check_configuration {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  # Waiting for the image to exist is still best-effort — rely on the
  # two-phase flow documented in README.md.
  depends_on = [
    aws_iam_role_policy_attachment.apprunner_ecr_access,
  ]
}
