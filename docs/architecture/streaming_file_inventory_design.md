# Streaming File Inventory Design (Slice 2)

## Objective

Implement a streaming file-inventory stage that emits file metadata batches continuously, without materializing full repository file lists in memory.

This stage feeds metadata-gate workers and is optimized for large trees.

## Design Summary

1. Introduce a `FileInventoryProvider` abstraction.
2. Discovery producer consumes provider output and emits queue batches.
3. Use OS-native backends where possible for fastest metadata enumeration.
4. Keep a portable fallback backend for parity and local development.

## Provider Contract

`FileInventoryProvider` yields `FileMeta` records as a stream:

| Field | Type | Notes |
| --- | --- | --- |
| `rel_path` | `str` | Path relative to repository root |
| `size_bytes` | `int` | File byte size |
| `mtime_ns` | `int` | Last-modified timestamp in nanoseconds (UTC epoch) |
| `kind` | `str` | `file`, `symlink`, `other` |

Provider API shape:

1. `stream(root_path, include_rules, exclude_rules) -> iterator[FileMeta]`
2. Must support incremental yielding (no full-list buffering).
3. Must provide deterministic ordering per backend (lexical within directory is sufficient).

## Batch Message Schema (Discovery -> Metadata Gate)

| Field | Type | Purpose |
| --- | --- | --- |
| `repository_id` | `int` | Repository scope |
| `run_id` | `str` | Discovery run identity |
| `batch_seq` | `int` | Monotonic sequence in run |
| `observed_at` | `datetime` | Producer wall-clock timestamp |
| `files` | `list[FileMeta]` | File metadata payload |

Idempotency key:
- `run_id + batch_seq`

## OS Backend Strategy

### Linux backend (priority 1)

- Native walker (Rust or C bridge).
- Use `openat`/`readdir` family for traversal.
- Use `statx` for metadata extraction.
- Multi-directory worker threads with bounded queue.

### macOS backend

- Use `getattrlistbulk` for bulk directory attribute retrieval where available.
- Fallback to `readdir + fstatat` if necessary.

### Windows backend

- Use `FindFirstFileEx` / `FindNextFile` enumeration.
- Use metadata from listing structures directly (size/mtime).

### Portable fallback backend

- Python `os.scandir` streaming walker.
- Used for initial integration and cross-platform fallback.

## Transport Option

For native backends:

1. Native process emits NDJSON or MessagePack lines on stdout.
2. Python producer reads stream and emits queue batches immediately.
3. Backpressure control in producer by queue depth/emit latency.

## Performance Guardrails

1. Constant-memory traversal (bounded batch + bounded in-flight queue).
2. No per-file DB access in discovery stage.
3. Bounded queue backpressure (pause producer when lag threshold exceeded).
4. Emit metrics:
   - `files_enumerated_total`
   - `directories_enumerated_total`
   - `inventory_batches_emitted_total`
   - `inventory_emit_latency_ms`
   - `inventory_queue_lag`

## Failure and Retry Semantics

1. Producer retries are idempotent via `run_id + batch_seq`.
2. Metadata gate must safely deduplicate repeated batches by idempotency key.
3. Partial backend failures are logged with path context and continue where safe.

## Rollout Plan

1. Implement provider interface + queue schema.
2. Implement discovery producer + portable `scandir` provider first.
3. Integrate metadata-gate consumption path with same schema.
4. Add Linux native backend and benchmark.
5. Add macOS and Windows backends (testing environments available).
6. Keep fallback backend always available behind provider selection.

## Benchmark Acceptance (Slice 2)

1. Large repository inventory does not exceed bounded memory target.
2. Producer emits incremental batches within seconds of start.
3. Linux native backend outperforms fallback backend on file/sec.
4. Cross-platform correctness parity validated on Windows/macOS test environments.
