# Phase 2.5.0: Baseline Test Data Preparation - Execution Plan

**Status**: `🧭` Pending Approval  
**Created**: 2026-03-23  
**Parent Plan**: [`axon-src/docs/architecture/multi_branch_tracking_implementation_plan.md`](../architecture/multi_branch_tracking_implementation_plan.md)

## Objective

Create a reference data set for migration testing BEFORE any schema changes. This baseline will be used to:
1. Validate migration correctness (data comparison before/after)
2. Benchmark query performance (timing comparison before/after)
3. Enable repeatable testing via database snapshot restore

## Scope Constraints

| Constraint | Enforcement |
|------------|-------------|
| No schema/model changes | Use current `main` code only |
| No long-running monitoring | Single-shot commands with user-visible output |
| Minimal risk | All commands are read-only or idempotent resets |
| Remote repository indexing | Configure via provider URLs, not local paths |

---

## 1. Repository Selection

### Selected Repositories

| ID | Repository | Provider | URL | Expected Size | Rationale |
|----|------------|----------|-----|---------------|-----------|
| `R1` | axon-src | GIT | `https://github.com/ExampleOrg/axon-mcp.git` | Medium (100-1000 files) | Primary codebase, Python-heavy |
| `R2` | jverein | GIT | `https://github.com/openjverein/jverein.git` | Large (> 1000 files) | Java codebase, stress testing |
| `R3` | docs | GIT | Small workspace docs repo if available | Small (< 100 files) | Quick iteration testing |

> **NOTE**: Replace `ExampleOrg/axon-mcp` with the actual remote URL for the axon-mcp repository.

### Size Classification Criteria

| Size | File Count | Symbol Count | Use Case |
|------|------------|--------------|----------|
| Small | < 100 | < 1,000 | Quick migration iteration |
| Medium | 100-1,000 | 1,000-10,000 | Typical production repo |
| Large | > 1,000 | > 10,000 | Stress testing, performance |

---

## 2. Pre-Flight Checklist

Before executing any commands, verify:

### 2.1 Environment Variables

```bash
# Verify devcontainer defaults are in effect
echo $DATABASE_URL
echo $TEST_DATABASE_URL
echo $REDIS_URL
echo $CELERY_BROKER_URL
```

**Expected Output**:
```
postgresql+asyncpg://indexer:indexer@localhost:5432/indexer
postgresql+asyncpg://indexer:indexer@localhost:5432/indexer_test
redis://localhost:6379/0
redis://localhost:6379/0
```

### 2.2 Service Health

```bash
# PostgreSQL
psql -h localhost -U indexer -d indexer -c "SELECT 1"

# Redis
redis-cli ping
```

### 2.3 Python Environment

```bash
# Activate virtual environment
source /home/vscode/.venv-dev/bin/activate

# Verify working directory
cd /workspaces/axon-mcp/axon-src
pwd
```

---

## 3. Execution Sequence

### Step 1: Reset Baseline Database

**Purpose**: Start with a clean schema using current code.

```bash
cd /workspaces/axon-mcp/axon-src
source /home/vscode/.venv-dev/bin/activate

# Reset the test database (clean schema, no data)
make db-reset-test
```

**Expected Output**: Schema creation logs, no errors.

**Duration**: ~5 seconds

---

### Step 2: Start API Server

**Purpose**: Provide REST endpoints for repository registration.

```bash
# Terminal 1: API Server
cd /workspaces/axon-mcp/axon-src
source /home/vscode/.venv-dev/bin/activate

# Use test database for baseline
export DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer_test'

# Disable auth for baseline run
export AUTH_ENABLED=false

make api-dev
```

**Verification**: `curl http://localhost:8080/health` returns `{"status": "ok"}`

---

### Step 3: Start Celery Worker

**Purpose**: Process repository sync tasks.

```bash
# Terminal 2: Celery Worker
cd /workspaces/axon-mcp/axon-src
source /home/vscode/.venv-dev/bin/activate

# Use test database for baseline
export DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer_test'
export TEST_DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer_test'

python scripts/start_celery_worker.py \
  --queue default,repository_sync,file_parsing,embeddings,discovery_inventory \
  --concurrency 4 \
  --loglevel info
```

**Verification**: Log shows `ready` and queue names.

---

### Step 4: Register Repositories

**Purpose**: Create repository records and trigger initial sync.

