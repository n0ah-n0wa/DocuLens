"""Verify production API and worker images meet §56 hardening expectations.

Run after a clean build (``make docker-verify``). Requires Docker. Exits non-zero on failure.
Does not pull secrets into the image and does not start the full application stack.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

API_IMAGE = "doculens-api:verify"
WORKER_IMAGE = "doculens-worker:verify"
APP_UID = 1001
_HEALTH_PROBE = (
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"
)


def main() -> int:
    failures: list[str] = []
    checks_by_image: tuple[tuple[str, str, Callable[[str], None]], ...] = (
        ("api", API_IMAGE, _check_api),
        ("worker", WORKER_IMAGE, _check_worker),
    )
    for name, image, checks in checks_by_image:
        sys.stdout.write(f"==> verifying {name} ({image})\n")
        try:
            checks(image)
            sys.stdout.write(f"OK  {name}\n")
        except AssertionError as exc:
            failures.append(f"{name}: {exc}")
            sys.stderr.write(f"FAIL  {name}: {exc}\n")
    if failures:
        sys.stderr.write("\n".join(failures) + "\n")
        return 1
    sys.stdout.write("all production images verified\n")
    return 0


def _docker() -> str:
    path = shutil.which("docker")
    if path is None:
        message = "docker executable not found on PATH"
        raise AssertionError(message)
    return path


def _check_api(image: str) -> None:
    _check_common(image, expects_healthcheck=True)
    _assert_not_importable(image, "pytest")
    _assert_not_importable(image, "ruff")
    _assert_path_absent(image, "/.env")
    _assert_path_absent(image, "/app/.env")
    _assert_path_absent(image, "/root/.aws")
    _assert_binary_absent(image, "uv")
    _assert_binary_absent(image, "pip")
    _run_api_smoke(image)


def _check_worker(image: str) -> None:
    _check_common(image, expects_healthcheck=True)
    _assert_not_importable(image, "pytest")
    _assert_not_importable(image, "ruff")
    _assert_path_absent(image, "/.env")
    _assert_path_absent(image, "/app/.env")
    _assert_binary_absent(image, "uv")
    _assert_binary_absent(image, "pip")
    # Import and CLI surface without connecting to infrastructure.
    _run(
        [
            _docker(),
            "run",
            "--rm",
            "--user",
            str(APP_UID),
            image,
            "python",
            "-c",
            "import doculens_worker; import doculens_worker.entrypoint",
        ]
    )


def _check_common(image: str, *, expects_healthcheck: bool) -> None:
    config = _inspect_config(image)
    user = str(config.get("User") or "")
    if user not in {str(APP_UID), f"{APP_UID}:{APP_UID}", "app", f"app:{APP_UID}"}:
        message = f"expected non-root User app/{APP_UID}, got {user!r}"
        raise AssertionError(message)
    if expects_healthcheck and not config.get("Healthcheck"):
        message = "HEALTHCHECK missing from image config"
        raise AssertionError(message)
    raw_env = cast("list[str] | None", config.get("Env"))
    env = {
        item.split("=", 1)[0]: item.split("=", 1)[1] if "=" in item else ""
        for item in raw_env or []
    }
    for forbidden in ("AWS_SECRET_ACCESS_KEY", "JWT_SECRET", "DATABASE_URL", "POSTGRES_PASSWORD"):
        if env.get(forbidden):
            message = f"secret-like env {forbidden} is baked into the image"
            raise AssertionError(message)
    _assert_runtime_filesystem(image)


def _assert_runtime_filesystem(image: str) -> None:
    """Production code and venv must be root-owned and not writable by the runtime user."""
    script = (
        "import os, sys\n"
        "uid = os.getuid()\n"
        "failures = []\n"
        "for path in ('/opt/venv', '/app'):\n"
        "    st = os.stat(path)\n"
        "    if st.st_uid == uid:\n"
        "        failures.append(f'{path} owned by runtime uid {uid}')\n"
        "    if os.access(path, os.W_OK):\n"
        "        failures.append(f'{path} is writable by runtime user')\n"
        "home = os.environ.get('HOME', '')\n"
        "if not home or not os.path.isdir(home) or not os.access(home, os.W_OK):\n"
        "    failures.append(f'HOME {home!r} must be a writable directory')\n"
        "if failures:\n"
        "    sys.stderr.write('; '.join(failures) + '\\n')\n"
        "    raise SystemExit(1)\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed docker argv
        [
            _docker(),
            "run",
            "--rm",
            "--user",
            str(APP_UID),
            "--entrypoint",
            "python",
            image,
            "-c",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "filesystem check failed"
        raise AssertionError(detail)


def _run_api_smoke(image: str) -> None:
    # Minimal local settings so the process starts; values are disposable and not secret material.
    name = f"doculens-api-verify-{int(time.time())}"
    run = subprocess.run(  # noqa: S603 - fixed docker argv
        [
            _docker(),
            "run",
            "-d",
            "--name",
            name,
            "--user",
            str(APP_UID),
            "-e",
            "APP_ENV=local",
            "-e",
            "DATABASE_URL=postgresql+asyncpg://doculens:doculens@127.0.0.1:1/doculens",
            "-e",
            "JWT_SECRET=verify-only-not-a-real-secret-0123456789abcdef",
            "-e",
            "STORAGE_BACKEND=filesystem",
            "-e",
            "STORAGE_LOCAL_ROOT=/tmp/doculens-storage",
            "-e",
            "QUEUE_BACKEND=memory",
            "-e",
            "VECTOR_STORE=memory",
            "-e",
            "EMBEDDING_PROVIDER=fake",
            "-e",
            "LLM_PROVIDER=fake",
            "-p",
            "18000:8000",
            image,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        message = f"api container failed to start: {run.stderr.strip()}"
        raise AssertionError(message)
    try:
        deadline = time.time() + 30
        last = ""
        while time.time() < deadline:
            probe = subprocess.run(  # noqa: S603
                [_docker(), "exec", name, "python", "-c", _HEALTH_PROBE],
                check=False,
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                return
            last = probe.stderr.strip() or probe.stdout.strip()
            time.sleep(1)
        logs = subprocess.run(  # noqa: S603
            [_docker(), "logs", name],
            check=False,
            capture_output=True,
            text=True,
        )
        message = f"api /health/live never became ready: {last}\n{logs.stdout}\n{logs.stderr}"
        raise AssertionError(message)
    finally:
        subprocess.run(  # noqa: S603
            [_docker(), "rm", "-f", name], check=False, capture_output=True
        )


def _assert_not_importable(image: str, module: str) -> None:
    result = subprocess.run(  # noqa: S603
        [
            _docker(),
            "run",
            "--rm",
            "--user",
            str(APP_UID),
            image,
            "python",
            "-c",
            f"import {module}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        message = f"development module {module!r} is importable in the production image"
        raise AssertionError(message)


def _assert_path_absent(image: str, path: str) -> None:
    result = subprocess.run(  # noqa: S603
        [
            _docker(),
            "run",
            "--rm",
            "--user",
            str(APP_UID),
            "--entrypoint",
            "python",
            image,
            "-c",
            f"import os, sys; sys.exit(0 if not os.path.exists({path!r}) else 1)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = f"path {path!r} must not exist in the image"
        raise AssertionError(message)


def _assert_binary_absent(image: str, binary: str) -> None:
    result = subprocess.run(  # noqa: S603
        [
            _docker(),
            "run",
            "--rm",
            "--user",
            str(APP_UID),
            "--entrypoint",
            "python",
            image,
            "-c",
            f"import shutil, sys; sys.exit(0 if shutil.which({binary!r}) is None else 1)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = f"binary {binary!r} must not be on PATH in the production image"
        raise AssertionError(message)


def _inspect_config(image: str) -> dict[str, object]:
    raw = _run([_docker(), "image", "inspect", image, "--format", "{{json .Config}}"])
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        message = "unexpected docker inspect payload"
        raise TypeError(message)
    return cast("dict[str, object]", payload)


def _run(argv: Sequence[str]) -> str:
    result = subprocess.run(  # noqa: S603 - argv is constructed in-process
        list(argv), check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"command failed: {argv}"
        raise AssertionError(message)
    return result.stdout


if __name__ == "__main__":
    raise SystemExit(main())
