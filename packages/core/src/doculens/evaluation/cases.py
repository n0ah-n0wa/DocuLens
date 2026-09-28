"""Curated evaluation case shapes (SPECIFICATIONS.md §62, §63)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from doculens.domain.answering import AnswerOutcome


class EvalCategory(StrEnum):
    """§63 / adversarial coverage tags for the curated corpus."""

    ANSWER_PRESENT = "answer_present"
    MULTI_PAGE = "answer_multi_page"
    MULTI_DOCUMENT = "answer_multi_document"
    AMBIGUOUS = "ambiguous_question"
    UNAVAILABLE = "unavailable_answer"
    CONTRADICTORY = "contradictory_sources"
    PROMPT_INJECTION = "prompt_injection"
    IRRELEVANT_DOCUMENTS = "irrelevant_documents"


@dataclass(frozen=True, slots=True)
class ExpectedEvidence:
    """Gold evidence identified by stable content, not by ephemeral chunk UUIDs."""

    text_contains: str
    page_number: int | None = None
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class ExpectedAnswerCharacteristics:
    """Structural expectations for the answer (outcome, terms, citation rules)."""

    outcome: AnswerOutcome
    must_include_terms: tuple[str, ...] = ()
    must_not_include_terms: tuple[str, ...] = ()
    require_citations: bool = True
    max_invalid_references: int = 0


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One curated evaluation question with gold evidence and answer expectations."""

    id: str
    question: str
    document_set: str
    expected_evidence: tuple[ExpectedEvidence, ...]
    expected_answer: ExpectedAnswerCharacteristics
    expected_citation_targets: tuple[ExpectedEvidence, ...]
    sufficient_evidence: bool
    category: EvalCategory
    description: str = ""


__all__ = [
    "EvalCase",
    "EvalCategory",
    "ExpectedAnswerCharacteristics",
    "ExpectedEvidence",
]
