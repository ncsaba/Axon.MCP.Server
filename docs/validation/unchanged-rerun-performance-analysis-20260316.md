# Unchanged Rerun Performance Analysis

**Date**: 2026-03-16
**Repository**: jverein (Java, medium size)
**Objective**: Validate streaming indexing unchanged rerun performance

## Executive Summary

The unchanged rerun correctly skips parsing and embedding via the metadata gate, but **CallGraphStep dominates runtime (80%)** due to lack of early-exit logic for unchanged files.

## Test Results

### Fresh Run (Job 3)

| Metric | Value |
|--------|-------|
| Duration | 345s (~6 min) |
| Files Discovered | 811 |
| Symbols Created | 8,610 |
| Chunks Created | 9,408 |
| Embeddings Generated | 9,408 |
| Status | COMPLETED |

### Unchanged Rerun (Job 4)

| Metric | Value |
|--------|-------|
| Duration | 37s |
| Files Processed | 811 |
| Metadata Gate Decisions | `unchanged=811` |
| Parse Tasks | 0 |
| Embeddings Generated | 0 |
| Status | COMPLETED |

## Timing Breakdown (Unchanged Rerun)

Extracted from worker logs at `/tmp/axon-index-validation-20260316T215740Z/`:

| Step | Duration | % of Total | Status |
|------|----------|------------|--------|
| CloneStep | instant | 0% | ✅ OK |
| DiscoveryStep | 107ms | 0.3% | ✅ OK |
| ParsingStep | 1ms | 0% | ✅ Fast (no parse tasks) |
| ApiExtractionStep | 944ms | 2.6% | ⚠️ Still runs |
| ReferenceBuildingStep | 251ms | 0.7% | ⚠️ Still runs |
| RelationshipBuildingStep | 146ms | 0.4% | ⚠️ Still runs |
| ImportResolutionStep | 5.8s | 15.7% | ⚠️ Still runs |
| **CallGraphStep** | **29.5s** | **79.7%** | 🔴 **BOTTLENECK** |
| DependencyExtractionStep | 37ms | 0.1% | ✅ OK |
| ConfigExtractionStep | 6ms | 0% | ✅ OK |
| PatternDetectionStep | instant | 0% | ✅ OK |
| CombinedExtractionStep | 396ms | 1.1% | ⚠️ Still runs |
| EmbeddingGenerationStep | 3ms | 0% | ✅ Fast (no new embeddings) |
| ServiceDetectionStep | 158ms | 0.4% | ✅ OK |
| ServiceDocumentationStep | 106ms | 0.3% | ✅ OK |

## Root Cause Analysis

### What's Working

1. **Metadata Gate**: Correctly identifies all 811 files as `unchanged`
2. **Parse Fanout**: No parse tasks enqueued (skipped correctly)
3. **Embedding Skip**: No new embeddings generated (reuse working)
4. **Job Metadata**: `files_processed=811` correctly reported

### What's Not Working

1. **CallGraphStep**: Rebuilds call graphs for all 805 files with 7,696 symbols every run
2. **ImportResolutionStep**: Still runs full import resolution (~5.8s)
3. **Other extraction steps**: Run even when no changes detected

### Why CallGraphStep is Slow

From logs:
```
call_graph_analysis_started: total_files=805 total_symbols=7696
call_graph_completed: relationships_created=20646
```

The step processes all symbols to build call relationships, even when no files changed. This is O(n) where n = total symbols, not O(changed symbols).

## Metrics Delta (Unchanged Rerun)

```
metadata_gate_files_total{decision="unchanged"} 811.0
streaming_stage_items_total{item_type="files",result="unchanged",stage="metadata_gate"} 811.0
streaming_stage_duration_seconds_sum{mode="streaming",stage="embedding"} 0.003s
streaming_stage_duration_seconds_sum{mode="streaming",stage="metadata_gate"} 0.037s
streaming_stage_duration_seconds_sum{mode="streaming",stage="parse_wait"} 0.0002s
```

## Recommendations

### Option A: Early-Exit for Unchanged Runs (Recommended)

Add early-exit logic to expensive steps when `changed_chunk_ids` is empty:

```python
# In CallGraphStep.execute()
changed_chunk_ids = ctx.metadata.get("changed_chunk_ids", [])
if not changed_chunk_ids:
    logger.info("call_graph_skipped_unchanged", repository_id=ctx.repository_id)
    return
```

**Impact**: Reduce unchanged rerun from 37s to ~2-3s

### Option B: Checkpoint-Based Skip

Use existing checkpoint system to skip completed steps:

```python
# Already implemented in sync_worker.py
if checkpoints.get(step.name) == "completed":
    logger.info(f"Skipping completed step: {step.name}")
    continue
```

**Limitation**: Requires job to have completed successfully. Doesn't help with partial reruns.

### Option C: Incremental Call Graph Updates

Only rebuild call graphs for changed files and their dependents:

1. Identify files that changed
2. Find symbols in those files
3. Find callers/callees of those symbols
4. Only rebuild affected subgraph

**Complexity**: High. Requires tracking symbol dependencies.

## Implementation Priority

| Step | Current Cost | Fix Complexity | Priority |
|------|--------------|----------------|----------|
| CallGraphStep | 29.5s (80%) | Low (early-exit) | 🔴 High |
| ImportResolutionStep | 5.8s (16%) | Low (early-exit) | 🟡 Medium |
| ApiExtractionStep | 944ms (3%) | Low | 🟢 Low |
| ReferenceBuildingStep | 251ms (1%) | Low | 🟢 Low |

## Next Steps

1. Implement early-exit in [`CallGraphStep`](../src/workers/pipeline/steps/call_graph_step.py)
2. Implement early-exit in [`ImportResolutionStep`](../src/workers/pipeline/steps/import_resolution_step.py)
3. Add `changed_chunk_ids` check to other extraction steps
4. Add streaming metrics to non-streaming steps for observability
5. Re-run validation to confirm improvement

## Files to Modify

- `axon-src/src/workers/pipeline/steps/call_graph_step.py`
- `axon-src/src/workers/pipeline/steps/import_resolution_step.py`
- `axon-src/src/workers/pipeline/steps/api_extraction_step.py`
- `axon-src/src/workers/pipeline/steps/reference_building_step.py`
- `axon-src/src/workers/pipeline/steps/relationship_building_step.py`

## Validation Commands

```bash
# Fresh run
python scripts/run_jverein_index_validation.py

# Unchanged rerun (run again immediately)
python scripts/run_jverein_index_validation.py

# Check logs
grep -E "step|Step|started|completed" /tmp/axon-index-validation-*/repository_sync_worker.log
```

## Related Documentation

- [Streaming Indexing Implementation Plan](../docs/architecture/streaming_indexing_implementation_plan.md)
- [Session Handover](../docs/SESSION_HANDOVER.md)
- [Parser Capability Matrix](../docs/architecture/parser_capability_matrix.md)
