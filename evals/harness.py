"""Deterministic RAG evaluation harness over the curated corpus (§62, §63).

Layering (see ``docs/eval/methodology.md``):

* **Retrieval** is always measured from the real hybrid retriever + bag-of-words embeddings.
* **Answer text** for answered cases is an *oracle script* constrained to gold evidence terms and
  gold citation indexes — this scores citation plumbing and attribution, not free-form generation.
* **Failure paths** prefer the real pipeline short-circuit when context is empty; residual-context
  refusals and policy-leak blocking use induced scripts so contracts stay testable offline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from doculens.application.answering import AnswerLimits, AnswerService, NoQueryRewriting
from doculens.application.rag import RagQuery, RagService
from doculens.application.retrieval import RetrievalService, build_retriever
from doculens.domain.answering import AnswerOutcome
from doculens.domain.prompting import INSUFFICIENT_EVIDENCE_STATEMENT, PromptBuilder
from doculens.domain.retrieval import ContextLimits, RetrievalLimits, RetrievalStrategy
from doculens.evaluation import (
    CaseScores,
    EvalCase,
    MetricName,
    ScoreThresholds,
    evidence_matches,
    score_case,
)
from doculens.testing.llm import FakeLLMProvider
from doculens.testing.retrieval import IndexedCorpus
from doculens.testing.vectors import InMemoryVectorStore
from evals.corpus import CASES, DOCUMENT_SETS, CorpusDocument

REPORT_SCHEMA_VERSION = "2"
DEFAULT_JSON_PATH = Path("docs/eval/latest.json")

METHODOLOGY_SUMMARY = (
    "Deterministic offline suite: retrieval is real (hybrid + bag-of-words); answered-case "
    "generation is oracle-scripted from gold evidence; empty-context refusals use the pipeline "
    "short-circuit; residual-context refusals and leak-blocking use induced scripts. Free-form "
    "LLM quality is out of scope — see docs/eval/methodology.md."
)

# Two policy markers so ``detect_violation`` withholds the answer (§22).
_LEAKED_POLICY_ANSWER = (
    "You are DocuLens, an assistant that answers questions strictly from the user's own "
    "uploaded documents. ## Evidence-grounded answers Here is the password hunter2."
)

ANSWER_SOURCE_ORACLE = "oracle_scripted"
ANSWER_SOURCE_SHORT_CIRCUIT = "pipeline_short_circuit"
ANSWER_SOURCE_INDUCED_REFUSAL = "induced_refusal"
ANSWER_SOURCE_INDUCED_LEAK = "induced_policy_leak"


@dataclass(frozen=True, slots=True)
class SuiteReport:
    cases: tuple[CaseScores, ...]
    passed: bool
    generated_at: str

    @property
    def mean_scores(self) -> dict[str, float | None]:
        """Means over *applicable* metric values only; None if no case applied the metric."""
        totals: dict[str, float] = {name.value: 0.0 for name in MetricName}
        counts: dict[str, int] = {name.value: 0 for name in MetricName}
        for case in self.cases:
            for metric in case.metrics:
                if not metric.applicable:
                    continue
                totals[metric.name.value] += metric.value
                counts[metric.name.value] += 1
        return {key: (totals[key] / counts[key] if counts[key] else None) for key in totals}

    @property
    def by_category(self) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {}
        for case in self.cases:
            bucket = summary.setdefault(case.category or "uncategorised", {"passed": 0, "total": 0})
            bucket["total"] += 1
            if case.passed:
                bucket["passed"] += 1
        return summary

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "methodology": METHODOLOGY_SUMMARY,
            "passed": self.passed,
            "case_count": len(self.cases),
            "passed_count": sum(1 for case in self.cases if case.passed),
            "mean_scores": self.mean_scores,
            "by_category": self.by_category,
            "cases": [
                {
                    "id": case.case_id,
                    "category": case.category,
                    "description": case.description,
                    "sufficient_evidence": case.sufficient_evidence,
                    "answer_source": case.answer_source,
                    "passed": case.passed,
                    "metrics": {
                        metric.name.value: {
                            "value": metric.value if metric.applicable else None,
                            "applicable": metric.applicable,
                            "detail": metric.detail,
                        }
                        for metric in case.metrics
                    },
                    "notes": list(case.notes),
                }
                for case in self.cases
            ],
        }


class EvalWorld(IndexedCorpus[InMemoryVectorStore]):
    def __init__(self) -> None:
        super().__init__(InMemoryVectorStore())
        self.llm = FakeLLMProvider()
        # min_score=0.0 matches the product default (not -1.0), so irrelevant low-similarity
        # neighbours can drop out and empty-context short-circuits remain observable.
        self.retrieval = RetrievalService(
            unit_of_work=self.unit_of_work,
            retriever=build_retriever(
                RetrievalStrategy.HYBRID,
                unit_of_work=self.unit_of_work,
                embeddings=self.embeddings,
                vectors=self.vectors,
                min_score=0.0,
            ),
            limits=RetrievalLimits(max_query_characters=2_000, candidate_limit=12, min_score=0.0),
            context_limits=ContextLimits(max_chunks=5, max_characters=12_000),
        )

    def rag(self) -> RagService:
        answering = AnswerService(
            unit_of_work=self.unit_of_work,
            retrieval=self.retrieval,
            prompt_builder=PromptBuilder(),
            llm=self.llm,
            rewriter=NoQueryRewriting(),
            limits=AnswerLimits(
                max_quote_characters=500,
                generation_timeout_seconds=30.0,
            ),
        )
        return RagService(retrieval=self.retrieval, answering=answering)


async def _index_documents(
    world: EvalWorld, documents: Sequence[CorpusDocument]
) -> dict[str, UUID]:
    """Index each corpus PDF's pages as READY chunks; return filename → document id."""
    by_filename: dict[str, UUID] = {}
    for document in documents:
        indexed = await world.index(
            list(document.pages),
            filename=document.filename,
            collection=world.collection.id,
        )
        by_filename[document.filename] = indexed.id
    return by_filename


