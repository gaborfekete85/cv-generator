# Terraform — AWS App Runner deployment

Ships the CV generator to **AWS App Runner**, a fully managed container
service that gives you auto-scaling, TLS-terminated HTTPS, and per-request
billing out of the box. No VPC, load balancer, or ECS cluster to wire up.

## What gets created

| Resource | Purpose |
|---|---|
| `aws_ecr_repository.this` | Private container registry for the Docker image. |
| `aws_ecr_lifecycle_policy.this` | Prunes untagged layers after 14 days; keeps last 20 tagged revisions. |
| `aws_iam_role.apprunner_ecr_access` | Lets App Runner pull images from the repo above. |
| `aws_apprunner_service.this` | The actual running service. Health-checked against `/health`. |

Defaults: `eu-central-1`, 0.25 vCPU / 0.5 GB (~$5/month 24×7), auto-deploy on
every new `:latest` push, `CV_PDF_BACKEND=auto` so the container uses
WeasyPrint.

## Prerequisites

- **Terraform** ≥ 1.5 — `brew install terraform`
- **AWS CLI** v2 with credentials configured — `aws configure`
- **Docker** running locally

Your AWS identity needs permissions for ECR, IAM roles, App Runner, and
CloudWatch Logs. `AdministratorAccess` works; if you're scoped down, at
minimum: `ecr:*`, `iam:CreateRole` / `AttachRolePolicy` / `PassRole`,
`apprunner:*`, and `logs:*`.

## Deploy — the easy way

```bash
cd pkgs/terraform
./deploy.sh
```

`deploy.sh` does the whole thing: `terraform init` → creates ECR + IAM →
`docker build` + push → creates the App Runner service → prints the URL.

Flags:

```bash
./deploy.sh --infra-only   # just terraform apply (skip docker build/push)
./deploy.sh --image-only   # just docker build/push (skip terraform)
```

## Deploy — the manual way (two-phase)

App Runner refuses to create a service if the image isn't already in ECR, so
you have to create the registry first, push an image into it, *then* create
the service.

```bash
cd pkgs/terraform
terraform init

# Phase 1 — ECR + IAM only (no App Runner service yet).
terraform apply -var="create_service=false"

# Grab the ECR URL for the push commands.
terraform output -raw ecr_repository_url
```

Build and push from the project root:

```bash
cd ../..                                    # back to cv-generator/
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin $(terraform -chdir=pkgs/terraform output -raw ecr_repository_url)

docker build -t cv-generator:latest .
docker tag cv-generator:latest $(terraform -chdir=pkgs/terraform output -raw ecr_repository_url):latest
docker push $(terraform -chdir=pkgs/terraform output -raw ecr_repository_url):latest
```

Then back to Terraform for phase 2:

```bash
cd pkgs/terraform
terraform apply                             # create_service defaults to true
terraform output service_url                # -> https://xxxxx.eu-central-1.awsapprunner.com
```

The first `apply` of phase 2 takes 3–5 minutes while App Runner pulls the
image and boots a container.

## Updating the app

With `auto_deploy = true` (the default), you don't touch Terraform after the
initial setup — just rebuild and push:

```bash
docker build -t cv-generator:latest .
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin $ECR_URL
docker tag cv-generator:latest $ECR_URL:latest
docker push $ECR_URL:latest
```

App Runner notices the new `:latest` and rolls out a new revision. Check
status with:

```bash
aws apprunner describe-service --service-arn $(terraform output -raw service_arn) \
  --query 'Service.Status'
```

## Customising

Copy `terraform.tfvars.example` to `terraform.tfvars` and uncomment the bits
you want to change:

- `aws_region` — default `eu-central-1`.
- `cpu` / `memory` — bump if you see WeasyPrint OOM or slow renders.
- `image_tag` — switch from `latest` to a semver tag for controlled releases
  (disable `auto_deploy` in that case).
- `environment_variables` — add anything the FastAPI process should read.

Then `terraform apply` picks up the changes.

## Teardown

```bash
terraform destroy
```

`force_delete = true` on the ECR repo means destroy succeeds even with
images still inside. If you only want to pause App Runner without
destroying everything (so your URL survives), use the AWS console or:

```bash
aws apprunner pause-service --service-arn $(terraform output -raw service_arn)
aws apprunner resume-service --service-arn $(terraform output -raw service_arn)
```

## Cost notes

App Runner charges per **running-instance-second**, not per request. The
defaults (0.25 vCPU, 0.5 GB) work out to ~**$0.007 / hour** = ~**$5 / month**
if the service stays warm 24×7. It auto-scales out under load but keeps at
least 1 instance provisioned. To drop to true idle billing, configure
`aws_apprunner_auto_scaling_configuration_version` with `min_size = 0` —
that's out of scope for this minimal setup.

ECR storage is ~$0.10 / GB / month; a cv-generator image is around 400 MB,
so ECR storage is effectively free.

## State

Local `terraform.tfstate` by default. For multi-machine / team use, uncomment
the S3 backend stanza in `versions.tf` after creating a state bucket +
DynamoDB lock table.
