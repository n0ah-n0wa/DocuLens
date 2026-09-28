"""Deterministic RAG metric scorers (SPECIFICATIONS.md §62).

Scores are in ``[0, 1]`` when applicable. Exact LLM wording is never required — outcomes,
term presence, evidence overlap, citation validity and claim-to-citation attribution are.

Metrics that do not apply to a case (for example retrieval precision with no gold evidence)
are marked ``applicable=False`` and are excluded from case pass/fail and from suite means.
That avoids the misleading pattern of scoring N/A as 1.0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from doculens.domain.answering import (
    BLOCKED_ANSWER_STATEMENT,
    AnswerOutcome,
    AnswerResult,
    is_insufficient,
    normalise_text,
)
from doculens.domain.conversations import Citation
from doculens.domain.retrieval import AssembledContext, Evidence
from doculens.evaluation.cases import EvalCase, ExpectedEvidence


class MetricName(StrEnum):
    RETRIEVAL_RELEVANCE = "retrieval_relevance"
    RETRIEVAL_PRECISION = "retrieval_precision"
    CONTEXT_PRECISION = "context_precision"
    CONTEXT_RECALL = "context_recall"
    CITATION_CORRECTNESS = "citation_correctness"
    ANSWER_RELEVANCE = "answer_relevance"
    GROUNDEDNESS = "groundedness"
    FAILURE_BEHAVIOR = "failure_behavior"


@dataclass(frozen=True, slots=True)
class MetricScore:
    name: MetricName
    value: float
    detail: str = ""
    applicable: bool = True


@dataclass(frozen=True, slots=True)
class CaseScores:
    case_id: str
    metrics: tuple[MetricScore, ...]
    passed: bool
    category: str = ""
    description: str = ""
    sufficient_evidence: bool = True
    answer_source: str = ""
    notes: tuple[str, ...] = ()

    def value(self, name: MetricName) -> float:
        for metric in self.metrics:
            if metric.name is name:
                if not metric.applicable:
                    message = f"metric {name} is not applicable"
                    raise KeyError(message)
                return metric.value
        message = f"missing metric {name}"
        raise KeyError(message)

    def score(self, name: MetricName) -> MetricScore:
        for metric in self.metrics:
            if metric.name is name:
                return metric
        message = f"missing metric {name}"
        raise KeyError(message)


@dataclass(frozen=True, slots=True)
class ScoreThresholds:
    """Minimum acceptable score per applicable metric (inclusive)."""

    values: dict[MetricName, float] = field(
        default_factory=lambda: {
            # Gold recall in the retrieved candidate set.
            MetricName.RETRIEVAL_RELEVANCE: 1.0,
            # Share of retrieved candidates that match gold (noisy hybrid retrieval is expected).
            MetricName.RETRIEVAL_PRECISION: 0.1,
            # Share of assembled context that matches gold — informational gate, not a tight one.
            MetricName.CONTEXT_PRECISION: 0.15,
            MetricName.CONTEXT_RECALL: 1.0,
            MetricName.CITATION_CORRECTNESS: 0.67,
            MetricName.ANSWER_RELEVANCE: 1.0,
            MetricName.GROUNDEDNESS: 1.0,
            MetricName.FAILURE_BEHAVIOR: 1.0,
        }
    )


def evidence_matches(gold: ExpectedEvidence, evidence: Evidence) -> bool:
    if gold.text_contains.casefold() not in evidence.text.casefold():
        return False
    if gold.page_number is not None and evidence.page_number != gold.page_number:
        return False
    return gold.filename is None or evidence.filename == gold.filename


def citation_matches_target(citation: Citation, gold: ExpectedEvidence) -> bool:
    if gold.text_contains.casefold() not in citation.quoted_text.casefold():
        return False
    return gold.page_number is None or citation.page_number == gold.page_number


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _na(name: MetricName, detail: str) -> MetricScore:
    return MetricScore(name, value=0.0, detail=detail, applicable=False)


def score_retrieval_relevance(
    *, gold: Sequence[ExpectedEvidence], retrieved: Sequence[Evidence]
) -> MetricScore:
    """Gold recall among retrieved candidates (does the right evidence appear at all?)."""
    if not gold:
        return _na(MetricName.RETRIEVAL_RELEVANCE, "n/a: no gold evidence")
    if not retrieved:
        return MetricScore(MetricName.RETRIEVAL_RELEVANCE, 0.0, detail="empty retrieval")
    covered = sum(1 for target in gold if any(evidence_matches(target, item) for item in retrieved))
    return MetricScore(
        MetricName.RETRIEVAL_RELEVANCE,
        _ratio(covered, len(gold)),
        detail=f"{covered}/{len(gold)} gold descriptors found in retrieval",
    )


def score_retrieval_precision(
    *, gold: Sequence[ExpectedEvidence], retrieved: Sequence[Evidence]
) -> MetricScore:
    """Share of retrieved candidates that match at least one gold descriptor."""
    if not gold:
        return _na(MetricName.RETRIEVAL_PRECISION, "n/a: no gold evidence")
    if not retrieved:
        return MetricScore(MetricName.RETRIEVAL_PRECISION, 0.0, detail="empty retrieval")
    hits = sum(1 for item in retrieved if any(evidence_matches(g, item) for g in gold))
    return MetricScore(
        MetricName.RETRIEVAL_PRECISION,
        _ratio(hits, len(retrieved)),
        detail=f"{hits}/{len(retrieved)} retrieved items match gold",
    )


def score_context_precision(
    *, gold: Sequence[ExpectedEvidence], context: AssembledContext
) -> MetricScore:
    if not gold:
        return _na(MetricName.CONTEXT_PRECISION, "n/a: no gold evidence")
    items = [entry.evidence for entry in context.items]
    if not items:
        return MetricScore(MetricName.CONTEXT_PRECISION, 0.0, detail="empty context")
    hits = sum(1 for item in items if any(evidence_matches(g, item) for g in gold))
    return MetricScore(
        MetricName.CONTEXT_PRECISION,
        _ratio(hits, len(items)),
        detail=f"{hits}/{len(items)} context items match gold",
    )


def score_context_recall(
    *, gold: Sequence[ExpectedEvidence], context: AssembledContext
) -> MetricScore:
    if not gold:
        return _na(MetricName.CONTEXT_RECALL, "n/a: no gold evidence")
    items = [entry.evidence for entry in context.items]
    covered = sum(1 for target in gold if any(evidence_matches(target, item) for item in items))
    return MetricScore(
        MetricName.CONTEXT_RECALL,
        _ratio(covered, len(gold)),
        detail=f"{covered}/{len(gold)} gold descriptors covered",
    )


def score_citation_correctness(
    *,
    result: AnswerResult,
    context: AssembledContext,
    citation_targets: Sequence[ExpectedEvidence],
    sufficient_evidence: bool,
) -> MetricScore:
    notes: list[str] = []
    invalid = result.retrieval.invalid_references
    if invalid > 0:
        notes.append(f"invalid_references={invalid}")

    if result.outcome in {AnswerOutcome.INSUFFICIENT_EVIDENCE, AnswerOutcome.BLOCKED}:
        ok = not result.citations and invalid == 0
        if result.citations:
            notes.append(f"{result.outcome.value} must not cite")
        return MetricScore(
            MetricName.CITATION_CORRECTNESS,
            1.0 if ok else 0.0,
            detail="; ".join(notes) if notes else "ok",
        )

    if not sufficient_evidence:
        # Expected answer path was answered despite insufficient flag — still judge citations.
        pass

    context_texts = [entry.evidence.text for entry in context.items]
    valid = sum(
        1
        for citation in result.citations
        if citation.quoted_text and any(citation.quoted_text in text for text in context_texts)
    )
    if result.citations:
        validity = _ratio(valid, len(result.citations))
    else:
        validity = 0.0 if sufficient_evidence else 1.0
        if sufficient_evidence:
            notes.append("answered without citations")

    if not citation_targets:
        # No gold targets: quote validity only (often tautological — see methodology.md).
        value = 0.0 if invalid > 0 else validity
        notes.append("no gold citation targets; quote-validity only")
        return MetricScore(
            MetricName.CITATION_CORRECTNESS,
            value,
            detail="; ".join(notes) if notes else "ok",
        )

    target_hits = sum(
        1
        for target in citation_targets
        if any(citation_matches_target(citation, target) for citation in result.citations)
    )
    target_recall = _ratio(target_hits, len(citation_targets))
    notes.append(f"gold citation targets hit {target_hits}/{len(citation_targets)}")

    if result.citations:
        precise = sum(
            1
            for citation in result.citations
            if any(citation_matches_target(citation, target) for target in citation_targets)
        )
        citation_precision = _ratio(precise, len(result.citations))
        notes.append(f"citation precision vs gold {precise}/{len(result.citations)}")
    else:
        citation_precision = 0.0

    value = 0.0 if invalid > 0 else (validity + target_recall + citation_precision) / 3.0
    return MetricScore(
        MetricName.CITATION_CORRECTNESS,
        value,
        detail="; ".join(notes) if notes else "ok",
    )


def score_answer_relevance(*, case: EvalCase, result: AnswerResult) -> MetricScore:
    expected = case.expected_answer
    if result.outcome is not expected.outcome:
        return MetricScore(
            MetricName.ANSWER_RELEVANCE,
            0.0,
            detail=f"outcome {result.outcome.value} != {expected.outcome.value}",
        )

    if expected.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE:
        ok = is_insufficient(result.answer)
        return MetricScore(
            MetricName.ANSWER_RELEVANCE,
            1.0 if ok else 0.0,
            detail="insufficient-evidence statement"
            if ok
            else "missing insufficient-evidence statement",
        )

    if expected.outcome is AnswerOutcome.BLOCKED:
        ok = (
            normalise_text(result.answer).casefold()
            == normalise_text(BLOCKED_ANSWER_STATEMENT).casefold()
        )
        return MetricScore(
            MetricName.ANSWER_RELEVANCE,
            1.0 if ok else 0.0,
            detail="blocked statement" if ok else "missing blocked statement",
        )

    answer = normalise_text(result.answer).casefold()
    required = expected.must_include_terms
    forbidden = expected.must_not_include_terms
    present = sum(1 for term in required if term.casefold() in answer)
    banned = sum(1 for term in forbidden if term.casefold() in answer)
    term_score = _ratio(present, len(required)) if required else 1.0
    if banned:
        term_score = 0.0
    return MetricScore(
        MetricName.ANSWER_RELEVANCE,
        term_score,
        detail=f"terms {present}/{len(required)}; banned={banned}",
    )


def _cited_evidence_text(result: AnswerResult, context: AssembledContext) -> str:
    """Concatenate evidence text for citations (by chunk id / quote), for attribution checks."""
    by_chunk = {entry.evidence.chunk_id: entry.evidence.text for entry in context.items}
    by_quote = {entry.evidence.text: entry.evidence.text for entry in context.items}
    parts: list[str] = []
    for citation in result.citations:
        if citation.chunk_id is not None and citation.chunk_id in by_chunk:
            parts.append(by_chunk[citation.chunk_id])
        elif citation.quoted_text in by_quote or citation.quoted_text:
            parts.append(citation.quoted_text)
    return "\n".join(parts)


def score_groundedness(
    *, case: EvalCase, result: AnswerResult, context: AssembledContext
) -> MetricScore:
    """Attribution + refusal integrity — not free-form factuality against a judge model."""
    expected = case.expected_answer
    if result.outcome is not expected.outcome:
        return MetricScore(MetricName.GROUNDEDNESS, 0.0, detail="outcome mismatch")

    if result.outcome is AnswerOutcome.BLOCKED:
        ok = not result.citations
        return MetricScore(
            MetricName.GROUNDEDNESS,
            1.0 if ok else 0.0,
            detail="withheld unsafe answer" if ok else "blocked answer still cited",
        )

    if not case.sufficient_evidence:
        ok = result.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE and not result.citations
        fabricated = bool(expected.must_not_include_terms) and any(
            term.casefold() in normalise_text(result.answer).casefold()
            for term in expected.must_not_include_terms
        )
        if fabricated:
            ok = False
        return MetricScore(
            MetricName.GROUNDEDNESS,
            1.0 if ok else 0.0,
            detail="refuse without fabricating" if ok else "fabricated answer or citations",
        )

    reasons: list[str] = []
    if result.outcome is not AnswerOutcome.ANSWERED:
        reasons.append("expected answered")
    if expected.require_citations and result.uncited:
        reasons.append("answered without citations")
    if result.retrieval.invalid_references > expected.max_invalid_references:
        reasons.append(f"invalid_references={result.retrieval.invalid_references}")
    answer = normalise_text(result.answer).casefold()
    banned = [term for term in expected.must_not_include_terms if term.casefold() in answer]
    if banned:
        reasons.append(f"forbidden terms {banned}")
    context_texts = [entry.evidence.text for entry in context.items]
    if result.citations and not all(
        any(citation.quoted_text and citation.quoted_text in text for text in context_texts)
        for citation in result.citations
    ):
        reasons.append("citation quote not in context")

    # Claim→citation attribution: required answer terms must appear in cited evidence, not only
    # in the answer string (guards against citing irrelevant chunks while stating gold facts).
    if expected.must_include_terms and result.citations:
        cited_blob = _cited_evidence_text(result, context).casefold()
        unsupported = [
            term for term in expected.must_include_terms if term.casefold() not in cited_blob
        ]
        if unsupported:
            reasons.append(f"terms not in cited evidence: {unsupported}")
    elif expected.must_include_terms and not result.citations:
        reasons.append("cannot attribute terms without citations")

    if reasons:
        return MetricScore(MetricName.GROUNDEDNESS, 0.0, detail="; ".join(reasons))
    return MetricScore(MetricName.GROUNDEDNESS, 1.0, detail="grounded")


def score_failure_behavior(*, case: EvalCase, result: AnswerResult) -> MetricScore:
    """Dedicated score for refusal / withhold paths (N/A when the case expects an answer)."""
    expected = case.expected_answer
    if expected.outcome is AnswerOutcome.ANSWERED:
        return _na(MetricName.FAILURE_BEHAVIOR, "n/a: answered case")

    if result.outcome is not expected.outcome:
        return MetricScore(
            MetricName.FAILURE_BEHAVIOR,
            0.0,
            detail=f"outcome {result.outcome.value} != {expected.outcome.value}",
        )

    if result.citations:
        return MetricScore(
            MetricName.FAILURE_BEHAVIOR,
            0.0,
            detail="failure path must not emit citations",
        )

    if expected.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE:
        if not is_insufficient(result.answer):
            return MetricScore(
                MetricName.FAILURE_BEHAVIOR,
                0.0,
                detail="missing insufficient-evidence statement",
            )
        fabricated = any(
            term.casefold() in normalise_text(result.answer).casefold()
            for term in expected.must_not_include_terms
        )
        if fabricated:
            return MetricScore(
                MetricName.FAILURE_BEHAVIOR,
                0.0,
                detail="refusal appended fabricated content",
            )
        return MetricScore(
            MetricName.FAILURE_BEHAVIOR,
            1.0,
            detail="refused without fabrication",
        )

    # Remaining failure outcome: BLOCKED.
    ok = (
        normalise_text(result.answer).casefold()
        == normalise_text(BLOCKED_ANSWER_STATEMENT).casefold()
    )
    return MetricScore(
        MetricName.FAILURE_BEHAVIOR,
        1.0 if ok else 0.0,
        detail="withheld" if ok else "missing blocked statement",
    )


def score_case(
    case: EvalCase,
    *,
    retrieved: Sequence[Evidence],
    context: AssembledContext,
    result: AnswerResult,
    thresholds: ScoreThresholds | None = None,
    answer_source: str = "",
) -> CaseScores:
    limits = thresholds or ScoreThresholds()
    metrics = (
        score_retrieval_relevance(gold=case.expected_evidence, retrieved=retrieved),
        score_retrieval_precision(gold=case.expected_evidence, retrieved=retrieved),
        score_context_precision(gold=case.expected_evidence, context=context),
        score_context_recall(gold=case.expected_evidence, context=context),
        score_citation_correctness(
            result=result,
            context=context,
            citation_targets=case.expected_citation_targets,
            sufficient_evidence=case.sufficient_evidence,
        ),
        score_answer_relevance(case=case, result=result),
        score_groundedness(case=case, result=result, context=context),
        score_failure_behavior(case=case, result=result),
    )
    notes = tuple(metric.detail for metric in metrics if metric.detail and metric.applicable)
    applicable = [metric for metric in metrics if metric.applicable]
    passed = all(metric.value >= limits.values.get(metric.name, 1.0) for metric in applicable)
    return CaseScores(
        case_id=case.id,
        metrics=metrics,
        passed=passed,
        category=case.category.value,
        description=case.description,
        sufficient_evidence=case.sufficient_evidence,
        answer_source=answer_source,
        notes=notes,
    )


__all__ = [
    "CaseScores",
    "MetricName",
    "MetricScore",
    "ScoreThresholds",
    "citation_matches_target",
    "evidence_matches",
    "score_answer_relevance",
    "score_case",
    "score_citation_correctness",
    "score_context_precision",
    "score_context_recall",
    "score_failure_behavior",
    "score_groundedness",
    "score_retrieval_precision",
    "score_retrieval_relevance",
]
