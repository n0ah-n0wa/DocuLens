# ADR-022 — GitHub Actions OIDC authentication to AWS

**Status:** Accepted
**Date:** 2026-10-01
**Specification references:** §5.5, §56, §57, §58, §59

## Context

SPECIFICATIONS.md §5.5 requires deployment workflows and forbids long-lived AWS access keys in
GitHub. Staging and production share an AWS account in the current Terraform layout, so a single
OIDC identity provider must be created once, while each environment still needs an isolated deploy
principal.

## Decision

- Authenticate CD exclusively with GitHub Actions OIDC (`permissions.id-token: write`) and
  `sts:AssumeRoleWithWebIdentity`. Repository variables hold role ARNs only — never
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.
- Create one account-level IAM OIDC provider for `token.actions.githubusercontent.com`
  (`create_github_oidc_provider`, typically in the production root).
- Create a **dedicated deploy role per Terraform environment** (`create_github_deploy_role`) with a
  trust policy that allows only:
  - audience `sts.amazonaws.com`;
  - subject `repo:<org>/<repo>:environment:<staging|production>` (exact match; no `environment:*`,
    no `refs/heads/*`).
- Scope deploy permissions to that environment’s ECR repositories, CMK, and Lambda functions
  (api / worker / migrate).
- Bind CD workflows to GitHub Environments of the same name (`staging`, `production`) so the JWT
  `sub` claim matches the trust policy. Production uses `workflow_dispatch` plus Environment
  protection rules for human approval.

## Alternatives considered

| Alternative                         | Why not                                                                             |
| ----------------------------------- | ----------------------------------------------------------------------------------- |
| Static IAM user keys in GitHub      | Forbidden by §5.5; long-lived secrets rotate poorly and leak broadly                |
| One shared deploy role for all envs | Breaks staging/production separation; a staging job could update production Lambdas |
| Trust `refs/heads/main`             | Branch pushes are not environment-gated; bypasses required reviewers                |
| Trust `environment:*`               | Any future GitHub Environment could assume the role                                 |
| OIDC provider in every env root     | Duplicate provider ARNs collide in one AWS account                                  |

## Consequences

- Bootstrap order: apply production (or create the IdP once) before staging deploy roles that look
  up the provider by URL.
- Operators must configure GitHub Environments and `AWS_DEPLOY_ROLE_ARN` as documented in
  [`docs/deployment.md`](../deployment.md).
- Image push / Lambda update steps can be added to `cd-staging.yml` / `cd-production.yml` without
  changing the trust model.
- CI remains keyless and does not assume the deploy role.
