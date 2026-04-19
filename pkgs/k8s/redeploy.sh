#!/usr/bin/env bash
# Rebuild the Docker image for Kubernetes and roll the Deployment via Helm.
#
# This script exists because building from an Apple Silicon Mac produces an
# ARM64 image by default, which crash-loops with "exec format error" on an
# x86_64 Kubernetes cluster. The fix is `docker buildx build
# --platform linux/amd64 --push`, which this script always enforces.
#
# The rollout itself is driven by `helm upgrade --install`. Since the image
# tag is typically `:latest` and Helm sees no diff in the chart values, we
# inject a `podAnnotations.rolloutTimestamp` that changes every run — that's
# the Helm-idiomatic way of telling Kubernetes to recreate the pods so the
# new image gets pulled (the chart already sets `imagePullPolicy: Always`).
#
# Usage:
#   ./rebuild.sh                 # build + push + helm upgrade
#   ./rebuild.sh --no-rollout    # build + push only (skip helm)
#   ./rebuild.sh --no-push       # build locally (no push, no helm)
#   ./rebuild.sh --helm-only     # skip build/push, just helm upgrade
#
# Env overrides:
#   IMAGE        default: gabendockerzone/cv-generator:latest
#   PLATFORM     default: linux/amd64   (linux/arm64 for Graviton, or
#                                        linux/amd64,linux/arm64 for multi-arch)
#   NAMESPACE    default: cv-generator
#   RELEASE      default: cv-generator  (Helm release name)
#   CHART_PATH   default: <this script's dir>/cv-generator
#   VALUES_FILE  optional: path to an extra -f values file (e.g. values-prod.yaml)
#   HELM_TIMEOUT default: 5m

set -euo pipefail

IMAGE="${IMAGE:-gabendockerzone/cv-generator:latest}"
PLATFORM="${PLATFORM:-linux/amd64}"
NAMESPACE="${NAMESPACE:-cv-generator}"
RELEASE="${RELEASE:-cv-generator}"
HELM_TIMEOUT="${HELM_TIMEOUT:-5m}"

MODE="${1:-full}"

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m==>\033[0m %s\n" "$*" >&2; }
fail() { printf "\033[1;31mERROR:\033[0m %s\n" "$*" >&2; exit 1; }

# --- Resolve paths ---------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHART_PATH="${CHART_PATH:-$SCRIPT_DIR/cv-generator}"

# --- Prereq checks ---------------------------------------------------------
command -v docker >/dev/null || fail "docker not found on PATH"
docker buildx version >/dev/null 2>&1 \
  || fail "docker buildx is required (bundled with recent Docker Desktop)"
if [[ "$MODE" != "--no-rollout" && "$MODE" != "--no-push" ]]; then
  command -v helm >/dev/null \
    || fail "helm not found on PATH (use --no-rollout to skip the rollout)"
  [[ -d "$CHART_PATH" ]] \
    || fail "chart directory not found at $CHART_PATH"
fi

if [[ "$MODE" != "--helm-only" ]]; then
  [[ -f "$PROJECT_ROOT/Dockerfile" ]] \
    || fail "Dockerfile not found in $PROJECT_ROOT — this script expects to live at pkgs/k8s/"
fi

# --- Docker Hub login reminder (best-effort) ------------------------------
if [[ "$MODE" != "--no-push" && "$MODE" != "--helm-only" ]]; then
  if ! docker info 2>/dev/null | grep -qi "Username:"; then
    warn "You don't appear to be logged into a Docker registry."
    warn "If the push fails with 'unauthorized', run: docker login -u <user>"
  fi
fi

# --- Build (and push, unless --no-push) -----------------------------------
if [[ "$MODE" == "--no-push" ]]; then
  log "Building $IMAGE for $PLATFORM (no push)"
  ( cd "$PROJECT_ROOT" && docker buildx build --platform "$PLATFORM" --load -t "$IMAGE" . )
  log "Local image built. Skipping push and Helm rollout."
  exit 0
fi

if [[ "$MODE" != "--helm-only" ]]; then
  log "Building + pushing $IMAGE for $PLATFORM"
  ( cd "$PROJECT_ROOT" && docker buildx build --platform "$PLATFORM" --push -t "$IMAGE" . )
fi

# --- Helm rollout ----------------------------------------------------------
if [[ "$MODE" == "--no-rollout" ]]; then
  log "Push complete. Skipping Helm rollout (--no-rollout)."
  exit 0
fi

# A changing annotation on the PodSpec is the Helm-idiomatic way to force
# the Deployment to recreate its pods even when nothing else has diffed.
ROLLOUT_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

HELM_ARGS=(
  upgrade --install "$RELEASE" "$CHART_PATH"
  --namespace "$NAMESPACE"
  --set-string "podAnnotations.rolloutTimestamp=$ROLLOUT_TS"
  --set-string "image.repository=${IMAGE%%:*}"
  --set-string "image.tag=${IMAGE##*:}"
  --wait
  --timeout "$HELM_TIMEOUT"
)

# Extra values file (optional, e.g. values-prod.yaml)
if [[ -n "${VALUES_FILE:-}" ]]; then
  [[ -f "$VALUES_FILE" ]] || fail "VALUES_FILE not found: $VALUES_FILE"
  HELM_ARGS+=( -f "$VALUES_FILE" )
fi

log "helm upgrade --install $RELEASE (namespace: $NAMESPACE, timeout: $HELM_TIMEOUT)"
helm "${HELM_ARGS[@]}"

log "Done. Release status:"
helm status "$RELEASE" --namespace "$NAMESPACE" | head -20

# NOTE: cv_template.md no longer needs a manual `kubectl cp` — it lives
# at backend/cv_template.md and is baked into the image, so any template
# edit reaches the cluster through this redeploy script (which rebuilds
# the image and rolls the deployment).