"""A ChromaDB for integration tests: an existing server or a disposable container.

``DOCULENS_TEST_CHROMA_URL`` selects a running server (the docker compose one, for example);
otherwise a testcontainers ChromaDB is started. Tests use their own uniquely named collections.
"""

import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

CHROMA_IMAGE = "chromadb/chroma:1.5.9"
CHROMA_PORT = 8000
CHROMA_STARTUP_TIMEOUT_SECONDS = 60.0

# See doculens.testing.postgres for why the Ryuk reaper is disabled by default.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


class VectorStoreUnavailableForTestsError(RuntimeError):
    """No ChromaDB could be provided (typically: Docker is not running)."""


@contextmanager
def provisioned_chroma_url() -> Iterator[str]:
    configured = os.environ.get("DOCULENS_TEST_CHROMA_URL")
    if configured:
        yield configured
        return

    from testcontainers.core.container import DockerContainer  # noqa: PLC0415 - optional dependency

    container = (
        DockerContainer(CHROMA_IMAGE)
        .with_env("IS_PERSISTENT", "FALSE")
        .with_env("ANONYMIZED_TELEMETRY", "FALSE")
        .with_exposed_ports(CHROMA_PORT)
    )
    try:
        container.start()
    except Exception as exc:
        message = f"ChromaDB container unavailable (is Docker running?): {exc}"
        raise VectorStoreUnavailableForTestsError(message) from exc
    try:
        url = _wait_until_live(container)
        yield url
    finally:
        container.stop()


def _wait_until_live(container: object) -> str:
    deadline = time.monotonic() + CHROMA_STARTUP_TIMEOUT_SECONDS
    while True:
        try:
            host = container.get_container_host_ip()  # type: ignore[attr-defined]
            port = container.get_exposed_port(CHROMA_PORT)  # type: ignore[attr-defined]
            url = f"http://{host}:{port}"
            with urllib.request.urlopen(f"{url}/api/v2/heartbeat", timeout=2) as response:  # noqa: S310 - local test URL
                if response.status == 200:  # noqa: PLR2004
                    return url
        except (urllib.error.URLError, OSError, ConnectionError):
            pass
        if time.monotonic() > deadline:
            message = f"ChromaDB did not become healthy within {CHROMA_STARTUP_TIMEOUT_SECONDS}s"
            raise VectorStoreUnavailableForTestsError(message)
        time.sleep(0.5)