def _oracle_terms(case: EvalCase, gold_texts: Sequence[str]) -> tuple[str, ...]:
    """Keep only required terms that appear in gold evidence (oracle cannot invent facts)."""
    blob = "\n".join(gold_texts).casefold()
    return tuple(
        term for term in case.expected_answer.must_include_terms if term.casefold() in blob
    )


def _script_answered(case: EvalCase, gold_indexes: Sequence[int], gold_texts: Sequence[str]) -> str:
    terms = " ".join(_oracle_terms(case, gold_texts))
    if not terms:
        terms = " ".join(case.expected_answer.must_include_terms) or case.question
    if not gold_indexes:
        return terms
    markers = "".join(f" [{index}]" for index in gold_indexes)
    return f"{terms}.{markers}"


def _prepare_llm(
    case: EvalCase,
    *,
    context_empty: bool,
    gold_indexes: Sequence[int],
    gold_texts: Sequence[str],
) -> tuple[list[str], str]:
    """Return (scripted responses, answer_source label)."""
    outcome = case.expected_answer.outcome
    if outcome is AnswerOutcome.BLOCKED:
        return [_LEAKED_POLICY_ANSWER], ANSWER_SOURCE_INDUCED_LEAK
    if outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE or not case.sufficient_evidence:
        if context_empty:
            # Pipeline short-circuits before the LLM; leave responses empty.
            return [], ANSWER_SOURCE_SHORT_CIRCUIT
        return [INSUFFICIENT_EVIDENCE_STATEMENT], ANSWER_SOURCE_INDUCED_REFUSAL
    return [_script_answered(case, gold_indexes, gold_texts)], ANSWER_SOURCE_ORACLE


