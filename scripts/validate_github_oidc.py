"""Validate GitHub Actions ↔ AWS OIDC wiring without calling AWS (SPECIFICATIONS.md §5.5).

Checks that:
- IAM module defaults pin environment-scoped subjects (no wildcards / branch refs);
- env examples separate provider creation from staging deploy roles;
- CD workflows request id-token and never mention static AWS access keys.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
IAM_MODULE = ROOT / "infrastructure" / "terraform" / "modules" / "iam" / "main.tf"
STAGING_EXAMPLE = (
    ROOT / "infrastructure" / "terraform" / "envs" / "staging" / "terraform.tfvars.example"
)
PRODUCTION_EXAMPLE = (
    ROOT / "infrastructure" / "terraform" / "envs" / "production" / "terraform.tfvars.example"
)
CD_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "cd-staging.yml",
    ROOT / ".github" / "workflows" / "cd-production.yml",
)
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

FORBIDDEN_SUBJECT_FRAGMENTS = (
    "environment:*",
    "ref:refs/heads/",
    "repo:*",
)
FORBIDDEN_SECRET_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)


def _fail(message: str) -> NoReturn:
    raise AssertionError(message)


def _strip_hcl_line_comments(text: str) -> str:
    return "\n".join(line.split("//", 1)[0].split("#", 1)[0] for line in text.splitlines())


def check_iam_module() -> None:
    text = IAM_MODULE.read_text(encoding="utf-8")
    code = _strip_hcl_line_comments(text)
    if "create_github_oidc_provider" not in code or "create_github_deploy_role" not in code:
        _fail("IAM module must split OIDC provider creation from the deploy role.")
    if "sts:AssumeRoleWithWebIdentity" not in code:
        _fail("Deploy role must use sts:AssumeRoleWithWebIdentity.")
    if "token.actions.githubusercontent.com:aud" not in code:
        _fail("Trust policy must require aud=sts.amazonaws.com.")
    if "environment:${var.github_deploy_environment}" not in code:
        _fail("Default OIDC subjects must be environment-scoped.")
    subjects_block = re.search(
        r"github_subjects\s*=.*?\[(.*?)\]",
        code,
        flags=re.DOTALL,
    )
    if subjects_block is None:
        _fail("IAM module must define local.github_subjects.")
    subjects_src = subjects_block.group(1)
    for fragment in FORBIDDEN_SUBJECT_FRAGMENTS:
        if fragment in subjects_src:
            _fail(f"IAM module must not default trust subjects containing {fragment!r}.")
        if f'"{fragment}"' in code or f"'{fragment}'" in code:
            _fail(f"IAM module must not embed forbidden subject {fragment!r}.")


def check_tfvars_examples() -> None:
    staging = STAGING_EXAMPLE.read_text(encoding="utf-8")
    production = PRODUCTION_EXAMPLE.read_text(encoding="utf-8")
    if "create_github_oidc_provider = false" not in staging:
        _fail("Staging example must leave OIDC provider creation off (account-level once).")
    if "create_github_deploy_role" not in staging:
        _fail("Staging example must create (or document) the staging deploy role.")
    if "create_github_oidc_provider = true" not in production:
        _fail("Production example should create the account OIDC provider.")
    if "environment:staging" not in staging:
        _fail("Staging example should document the staging environment subject.")
    if "environment:production" not in production:
        _fail("Production example should document the production environment subject.")


def check_cd_workflows() -> None:
    for path in CD_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        if "id-token: write" not in text:
            _fail(f"{path.name} must set permissions.id-token: write for OIDC.")
        if "aws-actions/configure-aws-credentials@" not in text:
            _fail(f"{path.name} must use configure-aws-credentials.")
        if "role-to-assume:" not in text:
            _fail(f"{path.name} must assume a role via OIDC (role-to-assume).")
        for secret_name in FORBIDDEN_SECRET_NAMES:
            if secret_name in text:
                _fail(f"{path.name} must not reference {secret_name}.")
        if "environment: staging" not in text and "environment: production" not in text:
            _fail(f"{path.name} must bind to a GitHub Environment.")

    staging = (ROOT / ".github" / "workflows" / "cd-staging.yml").read_text(encoding="utf-8")
    for required in (
        "CI gate",
        "Security scan",
        "Infrastructure plan",
        "Deploy · staging",
        "Smoke tests",
        "scripts/cd/deploy_staging.sh",
        "scripts/cd/smoke_staging.sh",
        "TF_VAR_EXTRA_SECRET_VALUES",
    ):
        if required not in staging:
            _fail(f"cd-staging.yml must include pipeline stage/script {required!r}.")
    if "AWS_ACCESS_KEY_ID" in staging:
        _fail("cd-staging.yml must not use static AWS keys.")
    _check_staging_smoke_suite()


def _check_staging_smoke_suite() -> None:
    smoke_py = ROOT / "scripts" / "cd" / "smoke_staging.py"
    smoke_sh = ROOT / "scripts" / "cd" / "smoke_staging.sh"
    if not smoke_py.is_file():
        _fail("scripts/cd/smoke_staging.py must exist for functional staging smoke.")
    if "smoke_staging.py" not in smoke_sh.read_text(encoding="utf-8"):
        _fail("smoke_staging.sh must invoke smoke_staging.py.")
    smoke_src = smoke_py.read_text(encoding="utf-8")
    for marker in (
        "auth/register",
        "documents",
        "conversations",
        "citation",
        "cleanup",
    ):
        if marker not in smoke_src:
            _fail(f"smoke_staging.py must cover {marker!r}.")


def check_ci_has_no_deploy_keys() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    for secret_name in FORBIDDEN_SECRET_NAMES:
        if secret_name in text:
            _fail(f"ci.yml must not reference {secret_name}.")
    if "role-to-assume:" in text:
        _fail("ci.yml must not assume the deploy role; CD owns deployment.")


def main() -> int:
    checks = (
        check_iam_module,
        check_tfvars_examples,
        check_cd_workflows,
        check_ci_has_no_deploy_keys,
    )
    failures: list[str] = []
    for check in checks:
        try:
            check()
        except AssertionError as exc:
            failures.append(str(exc))
    if failures:
        for item in failures:
            sys.stderr.write(f"FAIL: {item}\n")
        return 1
    sys.stdout.write("GitHub Actions OIDC trust and workflow checks passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
