"""RAG evaluation: curated-case models and deterministic metric scorers (§62)."""

from doculens.evaluation.cases import (
    EvalCase,
    EvalCategory,
    ExpectedAnswerCharacteristics,
    ExpectedEvidence,
)
from doculens.evaluation.metrics import (
    CaseScores,
    MetricName,
    MetricScore,
    ScoreThresholds,
    evidence_matches,
    score_case,
)

__all__ = [
    "CaseScores",
    "EvalCase",
    "EvalCategory",
    "ExpectedAnswerCharacteristics",
    "ExpectedEvidence",
    "MetricName",
    "MetricScore",
    "ScoreThresholds",
    "evidence_matches",
    "score_case",
]