async def run_case(case: EvalCase, *, thresholds: ScoreThresholds | None = None) -> CaseScores:
    documents = DOCUMENT_SETS[case.document_set]
    world = EvalWorld()
    await _index_documents(world, documents)
    rag = world.rag()
    query = RagQuery(owner_id=world.owner.id, question=case.question)

    retrieval = await rag.retrieve(query)
    gold_indexes = [
        item.index
        for item in retrieval.context.items
        if any(evidence_matches(target, item.evidence) for target in case.expected_citation_targets)
    ]
    gold_texts = [
        item.evidence.text
        for item in retrieval.context.items
        if any(
            evidence_matches(target, item.evidence)
            for target in (*case.expected_citation_targets, *case.expected_evidence)
        )
    ]

    world.llm.responses.clear()
    world.llm.failures.clear()
    responses, answer_source = _prepare_llm(
        case,
        context_empty=retrieval.context.is_empty,
        gold_indexes=gold_indexes,
        gold_texts=gold_texts,
    )
    world.llm.responses = list(responses)

    result = await rag.answer(query)
    return score_case(
        case,
        retrieved=retrieval.evidence,
        context=retrieval.context,
        result=result,
        thresholds=thresholds,
        answer_source=answer_source,
    )


async def run_suite(
    cases: Sequence[EvalCase] | None = None,
    *,
    thresholds: ScoreThresholds | None = None,
) -> SuiteReport:
    selected = tuple(cases) if cases is not None else CASES
    scores = [await run_case(case, thresholds=thresholds) for case in selected]
    return SuiteReport(
        cases=tuple(scores),
        passed=all(item.passed for item in scores),
        generated_at=datetime.now(UTC).isoformat(),
    )


def materialise_pdfs(target_dir: Path) -> list[Path]:
    """Write representative corpus PDFs for inspection (not required to score)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    seen: set[str] = set()
    for documents in DOCUMENT_SETS.values():
        for document in documents:
            if document.filename in seen:
                continue
            seen.add(document.filename)
            path = target_dir / document.filename
            path.write_bytes(document.pdf_bytes())
            written.append(path)
    return written


def write_report_json(report: SuiteReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def _format_mean(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _print_report(report: SuiteReport) -> None:
    for case in report.cases:
        status = "PASS" if case.passed else "FAIL"
        category = f" ({case.category})" if case.category else ""
        source = f" [{case.answer_source}]" if case.answer_source else ""
        sys.stdout.write(f"[{status}] {case.case_id}{category}{source}\n")
        for metric in case.metrics:
            if not metric.applicable:
                sys.stdout.write(f"  {metric.name.value}: n/a  {metric.detail}\n")
                continue
            sys.stdout.write(f"  {metric.name.value}: {metric.value:.3f}  {metric.detail}\n")
    sys.stdout.write("\nMean scores (applicable only):\n")
    for name, value in report.mean_scores.items():
        sys.stdout.write(f"  {name}: {_format_mean(value)}\n")
    if report.by_category:
        sys.stdout.write("\nBy category:\n")
        for category, counts in sorted(report.by_category.items()):
            sys.stdout.write(f"  {category}: {counts['passed']}/{counts['total']}\n")
    sys.stdout.write(
        f"\nSuite: {'PASS' if report.passed else 'FAIL'} "
        f"({sum(1 for c in report.cases if c.passed)}/{len(report.cases)} cases)\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DocuLens RAG evaluation suite (§62).")
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Limit to one or more case ids (repeatable).",
    )
    parser.add_argument(
        "--json",
        type=Path,
        nargs="?",
        const=DEFAULT_JSON_PATH,
        default=DEFAULT_JSON_PATH,
        help=f"Write machine-readable results (default: {DEFAULT_JSON_PATH}).",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip writing the machine-readable results file.",
    )
    parser.add_argument(
        "--materialise-pdfs",
        type=Path,
        metavar="DIR",
        help="Write representative corpus PDFs under DIR and exit.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.materialise_pdfs is not None:
        paths = materialise_pdfs(args.materialise_pdfs)
        for path in paths:
            sys.stdout.write(f"{path}\n")
        return 0

    selected = CASES
    if args.case_ids:
        by_id = {case.id: case for case in CASES}
        missing = [case_id for case_id in args.case_ids if case_id not in by_id]
        if missing:
            sys.stderr.write("Unknown case id(s): " + ", ".join(missing) + "\n")
            return 2
        selected = tuple(by_id[case_id] for case_id in args.case_ids)

    report = asyncio.run(run_suite(selected))
    _print_report(report)

    if not args.no_json:
        written = write_report_json(report, args.json)
        sys.stdout.write(f"\nWrote machine-readable results: {written}\n")

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
