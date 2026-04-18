resource "aws_ecr_repository" "this" {
  name                 = var.service_name
  image_tag_mutability = "MUTABLE" # lets us overwrite `:latest`
  force_delete         = true      # `terraform destroy` succeeds even if images exist

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep the registry tidy: expire untagged layers quickly, and cap tagged
# history so we don't pay to store a year of stale revisions.
resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  # Two priorities. Lower-priority rules match first, so:
  #   1. Untagged layers get pruned after 14 days.
  #   2. Whatever remains, cap the total count at 20 so the repo doesn't grow
  #      unbounded as we push new :latest images over the lifetime of the
  #      project.
  #
  # We use `tagStatus = "any"` for rule 2 — ECR rejects `tagPrefixList = [""]`
  # (empty strings not allowed) and we don't want to hardcode a tag prefix
  # that might not match the user's naming scheme.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the last 20 images (any tag status)"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 20
        }
        action = { type = "expire" }
      }
    ]
  })
}