```bash
# Terminal 3: Repository Registration
cd /workspaces/axon-mcp/axon-src
source /home/vscode/.venv-dev/bin/activate

# Register R1: axon-src (replace URL with actual remote)
curl -X POST http://localhost:8080/api/v1/repositories \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "GIT",
    "url": "https://github.com/ExampleOrg/axon-mcp.git",
    "path_with_namespace": "ExampleOrg/axon-mcp",
    "default_branch": "main"
  }'

# Register R2: jverein
curl -X POST http://localhost:8080/api/v1/repositories \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "GIT",
    "url": "https://github.com/openjverein/jverein.git",
    "path_with_namespace": "openjverein/jverein",
    "default_branch": "master"
  }'
```

**Expected Response**: JSON with `id`, `url`, `status: "pending"` or `"syncing"`

**Note**: If a small third repository is desired, add another registration following the same pattern.

---

### Step 5: Monitor Sync Progress

**Purpose**: Wait for indexing to complete.

```bash
# Poll repository status until all show completed or failed
watch -n 10 'curl -s http://localhost:8080/api/v1/repositories | jq ".[] | {id, path_with_namespace, status}"'
```

**Alternative**: Check Celery worker logs for task completion messages.

**Expected Duration**:
- Small repo: 1-5 minutes
- Medium repo: 5-15 minutes
- Large repo: 15-60 minutes

---

### Step 6: Capture Baseline Metrics

**Purpose**: Document current state for comparison.

#### 6.1 Row Counts

```bash
psql -h localhost -U indexer -d indexer_test -c "
SELECT
  'repositories' AS table_name, COUNT(*) AS row_count FROM repositories
UNION ALL SELECT 'commits', COUNT(*) FROM commits
UNION ALL SELECT 'file_instances', COUNT(*) FROM file_instances
UNION ALL SELECT 'file_contents', COUNT(*) FROM file_contents
UNION ALL SELECT 'symbols', COUNT(*) FROM symbols
UNION ALL SELECT 'relations', COUNT(*) FROM relations
UNION ALL SELECT 'chunks', COUNT(*) FROM chunks
UNION ALL SELECT 'embeddings', COUNT(*) FROM embeddings
UNION ALL SELECT 'chunk_symbol_links', COUNT(*) FROM chunk_symbol_links
ORDER BY table_name;
"
```

#### 6.2 Index Sizes

```bash
psql -h localhost -U indexer -d indexer_test -c "
SELECT
  schemaname,
  relname AS table_name,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
  pg_size_pretty(pg_relation_size(relid)) AS table_size,
  pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
"
```

#### 6.3 Sample Query Timings

```bash
# Symbol search by name
psql -h localhost -U indexer -d indexer_test -c "\timing on" -c "
SELECT s.id, s.name, s.fully_qualified_name, s.language, fi.path
FROM symbols s
JOIN file_instances fi ON s.file_instance_id = fi.id
WHERE s.name ILIKE '%Repository%'
LIMIT 20;
"

# File lookup by path pattern
psql -h localhost -U indexer -d indexer_test -c "\timing on" -c "
SELECT id, path, content_hash, lifecycle_state
FROM file_instances
WHERE path LIKE '%/src/%'
LIMIT 20;
"

# Chunk count per repository
psql -h localhost -U indexer -d indexer_test -c "\timing on" -c "
SELECT r.path_with_namespace, COUNT(c.id) AS chunk_count
FROM repositories r
JOIN file_instances fi ON fi.repository_id = r.id
JOIN chunks c ON c.file_instance_id = fi.id
GROUP BY r.id, r.path_with_namespace
ORDER BY chunk_count DESC;
"
```

#### 6.4 EXPLAIN Plans (Save for Comparison)

```bash
# Symbol search EXPLAIN
psql -h localhost -U indexer -d indexer_test -c "
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT s.id, s.name, s.fully_qualified_name, s.language, fi.path
FROM symbols s
JOIN file_instances fi ON s.file_instance_id = fi.id
WHERE s.name ILIKE '%Repository%'
LIMIT 20;
" > /workspaces/axon-mcp/axon-src/docs/validation/explain_symbol_search.json

# File lookup EXPLAIN
psql -h localhost -U indexer -d indexer_test -c "
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT id, path, content_hash, lifecycle_state
FROM file_instances
WHERE path LIKE '%/src/%'
LIMIT 20;
" > /workspaces/axon-mcp/axon-src/docs/validation/explain_file_lookup.json
```

---

### Step 7: Create Database Snapshot

**Purpose**: Enable repeatable baseline restoration.

```bash
# Create baseline snapshot
pg_dump -h localhost -U indexer -d indexer_test -Fc > /workspaces/axon-mcp/axon-src/docs/validation/baseline_test_data_$(date +%Y%m%d).dump

# Verify snapshot
ls -lh /workspaces/axon-mcp/axon-src/docs/validation/baseline_test_data_*.dump
```

**Expected Output**: File size ~10-100MB depending on corpus size.

---

### Step 8: Document Baseline

**Purpose**: Create reference artifact for migration validation.

