"""Language-model concepts (SPECIFICATIONS.md §21, §38, §67, §73).

What a generation is made of, what it costs and how it can fail, with no vendor in sight: a
provider adapter translates its protocol into these types and errors, and the answering use
case (§21) only ever sees them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from doculens.domain.conversations import MessageRole
from doculens.domain.errors import DependencyUnavailableError, DomainError, InvalidInputError


class LLMError(DomainError):
    """Base of every generation failure; carries where it happened for logs and metrics."""

    code = "LLM_ERROR"
    default_message = "The language model request could not be completed."

    def __init__(  # noqa: PLR0913 - the structured fields every provider error carries
        self,
        message: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        status_code: int | None = None,
        attempts: int = 1,
        retry_after_seconds: float | None = None,
        diagnostics: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.attempts = attempts
        self.retry_after_seconds = retry_after_seconds
        self.diagnostics = diagnostics


class LLMInputError(InvalidInputError):
    """The caller's messages cannot be sent (empty, malformed, over the size limit)."""

    code = "LLM_INPUT_INVALID"
    default_message = "The prompt cannot be sent to the language model."


class LLMProviderUnavailableError(LLMError, DependencyUnavailableError):
    """Timeouts, connection failures and server errors that outlived the retries (§67)."""

    code = "LLM_PROVIDER_UNAVAILABLE"
    default_message = "The language model is temporarily unavailable."


class LLMRateLimitedError(LLMProviderUnavailableError):
    code = "LLM_RATE_LIMITED"
    default_message = "The language model is rate limiting requests."


class LLMRequestRejectedError(LLMError):
    """The provider refused the request permanently (bad model, prompt too long, policy)."""

    code = "LLM_REQUEST_REJECTED"
    default_message = "The language model rejected the request."


class LLMResponseInvalidError(LLMError):
    code = "LLM_RESPONSE_INVALID"
    default_message = "The language model returned an unusable answer."


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One turn of the prompt; roles are the conversation roles of §7.7."""

    role: MessageRole
    content: str


class FinishReason(StrEnum):
    STOP = "stop"  # the model finished on its own
    LENGTH = "length"  # cut off by the output budget: the answer is incomplete
    CONTENT_FILTER = "content_filter"  # the provider withheld (part of) the answer
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """What one or more generations cost (§38); token counts are None when not reported."""

    requests: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            requests=self.requests + other.requests,
            input_tokens=_add_optional(self.input_tokens, other.input_tokens),
            output_tokens=_add_optional(self.output_tokens, other.output_tokens),
            latency_ms=self.latency_ms + other.latency_ms,
        )


def _add_optional(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """Per-call knobs the use case may set; the provider caps them by its configured limits."""

    max_output_tokens: int = 1024
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if self.max_output_tokens < 1:
            message = "max_output_tokens must be positive"
            raise ValueError(message)
        if not 0.0 <= self.temperature <= 2.0:  # noqa: PLR2004 - the range every API accepts
            message = "temperature must be between 0 and 2"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Generation:
    """The model's answer, which model produced it, why it stopped and what it cost."""

    text: str
    model: str
    finish_reason: FinishReason
    usage: LLMUsage

    @property
    def truncated(self) -> bool:
        return self.finish_reason is FinishReason.LENGTH


def validate_messages(messages: Sequence[ChatMessage], *, max_input_characters: int) -> None:
    """Refuse prompts no provider should be paid for: empty, blank turns, a system message
    anywhere but first, not ending with the user's turn, or over the character budget."""
    if not messages:
        message = "the prompt has no messages"
        raise LLMInputError(message)
    total = 0
    for index, item in enumerate(messages):
        if not item.content or not item.content.strip():
            message = f"message {index} is empty"
            raise LLMInputError(message)
        if item.role is MessageRole.SYSTEM and index != 0:
            message = "only the first message may be the system message"
            raise LLMInputError(message)
        total += len(item.content)
    if messages[-1].role is not MessageRole.USER:
        message = "the prompt must end with the user's message"
        raise LLMInputError(message)
    if total > max_input_characters:
        message = f"the prompt exceeds {max_input_characters} characters"
        raise LLMInputError(message)


__all__ = [
    "ChatMessage",
    "FinishReason",
    "Generation",
    "GenerationOptions",
    "LLMError",
    "LLMInputError",
    "LLMProviderUnavailableError",
    "LLMRateLimitedError",
    "LLMRequestRejectedError",
    "LLMResponseInvalidError",
    "LLMUsage",
    "validate_messages",
]
