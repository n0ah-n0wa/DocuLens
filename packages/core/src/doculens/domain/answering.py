"""Answering concepts (SPECIFICATIONS.md §21, §23, §25, §26, §38, OQ-29, OQ-30).

The pure rules of turning a generation into an answer with citations:

- :func:`is_insufficient` recognises the exact statement the policy prescribes, so an answer
  that says the documents are not enough is never presented as a grounded answer;
- :func:`clean_references` keeps the ``[n]`` references that point at a context item, drops
  the ones that do not (an invented index is a citation error, never a citation) and reports
  how many were dropped;
- :func:`build_citations` turns the valid references into persisted ``Citation`` records that
  quote the evidence itself (§23), in order of first mention;
- the query-rewriting prompt (§26) and its acceptance rule live here too, so the rewriter's
  behaviour is testable without a model.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from doculens.domain.conversations import Citation, MessageRole
from doculens.domain.embeddings import EmbeddingUsage
from doculens.domain.errors import DependencyUnavailableError
from doculens.domain.llm import ChatMessage, FinishReason, LLMUsage
from doculens.domain.prompting import (
    INSUFFICIENT_EVIDENCE_STATEMENT,
    QUESTION_TAG,
    SYSTEM_INSTRUCTIONS,
)
from doculens.domain.reranking import RerankingReport
from doculens.domain.retrieval import AssembledContext

# A reference is one to three digits: context items never number more, and bracketed
# figures such as [2024] stay part of the answer text.
_REFERENCE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")
_WHITESPACE = re.compile(r"[ \t]+")
_SPACE_BEFORE_PUNCTUATION = re.compile(r" +([.,;:!?])")


class AnswerOutcome(StrEnum):
    ANSWERED = "answered"  # grounded answer with citations
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # no answer was attempted or given
    BLOCKED = "blocked"  # the model's answer broke the grounding rules and was withheld


class GenerationTimeoutError(DependencyUnavailableError):
    """The model did not answer within the per-question budget; retryable (§67)."""

    code = "GENERATION_TIMEOUT"
    default_message = "Generating the answer took too long; please try again."


BLOCKED_ANSWER_STATEMENT = "The answer was withheld because it did not follow the grounding rules."

# Lines of the policy that never belong in an answer; two of them together mean the model
# reproduced its instructions (a document or a question asked it to reveal them).
_POLICY_MARKERS = tuple(
    line.strip()
    for line in SYSTEM_INSTRUCTIONS.splitlines()
    if line.startswith(("## ", "You are DocuLens"))
)


def normalise_text(text: str) -> str:
    return " ".join(text.split()).strip()


def is_insufficient(text: str) -> bool:
    """The model (or the pipeline) answered with the prescribed insufficient-evidence statement."""
    return normalise_text(text).lower().startswith(INSUFFICIENT_EVIDENCE_STATEMENT.lower())


def detect_violation(text: str) -> str | None:
    """Why an answer must be withheld, or None: today, reproducing the system policy."""
    normalised = normalise_text(text).lower()
    leaked = sum(1 for marker in _POLICY_MARKERS if marker.lower() in normalised)
    return "system_prompt_leak" if leaked >= 2 else None  # noqa: PLR2004 - two policy lines


@dataclass(frozen=True, slots=True)
class CleanedAnswer:
    text: str
    cited: tuple[int, ...]  # valid indexes in order of first mention
    invalid_references: int


def clean_references(text: str, allowed: frozenset[int]) -> CleanedAnswer:
    """Keep ``[n]`` markers that reference a context item; drop the rest and tidy spacing."""
    cited: list[int] = []
    invalid = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal invalid
        kept: list[str] = []
        for raw in match.group(1).split(","):
            index = int(raw.strip())
            if index in allowed:
                if index not in cited:
                    cited.append(index)
                kept.append(str(index))
            else:
                invalid += 1
        return "".join(f"[{index}]" for index in kept)

    cleaned = _REFERENCE.sub(replace, text)
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", cleaned)
    return CleanedAnswer(text=cleaned.strip(), cited=tuple(cited), invalid_references=invalid)


def build_citations(
    message_id: UUID,
    cited: Sequence[int],
    context: AssembledContext,
    *,
    max_quote_characters: int,
    new_id: Callable[[], UUID],
) -> tuple[Citation, ...]:
    """One citation per referenced context item, quoting the evidence, in reference order."""
    by_index = {item.index: item.evidence for item in context.items}
    citations: list[Citation] = []
    for order, index in enumerate(cited):
        evidence = by_index[index]
        recorded = evidence.metadata.get("retrieval_score")
        retrieval_score = float(recorded) if isinstance(recorded, int | float) else None
        reranked = retrieval_score is not None
        citations.append(
            Citation(
                id=new_id(),
                message_id=message_id,
                document_id=evidence.document_id,
                page_number=evidence.page_number,
                quoted_text=evidence.text[:max_quote_characters],
                retrieval_score=retrieval_score if retrieval_score is not None else evidence.score,
                citation_order=order,
                chunk_id=evidence.chunk_id,
                reranking_score=evidence.score if reranked else None,
            )
        )
    return tuple(citations)


# -- query rewriting (§26) ------------------------------------------------------------------------

REWRITE_PROMPT_VERSION = "rewrite-v1"
REWRITE_INSTRUCTIONS = (
    "You rewrite the latest question of a conversation into a standalone search query.\n"
    "- Resolve pronouns and references using the earlier turns.\n"
    "- Keep the user's language, intent and every detail; add nothing that was not asked.\n"
    "- If the question already stands on its own, return it unchanged.\n"
    "- Output only the rewritten question, on one line, without quotes or commentary.\n"
)
REWRITE_HISTORY_TAG = "conversation"


def build_rewrite_messages(question: str, history: Sequence[ChatMessage]) -> list[ChatMessage]:
    """The rewriting prompt: instructions, the earlier turns as data, the question last."""
    transcript = "\n".join(
        f"{message.role.value.lower()}: {normalise_text(message.content)}" for message in history
    )
    user = (
        f"<{REWRITE_HISTORY_TAG}>\n{transcript}\n</{REWRITE_HISTORY_TAG}>\n\n"
        f"<{QUESTION_TAG}>\n{normalise_text(question)}\n</{QUESTION_TAG}>"
    )
    return [
        ChatMessage(MessageRole.SYSTEM, REWRITE_INSTRUCTIONS),
        ChatMessage(MessageRole.USER, user),
    ]


def question_of_rewrite_prompt(messages: Sequence[ChatMessage]) -> str | None:
    """The question a rewriting prompt carries (used by the fake provider to echo it)."""
    if not messages or messages[0].content != REWRITE_INSTRUCTIONS:
        return None
    match = re.search(
        rf"<{QUESTION_TAG}>\n(.*)\n</{QUESTION_TAG}>", messages[-1].content, re.DOTALL
    )
    return match.group(1) if match else None


def accept_rewrite(candidate: str, original: str, *, max_characters: int) -> str | None:
    """A rewrite is used only when it is a plausible standalone question: non-empty, one
    line, bounded, and not an answer or a refusal in disguise."""
    text = normalise_text(candidate).strip("\"'")
    if not text or len(text) > max_characters or is_insufficient(text):
        return None
    if len(text) > 4 * max(len(normalise_text(original)), 20):
        return None  # far longer than the question it rewrites: the model started answering
    return text


# -- the structured result (§21, §38) -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnswerTiming:
    rewrite_ms: int = 0
    retrieval_ms: int = 0
    generation_ms: int = 0
    persistence_ms: int = 0
    total_ms: int = 0


@dataclass(frozen=True, slots=True)
class AnswerUsage:
    embeddings: EmbeddingUsage = field(default_factory=EmbeddingUsage)
    generation: LLMUsage = field(default_factory=LLMUsage)
    rewriting: LLMUsage = field(default_factory=LLMUsage)

    @property
    def llm(self) -> LLMUsage:
        return self.generation + self.rewriting


@dataclass(frozen=True, slots=True)
class RetrievalMetadata:
    """What retrieval did for this answer, without the evidence text itself."""

    question: str  # as asked
    query: str  # what was retrieved for (after rewriting, if any)
    rewritten: bool
    retriever: str
    documents_in_scope: int
    hits: int
    evidence: int
    context_items: int
    context_characters: int
    reranking: RerankingReport
    prompt_version: str | None
    model: str | None
    finish_reason: FinishReason | None
    invalid_references: int
    violation: str | None = None  # why the answer was withheld, when it was


@dataclass(frozen=True, slots=True)
class AnswerResult:
    outcome: AnswerOutcome
    answer: str
    citations: tuple[Citation, ...]
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    retrieval: RetrievalMetadata
    usage: AnswerUsage
    timing: AnswerTiming

    @property
    def grounded(self) -> bool:
        return self.outcome is AnswerOutcome.ANSWERED

    @property
    def truncated(self) -> bool:
        return self.retrieval.finish_reason is FinishReason.LENGTH

    @property
    def uncited(self) -> bool:
        """An answer that cites nothing although evidence was offered: a groundedness signal
        for the caller and the evaluation suite (§23, §62)."""
        return self.outcome is AnswerOutcome.ANSWERED and not self.citations


__all__ = [
    "BLOCKED_ANSWER_STATEMENT",
    "REWRITE_INSTRUCTIONS",
    "REWRITE_PROMPT_VERSION",
    "AnswerOutcome",
    "AnswerResult",
    "AnswerTiming",
    "AnswerUsage",
    "CleanedAnswer",
    "GenerationTimeoutError",
    "RetrievalMetadata",
    "accept_rewrite",
    "build_citations",
    "build_rewrite_messages",
    "clean_references",
    "detect_violation",
    "is_insufficient",
    "normalise_text",
    "question_of_rewrite_prompt",
]