Create file: `axon-src/docs/validation/baseline_metrics_YYYYMMDD.md`

```markdown
# Baseline Metrics - Phase 2.5.0

**Date**: YYYY-MM-DD
**Schema Version**: Current (pre-migration)

## Corpus Summary

| Repository | Files | Symbols | Chunks | Embeddings |
|------------|-------|---------|--------|------------|
| R1: axon-src | TBD | TBD | TBD | TBD |
| R2: jverein | TBD | TBD | TBD | TBD |

## Row Counts

| Table | Count |
|-------|-------|
| repositories | TBD |
| commits | TBD |
| file_instances | TBD |
| file_contents | TBD |
| symbols | TBD |
| relations | TBD |
| chunks | TBD |
| embeddings | TBD |
| chunk_symbol_links | TBD |

## Storage Sizes

| Table | Total Size | Table Size | Index Size |
|-------|------------|------------|------------|
| TBD | TBD | TBD | TBD |

## Query Timings

| Query | Execution Time | Notes |
|-------|----------------|-------|
| Symbol search by name | TBD ms | ILIKE %Repository% |
| File lookup by path | TBD ms | LIKE %/src/% |
| Chunk count per repo | TBD ms | Aggregation |

## Snapshot Artifact

- File: `baseline_test_data_YYYYMMDD.dump`
- Size: TBD MB
- Format: PostgreSQL custom dump (-Fc)
```

---

## 4. Artifact Storage Locations

| Artifact Type | Path Pattern | Example |
|---------------|--------------|---------|
| Execution Plan | `axon-src/docs/validation/phase_2.5.0_baseline_execution_plan.md` | This document |
| Baseline Metrics | `axon-src/docs/validation/baseline_metrics_YYYYMMDD.md` | `baseline_metrics_20260323.md` |
| Database Snapshot | `axon-src/docs/validation/baseline_test_data_YYYYMMDD.dump` | `baseline_test_data_20260323.dump` |
| EXPLAIN Plans | `axon-src/docs/validation/explain_*.json` | `explain_symbol_search.json` |

---

## 5. Blockers and Approval Gates

### Required Approvals Before Execution

| # | Question | Options | Impact |
|---|----------|---------|--------|
| 1 | **Repository URLs** - Confirm exact remote URLs for axon-src and jverein | Provide actual URLs | Without correct URLs, registration will fail |
| 2 | **Database Target** - Use `indexer_test` (recommended) or `indexer`? | `indexer_test` / `indexer` | `indexer_test` is isolated; `indexer` may have existing data |
| 3 | **Third Repository** - Include a small third repository? | Yes (specify URL) / No | Optional, adds coverage |
| 4 | **Authentication** - Confirm `AUTH_ENABLED=false` for baseline run? | Yes / No | If No, need valid API key for curl commands |

### Potential Blockers

| Blocker | Mitigation |
|---------|------------|
| Git clone fails (auth/network) | Verify network access; use public repos |
| Celery worker not processing | Check Redis connection; verify queue names |
| Out-of-memory during indexing | Reduce concurrency; use smaller repo first |
| pg_dump permission denied | Verify PostgreSQL user has read access |

---

## 6. Rollback / Cleanup

If baseline creation fails or needs to be restarted:

```bash
# Stop services
pkill -f "uvicorn src.api.main:app"
pkill -f "celery"

# Reset database
make db-reset-test

# Restart from Step 2
```

---

## 7. Success Criteria

| Criterion | Verification |
|-----------|--------------|
| All repositories indexed | `status: "completed"` in API response |
| Row counts documented | Metrics file contains all table counts |
| Snapshot created | `.dump` file exists and is non-empty |
| EXPLAIN plans captured | JSON files exist for key queries |
| No schema changes made | Verify via `git diff` |

---

## 8. Next Steps After Completion

1. **Proceed to Phase 2.5.1** - Commit, Branch, Tag and TREE_FILE schema (renumbered 2026-10-14; user registry is now 2.5.2)
2. **Preserve baseline artifacts** - Do not modify or delete
3. **Update implementation plan** - Mark Phase 2.5.0 as `✅` complete

---

## Appendix: Quick Reference Commands

```bash
# Full sequence (after approvals)
cd /workspaces/axon-mcp/axon-src
source /home/vscode/.venv-dev/bin/activate
export DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer_test'
export AUTH_ENABLED=false

# Terminal 1
make api-dev

# Terminal 2
python scripts/start_celery_worker.py --queue default,repository_sync,file_parsing,embeddings,discovery_inventory --concurrency 4 --loglevel info

# Terminal 3 (after services ready)
make db-reset-test
# ... register repositories, monitor, capture metrics, create snapshot
```
