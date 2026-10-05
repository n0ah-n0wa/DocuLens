# ADR-009 — RAG evaluation strategy

**Status:** Accepted
**Date:** 2026-10-02
**Specification references:** §61–§64, §82, §88 (AI checklist)
**Related:** OQ-22; ADR-016, ADR-017, ADR-018

## Context

The specification requires a curated evaluation set with retrieval, groundedness, and citation
metrics, plus hallucination and adversarial coverage, without treating a green unit suite as proof
of production RAG quality.

## Decision

- **Harness:** `evals/` package with curated PDF corpus and cases; run via
  `uv run python -m evals` (also `make eval`).
- **CI:** Every CI backend job runs the harness with **FakeLLM / FakeEmbedding** providers so the
  gate is deterministic and free of external spend (OQ-22: real-provider runs are not a PR merge
  gate).
- **Metrics:** Retrieval hit/rank, groundedness/insufficient-evidence behaviour, citation presence
  and evidence alignment — documented in [`docs/eval/methodology.md`](../eval/methodology.md).
- **Adversarial / injection:** Separate unit suite
  `packages/core/tests/unit/test_prompt_injection.py` plus eval categories; not a substitute for
  red-team against a production model.
- **Honesty:** Methodology documents oracle limits and that FakeLLM scores are regression signals,
  not production quality certificates.

## Alternatives considered

| Alternative                   | Why not                                                  |
| ----------------------------- | -------------------------------------------------------- |
| Real LLM required on every PR | Cost, flakiness, secret coupling; rejected per OQ-22     |
| Eval only manual              | Spec requires automated evaluation in the quality system |

## Consequences

- Closing OQ-22 may add a scheduled real-provider job without weakening the PR FakeLLM gate.
- Portfolio claims must distinguish “eval harness green” from “production RAG proven on live models”.
