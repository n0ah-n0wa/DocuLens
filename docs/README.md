# DocuLens documentation

Technical documentation for reviewers and operators. Capabilities described here match the
repository unless marked open (`OQ-n`) or residual.

| Document                                                     | Topic                       |
| ------------------------------------------------------------ | --------------------------- |
| [`architecture.md`](architecture.md)                         | System design and status    |
| [`api.md`](api.md)                                           | HTTP API / OpenAPI          |
| [`database.md`](database.md)                                 | PostgreSQL schema           |
| [`rag.md`](rag.md)                                           | Ingestion → answer pipeline |
| [`security.md`](security.md)                                 | Security controls           |
| [`deployment.md`](deployment.md)                             | AWS CD, OIDC, rollback      |
| [`operations.md`](operations.md)                             | Troubleshooting / runbook   |
| [`development.md`](development.md)                           | Local setup                 |
| [`specification-compliance.md`](specification-compliance.md) | §-by-§ compliance audit     |
| [`eval/methodology.md`](eval/methodology.md)                 | Offline RAG eval honesty    |
| [`eval/sample-results.md`](eval/sample-results.md)           | Sample green eval snapshot  |
| [`decisions/`](decisions/README.md)                          | ADRs                        |
| [`planning/open-questions.md`](planning/open-questions.md)   | Living ambiguity register   |

Root entry points: [`../README.md`](../README.md), [`../SPECIFICATIONS.md`](../SPECIFICATIONS.md),
[`../evals/README.md`](../evals/README.md). Local API demo:
`uv run python scripts/demo_api_happy_path.py`.
