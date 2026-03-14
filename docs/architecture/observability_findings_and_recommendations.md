# Observability Findings And Recommendations

## Goal

Document current findings and near-term recommendations for:

1. application and worker logging
2. indexer/runtime metrics
3. AI / LLM / embedding observability

This is a planning/reference artifact only. It does not imply immediate implementation.

## Scope

`✅` present, `🚧` partial, `🧭` recommended next step.

| Area | Current State | Notes |
| --- | --- | --- |
| Structured application logging | `✅` | `structlog` is configured for API/runtime logs. |
| Worker/process log capture | `✅` | Validation harness captures API and worker logs to per-run files. |
| Repository-scoped progress logs | `✅` | Redis stream publisher exists for repository log history. |
| Streaming/indexer Prometheus metrics | `✅` | Discovery, metadata gate, parse wait, and embedding metrics are emitted. |
| Prometheus multiprocess aggregation | `🚧` | Implemented in validation harness context; not yet documented as a required dev-container dependency. |
| Job progress/accounting consistency | `🚧` | `job_metadata` counters do not yet reflect real pipeline work reliably. |
| ELK-compatible logging strategy | `🚧` | Logs are structured enough to ship, but deployment guidance is missing. |
| Phoenix / AI tracing | `🧭` | Optional for future search/enrichment tracing; current AI footprint is too small to justify priority investment. |

## Current Findings

### 1. Logging

Current logging coverage is functional:

1. API and application startup/shutdown logs are structured.
2. Worker execution logs are available in local process logs.
3. Repository progress logs are published via Redis streams.
4. Large indexing runs are diagnosable from log files.

Current weaknesses:

1. Log volume is high in some worker paths, especially debug-heavy graph/call resolution flows.
2. Logging style is not fully uniform across all modules.
3. There is no documented deployment posture for environments with and without centralized log aggregation.
4. Job summary counters are less trustworthy than raw logs and final persisted stats.

### 2. Metrics

Prometheus-style metrics are now available for the streaming indexer path:

1. discovery batch emission
2. metadata gate decisions and DB timings
3. parse-wait lag and queue depth
4. embedding stage batch sizing and skip behavior

Validation findings from the `domeus-core` run:

1. Metrics are useful for unchanged-rerun verification.
2. Worker metrics required Prometheus multiprocess collection to appear correctly at the API `/metrics` endpoint.
3. `metrics_delta` now contains actionable batch and timing data.

Current weaknesses:

1. Metrics exist, but Prometheus itself is not yet documented as a local runtime dependency.
2. `job_metadata` and metrics are not aligned; pipeline completion can be correct while job summary counters remain zero.
3. Metric output currently favors validation and engineering analysis, not yet dashboards/alerts.

### 3. AI / LLM / Embedding Observability

Current AI/LLM observability needs are limited:

1. embedding generation is currently the main AI-adjacent runtime path
2. embedding work volume and skip behavior can already be covered well with Prometheus
3. there is not yet enough LLM-heavy workflow surface area to justify broad Phoenix instrumentation

Current conclusion:

1. Phoenix is optional and environment-dependent right now.
2. Embedding-specific observability should stay lightweight unless richer AI workflows are introduced.

## Deployment Reality

Observed deployment assumptions are mixed:

1. Some target environments already have working ELK installations.
2. Some target environments do not have centralized logging infrastructure.
3. Some environments may also have Arize Phoenix available.
4. Prometheus is not yet consistently present in local/dev workflow, but should be.

This means observability must support both:

1. `with ELK`
2. `without ELK`

and should not assume Phoenix availability.

## Recommendations

### Logging

| Recommendation | Priority | Why |
| --- | --- | --- |
| Keep stdout/file structured logs as the canonical baseline | `🧭` | Works in all environments, including without ELK. |
| Treat ELK shipping as optional integration, not a hard runtime dependency | `🧭` | Deployment environments differ. |
| Standardize on JSON logs for deployable environments | `🧭` | Easier ingestion into ELK and other log pipelines. |
| Reduce high-volume debug logging in hot worker paths | `🧭` | Current large runs produce too much low-signal output. |
| Preserve repository-scoped Redis log stream only as progress UX, not primary forensic storage | `🧭` | Useful for UI/runtime progress, not sufficient as the only log system. |

Recommended operating model:

1. Baseline mode:
   - structured stdout/file logs
   - local worker/API log files
   - Redis repository progress stream
2. ELK-enabled mode:
   - same baseline logs
   - JSON log shipping into ELK
   - no separate code path required beyond log formatting and deployment wiring

### Metrics

| Recommendation | Priority | Why |
| --- | --- | --- |
| Document Prometheus as an explicit dependency for local/dev observability | `🧭` | Metrics are now useful enough to justify first-class setup. |
| Add Prometheus startup/install to the dev-container in a follow-up increment | `🧭` | Required for practical scraping and dashboarding. |
| Keep current metrics low-cardinality and stage-oriented | `🧭` | This is the right tradeoff for indexer operational telemetry. |
| Fix `job_metadata` progress/accounting so it matches real persisted work | `🧭` | Current zeroed counters are misleading. |
| Add dashboards/queries for unchanged-rerun validation and queue-stage timing | `🧭` | The underlying metrics are now available. |

Important Prometheus note:

1. The API `/metrics` endpoint alone is not the full solution.
2. Worker metrics require Prometheus multiprocess collection when exposed through the API process.
3. That dependency and runtime contract must be documented before Prometheus is treated as operationally supported.

### AI / Phoenix

| Recommendation | Priority | Why |
| --- | --- | --- |
| Do not prioritize broad Phoenix instrumentation yet | `🧭` | Current AI/LLM surface area is still small. |
| Cover embedding runtime behavior with Prometheus for now | `🧭` | Sufficient for throughput/skip monitoring. |
| Revisit Phoenix once LLM search/enrichment/tracing is materially expanded | `🧭` | Phoenix is more valuable for retrieval and LLM workflow inspection than current embedding-only flows. |

Good future Phoenix triggers would be:

1. retrieval-augmented search quality evaluation
2. prompt/response tracing for AI enrichment
3. span-level debugging of multi-step LLM workflows

## Suggested Follow-Up Work

### Slice A: Logging Posture

1. Define recommended log format by environment:
   - local/dev
   - deployed without ELK
   - deployed with ELK
2. Reduce noisy debug logs in hot indexing paths.
3. Document how to ship JSON logs into ELK where available.

### Slice B: Prometheus Runtime

1. Add Prometheus to dev-container/runtime setup.
2. Document scrape configuration for API `/metrics`.
3. Document multiprocess metric collection requirements.
4. Add a minimal dashboard/query cookbook for indexing runs.

### Slice C: Progress Accuracy

1. Fix `jobs.job_metadata` progress counters.
2. Reconcile repository detail/stats semantics where they currently diverge.
3. Ensure run summaries and logs tell the same story.

### Slice D: AI Observability Revisit

1. Re-evaluate Phoenix only after additional LLM-heavy features exist.
2. Keep embedding observability in Prometheus unless a stronger Phoenix use case appears.

## Recommended Current Position

1. Logging: good enough to operate and debug, not yet fully standardized for all deployment modes.
2. Metrics: useful and now validated, but Prometheus dependency/setup must be made explicit.
3. AI/Phoenix: defer major investment for now.

## Explicit Decisions

1. Logging must work both with and without ELK.
2. Prometheus should be documented and then added to the dev-container in a follow-up step.
3. Phoenix is optional for some environments, but not a current priority.
4. Embedding observability is currently better handled with Prometheus than with dedicated AI tracing.
