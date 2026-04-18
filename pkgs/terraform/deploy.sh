#!/usr/bin/env bash
# End-to-end deploy helper: provisions ECR + IAM, builds and pushes the image,
# then creates (or updates) the App Runner service.
#
# Usage:
#   ./deploy.sh                    # full deploy
#   ./deploy.sh --infra-only       # just terraform apply (no docker build/push)
#   ./deploy.sh --image-only       # just docker build + push (skip terraform)
#
# Requires: terraform, docker, awscli, an AWS session with sufficient perms.
# Compatible with both macOS (BSD userland) and Linux.

set -euo pipefail

MODE="${1:-full}"
cd "$(dirname "$0")"

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31mERROR:\033[0m %s\n" "$*" >&2; exit 1; }

command -v terraform >/dev/null || fail "terraform not found on PATH"
command -v docker    >/dev/null || fail "docker not found on PATH"
command -v aws       >/dev/null || fail "aws CLI not found on PATH"

# --- Phase 1: terraform ECR + IAM ------------------------------------------
if [[ "$MODE" != "--image-only" ]]; then
  log "terraform init"
  terraform init -input=false

  log "terraform apply — phase 1: ECR + IAM (no App Runner service yet)"
  terraform apply -input=false -auto-approve -var="create_service=false"
fi

# --- Read values from terraform state --------------------------------------
# These come from outputs.tf; they exist after phase 1 (or from a previous
# run if we're in --image-only mode).
ECR_URL=$(terraform output -raw ecr_repository_url)
REGION=$(terraform output -raw aws_region)
IMAGE_TAG=$(terraform output -raw image_tag)

log "target: ${ECR_URL}:${IMAGE_TAG}  (region: ${REGION})"

# --- Phase 2: docker build + push ------------------------------------------
if [[ "$MODE" != "--infra-only" ]]; then
  log "docker login to ECR"
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$ECR_URL"

  # Build context is the project root (two levels up from pkgs/terraform).
  #
  # --platform linux/amd64 is critical: App Runner runs on x86_64 instances,
  # so an ARM64 image built natively on Apple Silicon will crash-loop with
  # "exec format error" and the App Runner health check will fail with the
  # misleading "Check your configured port number" message.
  # buildx handles the cross-build automatically — it's bundled with recent
  # Docker Desktop.
  log "docker build (linux/amd64)"
  docker buildx build --platform linux/amd64 --load \
    -t "${ECR_URL}:${IMAGE_TAG}" ../..

  log "docker push"
  docker push "${ECR_URL}:${IMAGE_TAG}"
fi

# --- Phase 3: terraform creates / updates the App Runner service ----------
if [[ "$MODE" != "--image-only" ]]; then
  log "terraform apply — phase 2: App Runner service"
  terraform apply -input=false -auto-approve

  SERVICE_URL=$(terraform output -raw service_url || true)
  if [[ -n "${SERVICE_URL:-}" ]]; then
    log "Done. Service URL:"
    printf "\n    %s\n\n" "$SERVICE_URL"
  else
    log "Done (service URL not available yet — terraform output service_url when ready)."
  fi
fi
