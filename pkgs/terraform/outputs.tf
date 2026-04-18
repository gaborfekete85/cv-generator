output "ecr_repository_url" {
  description = "Push your Docker image to this URL."
  value       = aws_ecr_repository.this.repository_url
}

output "aws_region" {
  description = "The AWS region the stack is deployed in (useful for scripting `aws ecr get-login-password`)."
  value       = var.aws_region
}

output "image_tag" {
  description = "ECR image tag the service is configured to deploy."
  value       = var.image_tag
}

output "service_url" {
  description = "Public HTTPS URL of the deployed App Runner service."
  value       = try("https://${aws_apprunner_service.this[0].service_url}", null)
}

output "service_status" {
  description = "App Runner service status (e.g. RUNNING, CREATE_FAILED, PAUSED)."
  value       = try(aws_apprunner_service.this[0].status, null)
}

output "service_arn" {
  description = "ARN of the App Runner service — useful for CLI operations (logs, start/pause/resume)."
  value       = try(aws_apprunner_service.this[0].arn, null)
}

output "docker_push_commands" {
  description = "Copy-paste these three commands to build and push the image."
  value       = <<-EOT

    # From the project root (where the Dockerfile lives):
    aws ecr get-login-password --region ${var.aws_region} \
      | docker login --username AWS --password-stdin ${aws_ecr_repository.this.repository_url}

    docker build -t ${var.service_name}:${var.image_tag} ../..

    docker tag ${var.service_name}:${var.image_tag} ${aws_ecr_repository.this.repository_url}:${var.image_tag}
    docker push ${aws_ecr_repository.this.repository_url}:${var.image_tag}

  EOT
}
