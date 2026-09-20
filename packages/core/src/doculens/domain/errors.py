"""Domain error hierarchy.

Every error carries a stable, machine-readable ``code`` that the interface layer turns into the API
error envelope (SPECIFICATIONS.md §35). Messages must be safe to show to users: no internal
details, stack traces or secrets. Feature-specific errors subclass one of the categories below and
override ``code`` (for example ``DOCUMENT_NOT_FOUND``) so clients can rely on it.
"""


class DomainError(Exception):
    """Base class for errors raised by the domain and application layers."""

    code: str = "DOMAIN_ERROR"
    default_message: str = "The request could not be processed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class InvalidInputError(DomainError):
    """The input violates a domain rule (as opposed to failing transport-level validation)."""

    code = "INVALID_INPUT"
    default_message = "The provided input is invalid."


class NotFoundError(DomainError):
    """The resource does not exist, or exists but is not visible to the caller (§9)."""

    code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class ConflictError(DomainError):
    """The operation conflicts with the current state of the resource."""

    code = "CONFLICT"
    default_message = "The request conflicts with the current state of the resource."


class PermissionDeniedError(DomainError):
    """The caller is authenticated but not allowed to perform the operation."""

    code = "PERMISSION_DENIED"
    default_message = "You do not have permission to perform this action."


class UnauthenticatedError(DomainError):
    """The caller is not authenticated, or presented credentials that cannot be accepted."""

    code = "UNAUTHENTICATED"
    default_message = "Authentication is required."


class DependencyUnavailableError(DomainError):
    """An external dependency (storage, queue, provider) cannot be used right now (§67)."""

    code = "DEPENDENCY_UNAVAILABLE"
    default_message = "A required service is temporarily unavailable."


class RateLimitedError(DomainError):
    """The caller exceeded a request budget; ``retry_after_seconds`` says when to try again."""

    code = "RATE_LIMITED"
    default_message = "Too many requests. Please try again later."

    def __init__(self, retry_after_seconds: int, message: str | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, retry_after_seconds)
