# Pipeline Stage Instrumentation Plan

**Date**: 2026-03-17
**Objective**: Add consistent Prometheus metrics to all pipeline stages for performance analysis
**Status**: ✅ COMPLETED

## Current State

### All Stages Now Instrumented (using `streaming_stage_*` metrics)

| Stage | File | Metrics Used | Status |
|-------|------|--------------|--------|
| `clone` | `clone_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `discovery` | `discovery_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `metadata_gate` | `inventory_worker.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total`, `streaming_stage_batch_size`, `streaming_stage_batches_total`, `streaming_stage_db_duration_seconds`, `streaming_stage_lag_seconds` | ✅ |
| `parsing` | `parsing_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total`, `streaming_stage_batch_size` | ✅ |
| `parse_wait` | `parsing_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total`, `streaming_stage_batch_size`, `streaming_stage_queue_depth`, `streaming_stage_lag_seconds` | ✅ |
| `api_extraction` | `api_extraction_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `reference_building` | `reference_building_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `relationship_building` | `relationship_building_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `import_resolution` | `import_resolution_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `call_graph` | `call_graph_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `dependency_extraction` | `dependency_extraction_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `config_extraction` | `config_extraction_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `pattern_detection` | `pattern_detection_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `combined_extraction` | `combined_extraction_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `embedding` | `embedding_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total`, `streaming_stage_batch_size`, `streaming_stage_queue_depth`, `streaming_stage_db_duration_seconds` | ✅ |
| `service_detection` | `service_detection_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |
| `service_documentation` | `service_documentation_step.py` | `streaming_stage_duration_seconds`, `streaming_stage_items_total` | ✅ |

## Metrics Schema

All stages should emit the following metrics:

```python
# Required for all stages
streaming_stage_duration_seconds.labels(stage="<stage>", mode="batch").observe(duration)
streaming_stage_items_total.labels(stage="<stage>", item_type="<type>", result="<result>").inc(count)

# Optional, for batch-processing stages
streaming_stage_batch_size.labels(stage="<stage>").observe(batch_size)

# Optional, for stages with DB operations
streaming_stage_db_duration_seconds.labels(stage="<stage>", operation="<op>").observe(duration)

# Optional, for stages with queue/async work
streaming_stage_queue_depth.labels(stage="<stage>").set(depth)
```

### Stage Name Mapping

| Stage Name | Item Type | Result Values |
|------------|-----------|---------------|
| `api_extraction` | `endpoints` | `created`, `skipped` |
| `reference_building` | `references` | `created`, `skipped` |
| `relationship_building` | `relationships` | `created`, `skipped` |
| `import_resolution` | `imports` | `created`, `skipped` |
| `call_graph` | `call_relationships` | `created`, `skipped` |
| `dependency_extraction` | `dependencies` | `found`, `skipped` |
| `config_extraction` | `configs` | `found`, `skipped` |
| `pattern_detection` | `patterns` | `detected`, `skipped` |
| `combined_extraction` | `outgoing_calls`, `events_published`, `events_subscribed` | `created`, `skipped` |
| `service_detection` | `services` | `detected`, `skipped` |
| `service_documentation` | `services` | `documented`, `skipped` |

## Implementation Plan

### Phase 1: Add metrics import and instrumentation to each step

For each uninstrumented step, add:

1. Import metrics from `src.utils.metrics`
2. Add `start_time = time.perf_counter()` at start
3. Add `streaming_stage_duration_seconds` at end
4. Add `streaming_stage_items_total` for items processed
5. Add `streaming_stage_items_total` with `result=skipped` when early-exit

### Phase 2: Add early-exit detection

For stages that can skip work on unchanged reruns:

```python
# Check if work is needed
changed_chunk_ids = ctx.metadata.get("changed_chunk_ids", [])
if not changed_chunk_ids:
    streaming_stage_items_total.labels(
        stage="<stage>",
        item_type="<type>",
        result="skipped"
    ).inc(total_items)
    streaming_stage_duration_seconds.labels(
        stage="<stage>",
        mode="batch"
    ).observe(time.perf_counter() - start_time)
    return
```

### Phase 3: Add DB timing for DB-heavy stages

For stages with significant DB operations:

```python
db_start = time.perf_counter()
# ... DB operation ...
streaming_stage_db_duration_seconds.labels(
    stage="<stage>",
    operation="<operation>"
).observe(time.perf_counter() - db_start)
```

## Files to Modify

| File | Changes |
|------|---------|
| `api_extraction_step.py` | Add metrics import, duration/items counters |
| `reference_building_step.py` | Add metrics import, duration/items counters |
| `relationship_building_step.py` | Add metrics import, duration/items counters |
| `import_resolution_step.py` | Add metrics import, duration/items counters, early-exit |
| `call_graph_step.py` | Add metrics import, duration/items counters, early-exit |
| `dependency_extraction_step.py` | Add metrics import, duration/items counters |
| `config_extraction_step.py` | Add metrics import, duration/items counters |
| `pattern_detection_step.py` | Add metrics import, duration/items counters |
| `combined_extraction_step.py` | Add metrics import, duration/items counters |
| `service_detection_step.py` | Add metrics import, duration/items counters |
| `service_documentation_step.py` | Add metrics import, duration/items counters |

## Validation

After implementation:

1. Run fresh sync: `python scripts/run_jverein_index_validation.py`
2. Run unchanged rerun immediately after
3. Check `/metrics` endpoint for all stage metrics
4. Verify timing breakdown shows all stages

## Expected Outcome

After instrumentation, the validation harness should produce consistent timing data for all stages:

```
streaming_stage_duration_seconds_sum{stage="discovery",mode="streaming"} 0.107
streaming_stage_duration_seconds_sum{stage="metadata_gate",mode="streaming"} 0.037
streaming_stage_duration_seconds_sum{stage="parsing",mode="streaming"} 0.001
streaming_stage_duration_seconds_sum{stage="api_extraction",mode="batch"} 0.944
streaming_stage_duration_seconds_sum{stage="reference_building",mode="batch"} 0.251
streaming_stage_duration_seconds_sum{stage="relationship_building",mode="batch"} 0.146
streaming_stage_duration_seconds_sum{stage="import_resolution",mode="batch"} 5.8
streaming_stage_duration_seconds_sum{stage="call_graph",mode="batch"} 29.5
streaming_stage_duration_seconds_sum{stage="dependency_extraction",mode="batch"} 0.037
streaming_stage_duration_seconds_sum{stage="config_extraction",mode="batch"} 0.006
streaming_stage_duration_seconds_sum{stage="pattern_detection",mode="batch"} 0.0
streaming_stage_duration_seconds_sum{stage="combined_extraction",mode="batch"} 0.396
streaming_stage_duration_seconds_sum{stage="embedding",mode="streaming"} 0.003
streaming_stage_duration_seconds_sum{stage="service_detection",mode="batch"} 0.158
streaming_stage_duration_seconds_sum{stage="service_documentation",mode="batch"} 0.106
```

## Related Documentation

- [Unchanged Rerun Performance Analysis](../validation/unchanged-rerun-performance-analysis-20260316.md)
- [Streaming Indexing Implementation Plan](./streaming_indexing_implementation_plan.md)
- [Observability Findings](./observability_findings_and_recommendations.md)
