"""Language-model port (SPECIFICATIONS.md §21, §38, §67, §73).

The answering use case generates through :class:`LLMProvider` and never sees a vendor SDK or
HTTP client. Every adapter honours the same contract:

- ``generate`` takes the prompt as ordered ``ChatMessage`` turns (system first, user last) and
  returns a ``Generation`` with the text, the model that produced it, why it stopped and usage;
- prompts are validated with ``validate_messages`` before any network call, so a caller bug
  never becomes a paid request; ``max_output_tokens`` is capped by the configured limit;
- every request has a timeout and transient failures are retried with bounded exponential
  backoff inside the adapter; what escapes is a structured error of ``doculens.domain.llm``:
  ``LLMProviderUnavailableError`` (or its rate-limited subtype) after the retries,
  ``LLMRequestRejectedError`` for permanent rejections, ``LLMResponseInvalidError`` for
  malformed answers, ``LLMInputError`` for the caller's own mistakes;
- usage (requests, input and output tokens when reported, latency) travels with every
  generation for the §38 ledger; the API key never appears in logs or errors.

Streaming (§44) is added to this port when the chat endpoint arrives.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from doculens.domain.llm import ChatMessage, Generation, GenerationOptions


@dataclass(frozen=True, slots=True)
class LLMLimits:
    """Bounds every adapter applies; injected from configuration, never hard-coded."""

    max_input_characters: int
    max_output_tokens: int

    def __post_init__(self) -> None:
        if self.max_input_characters < 1 or self.max_output_tokens < 1:
            message = "language model limits must be positive"
            raise ValueError(message)


class LLMProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable provider identifier for logs, metrics and the usage ledger."""
        ...

    @property
    def model(self) -> str:
        """The configured model; a generation reports the model that actually answered."""
        ...

    async def generate(
        self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None
    ) -> Generation:
        """One complete answer to the prompt."""
        ...


__all__ = ["LLMLimits", "LLMProvider"]
