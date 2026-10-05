# RAG evaluation methodology and limitations

This document explains what the DocuLens offline evaluation suite (§62, §63) **does and does not**
measure. It exists so a green suite is not mistaken for proof of production RAG quality.

## What the suite measures

| Concern           | How it is measured                                                                                                                                                | Honest scope                                                                                           |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Retrieval quality | Gold recall (`retrieval_relevance`), candidate precision (`retrieval_precision`), context precision/recall against content-based gold descriptors                 | Real hybrid retrieval over bag-of-words embeddings on a tiny curated corpus                            |
| Citation quality  | Quote↔context validity, gold-target recall, citation precision vs gold                                                                                            | Pipeline citation plumbing + whether the answer cites the right chunks (oracle-scripted indexes in CI) |
| Groundedness      | Outcome integrity; refusals without forbidden facts; **required answer terms must appear in cited evidence** (claim→citation attribution)                         | Structural groundedness, not NLI/factuality judging                                                    |
| Answer relevance  | Outcome match + required/forbidden term checks (not exact wording)                                                                                                | Shallow lexical relevance to the curated case, not semantic answer quality                             |
| Failure behavior  | Dedicated `failure_behavior` metric: insufficient / blocked outcomes, no citations, no fabricated appendices; empty-context cases exercise the real short-circuit | Refusal **contracts** and leak withholding; not whether a production LLM would refuse                  |

## Layered answer sources

Each case records `answer_source` in the JSON report:

| Source                   | Meaning                                                                                      |
| ------------------------ | -------------------------------------------------------------------------------------------- |
| `pipeline_short_circuit` | Empty assembled context → answering returns insufficient evidence **before** calling the LLM |
| `oracle_scripted`        | Answer text is built from gold terms + gold citation markers (deterministic CI)              |
| `induced_refusal`        | Residual non-empty context; harness scripts the fixed insufficient-evidence statement        |
| `induced_policy_leak`    | Harness scripts a policy-leaking completion so `detect_violation` can withhold it            |

Oracle scripting is intentional: it scores retrieval, citation wiring, attribution, and refusal
handling **without** pretending FakeLLM free-form prose is a production model. It is **not** a
license to tune the product until only this corpus passes.

## Misleading patterns this design avoids

1. **Scoring N/A as 1.0** — cases with no gold evidence no longer inflate retrieval/context means.
   Inapplicable metrics are `applicable: false` and omitted from means and pass/fail.
2. **Calling recall “relevance” alone** — `retrieval_precision` is reported separately so junk-heavy
   candidate sets are visible even when gold is present.
3. **Tautological groundedness** — “citation quote ∈ context” alone is mostly a pipeline identity
   (quotes are sliced from evidence). Attribution requires required terms ∈ **cited** evidence.
4. **Mean score inflation on failure cases** — empty-gold retrieval metrics are N/A, not perfect.
5. **`min_score=-1.0` in the harness** — that forced every neighbour into evidence and hid empty
   short-circuits; the harness now uses the product default `0.0`.

## Known limitations (read before trusting a PASS)

- **Corpus scale** — a handful of short synthetic PDFs; lexical overlap is easy. Ranking quality on
  noisy enterprise corpora is not measured.
- **Harness knobs** — the suite uses hybrid retrieval with bag-of-words embeddings,
  `min_score=0.0`, and a harness `candidate_limit=12` (product default `RETRIEVAL_CANDIDATES` is
  20). Do not treat harness knobs as the production profile.
- **Embeddings** — bag-of-words test embeddings, not the production embedding model. Semantic near-
  misses and multilingual behaviour are out of scope.
- **No free-form generation quality** — CI does not grade a real LLM’s answers. Fluency, partial
  answers, hedging, and multi-hop synthesis are unevaluated here.
- **Term matching ≠ understanding** — `must_include_terms` can pass a vacuous sentence that happens
  to contain the tokens.
- **Contradiction / ambiguity policy** — the suite expects refusal (insufficient evidence). A
  product choice to present both sides with citations would need different gold labels; today’s
  cases encode “do not confidently pick one.”
- **Induced refusals** — when context is non-empty, FakeLLM would often fabricate; CI scripts the
  refusal to test the **contract**, not the model’s judgment. Empty-corpus / empty-context cases
  are the ones that exercise the real short-circuit.
- **Prompt injection** — document-bait cases check that the oracle cites the real policy, not the
  bait; leak cases check withhold-on-violation. They do not prove a frontier model resists
  injection.
- **Context precision thresholds are deliberately loose** — hybrid retrieval with `max_chunks=5`
  often yields 1–2 gold hits among distractors. Gates emphasize **context recall** and citation
  attribution more than precision.
- **No cost/latency/quality trade-off** — not an online eval; no shadow traffic, no human labels.

## Extending without gaming

- Add cases that fail today’s system for real reasons; do not weaken thresholds to absorb
  regressions.
- Prefer new **content-based** gold descriptors over brittle chunk ids.
- Keep oracle answers constrained to terms present in gold evidence.
- Schedule real-provider evaluation separately (OQ-22); store those reports under `docs/eval/`
  with a different schema or `provider` field — do not overwrite this suite’s meaning.

## Running

```bash
uv run python -m evals
make eval
```

Machine-readable output: `docs/eval/latest.json` (schema version 2, gitignored).  
Committed snapshot of a green run: [`sample-results.md`](sample-results.md).
