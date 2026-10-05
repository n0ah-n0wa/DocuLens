# Sample RAG evaluation results

Deterministic offline suite (`uv run python -m evals`) with **fake** embedding/LLM providers.
This is a committed snapshot of a green local run so reviewers can see expected shape without
re-running. Regenerate anytime; do not treat these scores as production-model quality.

**Methodology:** [`methodology.md`](methodology.md) · **Harness:** [`evals/README.md`](../../evals/README.md)

## Suite outcome

| Field     | Value                                      |
| --------- | ------------------------------------------ |
| Result    | **PASS** (16/16 cases)                     |
| Providers | Bag-of-words embeddings + oracle / induced |
| Schema    | `docs/eval/latest.json` v2 (CI artefact)   |
| Captured  | 2026-10-03                                 |

## Mean scores (applicable metrics only)

| Metric                 | Mean  |
| ---------------------- | ----- |
| `retrieval_relevance`  | 1.000 |
| `retrieval_precision`  | 0.346 |
| `context_precision`    | 0.350 |
| `context_recall`       | 1.000 |
| `citation_correctness` | 1.000 |
| `answer_relevance`     | 1.000 |
| `groundedness`         | 1.000 |
| `failure_behavior`     | 1.000 |

Context precision is intentionally loose (hybrid retrieval often returns gold among distractors).
Gates emphasize recall, citation attribution, and refusal contracts — see methodology.

## Pass rate by category

| Category                | Pass |
| ----------------------- | ---- |
| `answer_present`        | 4/4  |
| `answer_multi_page`     | 1/1  |
| `answer_multi_document` | 1/1  |
| `ambiguous_question`    | 1/1  |
| `unavailable_answer`    | 3/3  |
| `contradictory_sources` | 1/1  |
| `irrelevant_documents`  | 2/2  |
| `prompt_injection`      | 3/3  |

## How to reproduce

```bash
uv run python -m evals
# or: make eval
```

Exit code `0` means every applicable metric met its threshold.
