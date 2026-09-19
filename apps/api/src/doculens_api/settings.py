"""Settings owned by the API process, on top of the core settings."""

from pydantic import Field

from doculens.infrastructure.config import CoreSettings


class ApiSettings(CoreSettings):
    api_docs_enabled: bool | None = Field(
        default=None,
        description=(
            "Serve the OpenAPI document and interactive docs (§75). Unset means enabled locally "
            "and disabled in deployed environments; set explicitly to override."
        ),
    )
    request_id_header: str = Field(
        default="X-Request-ID",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9-]*$",
        description="Header used to receive and return the request correlation ID (§36).",
    )
    health_probe_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30,
        description="Upper bound for each dependency probe run by /health/ready.",
    )

    @property
    def docs_enabled(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return not self.is_deployed
