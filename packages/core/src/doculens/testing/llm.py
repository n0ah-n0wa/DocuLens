"""Deterministic fake ``LLMProvider`` for tests and local development (OQ-17).

Answers are scripted or derived from the last user message, so tests can predict them; calls
and options are recorded, and delays and failures can be scripted so use cases can be tested
against provider behaviour without a network.
"""

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from doculens.application.llm import LLMLimits
from doculens.domain.answering import question_of_rewrite_prompt
from doculens.domain.conversations import MessageRole
from doculens.domain.llm import (
    ChatMessage,
    FinishReason,
    Generation,
    GenerationOptions,
    LLMUsage,
    validate_messages,
)
from doculens.domain.prompting import (
    INSUFFICIENT_EVIDENCE_STATEMENT,
    NO_DOCUMENTS_MARKER,
    QUESTION_TAG,
)

DEFAULT_LIMITS = LLMLimits(max_input_characters=200_000, max_output_tokens=1024)


@dataclass
class FakeLLMProvider:
    """Records every call; ``failures`` are raised (and consumed) before any answer is made;
    ``responses`` are returned in order. Without a script the fake behaves like a well-behaved
    model: it echoes the question of a grounded prompt citing the first document (or gives the
    insufficient-evidence statement when no document was retrieved), returns a rewriting
    prompt's question unchanged, and echoes any other prompt's last user message."""

    model_name: str = "fake-llm-v1"
    limits: LLMLimits = DEFAULT_LIMITS
    responses: list[str] = field(default_factory=list)
    failures: list[Exception] = field(default_factory=list)
    delay_seconds: float = 0.0
    characters_per_token: int = 4
    calls: list[list[ChatMessage]] = field(default_factory=list)
    options_seen: list[GenerationOptions] = field(default_factory=list)

    name = "fake"

    @property
    def model(self) -> str:
        return self.model_name

    async def generate(
        self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None
    ) -> Generation:
        validate_messages(messages, max_input_characters=self.limits.max_input_characters)
        chosen = options or GenerationOptions()
        self.calls.append(list(messages))
        self.options_seen.append(chosen)
        if self.failures:
            raise self.failures.pop(0)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        text = self.responses.pop(0) if self.responses else self._default_answer(messages)
        budget = min(chosen.max_output_tokens, self.limits.max_output_tokens)
        finish_reason = FinishReason.STOP
        if self._tokens(text) > budget:
            text = text[: budget * self.characters_per_token]
            finish_reason = FinishReason.LENGTH
        return Generation(
            text=text,
            model=self.model,
            finish_reason=finish_reason,
            usage=LLMUsage(
                requests=1,
                input_tokens=self._tokens("".join(m.content for m in messages)),
                output_tokens=self._tokens(text),
                latency_ms=1.0,
            ),
        )

    def _default_answer(self, messages: Sequence[ChatMessage]) -> str:
        rewrite = question_of_rewrite_prompt(messages)
        if rewrite is not None:
            return rewrite
        user = next(m for m in reversed(messages) if m.role is MessageRole.USER)
        grounded = re.search(rf"<{QUESTION_TAG}>\n(.*)\n</{QUESTION_TAG}>", user.content, re.DOTALL)
        if grounded is None:
            return f"Fake answer to: {user.content.strip()}"
        if NO_DOCUMENTS_MARKER in user.content:
            return INSUFFICIENT_EVIDENCE_STATEMENT
        return f"Fake answer to: {grounded.group(1).strip()} [1]"

    def _tokens(self, text: str) -> int:
        return -(-len(text) // self.characters_per_token)  # ceiling division


__all__ = ["FakeLLMProvider"]
