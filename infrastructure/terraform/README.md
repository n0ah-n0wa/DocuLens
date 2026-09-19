# Terraform

Infrastructure as Code for the `staging` and `production` environments (SPECIFICATIONS.md §57).
`local` is not a Terraform environment: it is the docker compose stack in `docker/compose.yaml`.

## Layout

```text
infrastructure/terraform/
├── envs/
│   ├── staging/       # root module for staging
│   └── production/    # root module for production
└── modules/           # reusable modules, added in the infrastructure phase (see modules/README.md)
```

Each environment root currently declares the Terraform and AWS provider versions, the provider
configuration with default tags, and a partial S3 backend. No resources exist yet.

## Working with an environment

```bash
cd infrastructure/terraform/envs/staging
terraform init -backend-config=backend.hcl   # backend.hcl is git-ignored; see below
terraform fmt -check -recursive
terraform validate
terraform plan -var-file=terraform.tfvars
```

`backend.hcl` supplies the state bucket, region and locking settings and is created per
environment by the person bootstrapping it. Bootstrapping (state bucket, GitHub OIDC provider and
the deployment role) is a one-time, documented manual step covered in the infrastructure phase.

Version pins: `versions.tf` in each environment, plus the committed `.terraform.lock.hcl` that
records provider checksums. After changing a provider version, refresh it with
`terraform providers lock` and commit the result.

CI runs `terraform fmt -check -recursive` and, per environment,
`terraform init -backend=false && terraform validate`.
