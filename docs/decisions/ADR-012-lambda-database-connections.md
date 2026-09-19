# ADR-012 — Database connection management for Lambda

**Status:** Proposed (blocks `OQ-28`; not binding until accepted)
**Date:** 2026-09-19
**Specification references:** §5.4, §45, §65, §66

## Context

The API and, depending on `OQ-2`, the worker run on AWS Lambda. Every concurrent Lambda instance
holds its own database connections, and instances are created and frozen outside the
application's control. Under load, concurrency multiplied by per-instance pool size can exceed the
connection limit of an RDS PostgreSQL instance, turning a traffic spike into database connection
failures, which §66 requires the system to tolerate. §5.4 lists RDS or Aurora but no connection
management component, so a choice is needed before the first repository adapter is deployed.

## Options

### A. RDS Proxy in front of RDS / Aurora

Lambda connects to the proxy, which multiplexes connections to the database and handles
credential retrieval from Secrets Manager.

- Standard AWS answer to this problem; absorbs spikes; IAM authentication possible.
- Adds a billed component and one more hop of latency; not in the §5.4 list, so §5.4 must be
  amended.

### B. Small per-instance pools plus reserved concurrency

Each Lambda instance opens at most one or two connections; API and worker functions get reserved
concurrency limits sized so that the total stays below the database limit.

- No new component.
- Caps throughput by design; the concurrency limit becomes a capacity ceiling shared with the
  §65 latency budget; misconfiguration fails loudly under load.

### C. Aurora Serverless v2 with the Data API

Replaces socket connections with HTTP calls to the Data API.

- No connection management at all.
- Changes the persistence adapter significantly (no SQLAlchemy async driver over Data API in the
  standard tool-chain), which conflicts with the §5.1 requirement to use SQLAlchemy 2.x and Alembic.

## Recommendation

Option A for `production` and `staging`, with option B's per-instance pool discipline applied in
all environments regardless (small pools, short idle timeouts) so that local and Lambda behaviour
match. Option C is ruled out by the §5.1 tool-chain requirement.

## Consequences (if accepted)

- §5.4 gains RDS Proxy; the `database` Terraform module provisions it and the API/worker roles get
  connect permissions only for the proxy.
- The SQLAlchemy engine is configured with a small pool and connection recycling suited to Lambda.
- Alembic migrations (`OQ-23`) connect directly to the database, not through the proxy, because
  DDL through a multiplexing proxy is not supported in all modes.
- `OQ-28` is closed and `docs/architecture.md` §9 and §12 move the item from _Pending_ to _Planned_.
