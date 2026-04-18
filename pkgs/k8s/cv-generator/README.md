# cv-generator Helm chart

Installs the CV generator onto a Kubernetes cluster with:

- a **Deployment** pulling `gabendockerzone/cv-generator:latest`
- a **ClusterIP Service** on port 80 → container port 8000
- an **Ingress** routing `cv.rewura.com` to the service (TLS via cert-manager
  by default)
- **Liveness + readiness probes** hitting the `/health` endpoint
- a minimal **ServiceAccount**
- an optional **HPA**

## Prerequisites

- Kubernetes cluster with `kubectl` configured
- nginx-ingress-controller (or adjust `ingress.className`)
- cert-manager with a `letsencrypt-prod` ClusterIssuer (or disable TLS / bring
  your own Secret)
- DNS `A`/`CNAME` for `cv.rewura.com` pointing at your ingress LB

## Install

Namespace is already created; the chart does not create it.

```bash
helm upgrade --install cv-generator ./pkgs/k8s/cv-generator \
  --namespace cv-generator
```

(That's the command the user asked for — see `NOTES.txt` after install.)

## Common overrides

Pin a specific image tag instead of `:latest`:

```bash
helm upgrade --install cv-generator ./pkgs/k8s/cv-generator \
  --namespace cv-generator \
  --set image.tag=2026.04.18
```

Disable TLS / ingress (e.g. local cluster):

```bash
helm upgrade --install cv-generator ./pkgs/k8s/cv-generator \
  --namespace cv-generator \
  --set ingress.tls.enabled=false
# or fully disable ingress and port-forward:
helm upgrade --install cv-generator ./pkgs/k8s/cv-generator \
  --namespace cv-generator \
  --set ingress.enabled=false
```

Enable autoscaling:

```bash
helm upgrade --install cv-generator ./pkgs/k8s/cv-generator \
  --namespace cv-generator \
  --set autoscaling.enabled=true \
  --set autoscaling.maxReplicas=5
```

## Roll out a new image

With `image.tag = latest` + `imagePullPolicy = Always`, forcing a restart is
enough:

```bash
kubectl -n cv-generator rollout restart deploy/cv-generator
kubectl -n cv-generator rollout status  deploy/cv-generator
```

## Uninstall

```bash
helm uninstall cv-generator --namespace cv-generator
```
