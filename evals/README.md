# RAG evaluation (SPECIFICATIONS.md §62, §63)

Deterministic quality measurement for retrieval-augmented generation, separate from ordinary
unit/API tests. Read [`docs/eval/methodology.md`](../docs/eval/methodology.md) before treating a
green run as production RAG proof.

## What it measures

| Metric                 | Meaning                                                                  |
| ---------------------- | ------------------------------------------------------------------------ |
| `retrieval_relevance`  | Gold recall in the retrieved candidate set (N/A if no gold)              |
| `retrieval_precision`  | Share of retrieved candidates that match gold (N/A if no gold)           |
| `context_precision`    | Share of assembled context items that match gold (N/A if no gold)        |
| `context_recall`       | Share of gold descriptors covered by assembled context (N/A if no gold)  |
| `citation_correctness` | Quote validity + gold-target recall + citation precision vs gold         |
| `answer_relevance`     | Expected outcome plus required/forbidden terms (not exact prose)         |
| `groundedness`         | Refusal integrity, or answered claims attributable to **cited** evidence |
| `failure_behavior`     | Dedicated refusal/withhold score (N/A for answered cases)                |

Inapplicable metrics are omitted from pass/fail and from suite means (they are **not** scored as
1.0). Exact LLM wording is never required. Unsupported answers must refuse or be withheld rather
than confidently fabricate.

## Corpus categories (§63 + adversarial)

| Category                | Intent                                                                    |
| ----------------------- | ------------------------------------------------------------------------- |
| `answer_present`        | Fact available in a single place                                          |
| `answer_multi_page`     | Answer distributed across pages                                           |
| `answer_multi_document` | Answer distributed across documents                                       |
| `ambiguous_question`    | Question underspecified — refuse a confident pick                         |
| `unavailable_answer`    | No supporting evidence — refuse (includes empty corpus short-circuit)     |
| `contradictory_sources` | Sources disagree — do not invent a single truth                           |
| `prompt_injection`      | Document/question injection bait; grounded answer, refuse, or block leaks |
| `irrelevant_documents`  | Noise / wrong corpus must not produce unsupported answers                 |

Cases and representative documents live in [`corpus.py`](corpus.py). Materialise the PDFs for
human inspection:

```bash
uv run python -m evals --materialise-pdfs evals/corpus/pdfs
```

## Run locally

```bash
uv run python -m evals
uv run python -m evals --case annual-leave-direct
uv run python -m evals --json docs/eval/latest.json
uv run python -m evals --no-json
make eval
```

Exit code `0` means every case met thresholds on its **applicable** metrics.

## Machine-readable results

Default path: `docs/eval/latest.json` (schema version 2). Includes `methodology`, per-metric
`applicable` flags, `answer_source`, category rollups, and means over applicable scores only.
Generated JSON is gitignored; CI uploads the artifact.

## CI

The Python job runs `uv run python -m evals` after pytest and uploads `docs/eval/latest.json`.
Real-provider evaluation remains optional / scheduled (OQ-22).

## Extending

1. Add documents/cases in `corpus.py` with a `category` and content-based gold descriptors.
2. Keep oracle answers constrained to terms present in gold evidence.
3. Do not lower thresholds to absorb product regressions — see methodology.md.
