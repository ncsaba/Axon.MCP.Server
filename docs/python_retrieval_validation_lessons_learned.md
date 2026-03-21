# Python Retrieval Validation - Lessons Learned

**Date**: 2026-03-21  
**Context**: Running Python retrieval quality validation against the axon-src codebase

This document captures the issues encountered during validation setup and execution, along with their solutions. It serves as a troubleshooting reference for future validation runs and similar testing scenarios.

---

## Issue Summary

| # | Issue | Category | Severity |
|---|-------|----------|----------|
| 1 | JWT authentication blocking API startup | Configuration | High |
| 2 | Enum type doesn't exist after schema reset | Database | High |
| 3 | Celery worker not processing tasks | Configuration | High |
| 4 | API and Celery using different databases | Configuration | Critical |
| 5 | Bug in `knowledge_extractor.py` | Code | High |
| 6 | API returns 0 Python symbols despite database having them | Query/Script | Medium |

---

## Issue 1: JWT Authentication Blocking API Startup

### Symptoms

API server failed to start with error:
```
AssertionError: JWT_SECRET_KEY not configured
```

### Root Cause

The API server requires JWT authentication by default. When `AUTH_ENABLED` is not explicitly disabled, the JWT middleware validates that `JWT_SECRET_KEY` is set.

### Solution

Set environment variables to disable authentication for development/testing:

```bash
export AUTH_ENABLED=false
export MCP_AUTH_ENABLED=false
export REST_AUTH_METHODS=[]
export MCP_AUTH_METHODS=[]
```

### Prevention

The validation script now includes these in the environment when starting the API server:

```python
proc_env["AUTH_ENABLED"] = "false"
proc_env["MCP_AUTH_ENABLED"] = "false"
proc_env["REST_AUTH_METHODS"] = "[]"
proc_env["MCP_AUTH_METHODS"] = "[]"
```

---

## Issue 2: Enum Type Doesn't Exist After Schema Reset

### Symptoms

After resetting the database schema, API startup failed with:
```
ProgrammingError: type "sourcecontrolproviderenum" does not exist
```

### Root Cause

The `_ensure_source_control_provider_enum_values()` function in `main.py` attempted to alter a PostgreSQL enum type without first checking if it exists. After a fresh schema reset, the enum doesn't exist yet.

### Solution

Modified [`src/api/main.py`](../src/api/main.py) to check for enum existence before altering:

```python
async def _ensure_source_control_provider_enum_values(conn) -> None:
    try:
        # First check if the enum type exists
        result = await conn.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = 'sourcecontrolproviderenum'")
        )
        if result.scalar() is None:
            logger.info("source_control_provider_enum_not_found_will_be_created")
            return  # Enum will be created by SQLAlchemy
        
        # Enum exists, ensure values are present
        # ... rest of function
```

### Prevention

Always check for database object existence before modifying. SQLAlchemy will create enums during table creation, so the alter logic should only run for existing databases.

---

## Issue 3: Celery Worker Not Processing Tasks

### Symptoms

- Repository sync triggered successfully
- Celery worker showed no CPU activity
- No tasks being processed
- Sync status stuck in "PENDING"

### Root Cause

The Celery worker was only listening to the `default` queue, but tasks were being routed to specific queues:
- `repository_sync`
- `file_parsing`
- `embeddings`
- `inventory`
- `ai_enrichment`
- `repository_aggregation`

### Solution

Start Celery worker with explicit queue subscriptions:

```bash
celery -A src.workers.celery_app worker \
    --loglevel=info \
    -Q repository_sync,file_parsing,embeddings,inventory,ai_enrichment,repository_aggregation \
    --concurrency=2
```

### Prevention

The validation script now includes all queues in the worker startup command. For production, consider:
1. Using a Celery router configuration that makes queue subscriptions explicit
2. Adding a health check that verifies worker queue subscriptions
3. Documentation of required queues in deployment guides

---

## Issue 4: API and Celery Using Different Databases

### Symptoms

- API showed repository was created
- Celery worker processed tasks
- No data appeared in API queries after sync
- Database queries showed data in wrong database

### Root Cause

The API was started with `DATABASE_URL` pointing to `indexer_test` (test database), but the Celery worker was started with the default `DATABASE_URL` pointing to `indexer` (production database).

This happened because:
1. API startup code correctly substituted `TEST_DATABASE_URL` for `DATABASE_URL`
2. Celery worker startup code did NOT perform the same substitution
3. Tasks ran against production DB while API queried test DB

### Solution

Ensure both API and Celery use the same database by explicitly setting `DATABASE_URL`:

```bash
# For test environment
export DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer_test'

# Start both services with the same environment
```

### Prevention

1. **Script improvement**: The validation script now passes `DATABASE_URL` explicitly to both API and Celery
2. **Environment consistency**: Use a single source of truth for database URLs
3. **Health checks**: Add database name to health check response to catch mismatches

```python
# In validation script
if use_test_db and "TEST_DATABASE_URL" in proc_env:
    proc_env["DATABASE_URL"] = proc_env["TEST_DATABASE_URL"]
    print("    Using TEST_DATABASE_URL")
```

---

## Issue 5: Bug in `knowledge_extractor.py`

### Symptoms

```
TypeError: 'file_id' is an invalid keyword argument for Dependency
```

### Root Cause

In [`src/extractors/knowledge_extractor.py`](../src/extractors/knowledge_extractor.py) line 783, the code passed `file_id=file_id` to the `Dependency` model, but the model expects `file_instance_id` instead.

This was a missed rename from the file instance/content separation work.

### Solution

Changed line 783 from:
```python
file_id=file_id,
```
to:
```python
file_instance_id=file_id,
```

### Prevention

1. **Greppable naming**: Use unique parameter names that can be easily searched
2. **Model validation**: Consider using Pydantic models that provide clearer error messages
3. **Integration tests**: Add tests that exercise the full dependency creation path

---

## Issue 6: API Returns 0 Python Symbols Despite Database Having Them

### Symptoms

- Database query showed 2754 Python symbols
- API returned 0 Python symbols
- API returned symbols of other languages (MARKDOWN, UNKNOWN)

### Root Cause

Two issues:

1. **Wrong port**: The validation script was querying port 8000, but the API was running on port 8080

2. **Pagination without language filter**: The script fetched the first 500 symbols without a language filter. Since symbols are ordered by ID, and Python symbols had higher IDs (starting around 5550+), only MARKDOWN and UNKNOWN symbols were returned.

### Solution

1. **Correct port**: Updated `DEFAULT_BASE_URL` to use port 8080

2. **Language-specific queries**: Modified `get_symbols_for_repo()` to accept a `language` parameter:

```python
def get_symbols_for_repo(
    client: ApiClient, repo_id: int, limit: int = 5000, language: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get all symbols for a repository, optionally filtered by language."""
    symbols = []
    offset = 0
    page_size = 100
    while True:
        query = {"repository_id": repo_id, "skip": offset, "limit": page_size}
        if language:
            query["language"] = language
        # ... rest of function
```

3. **Fetch Python symbols specifically**: Updated `run_validation()` to fetch Python symbols explicitly:

```python
print("\n[STEP] Fetching Python symbols...")
python_symbols = get_symbols_for_repo(client, repo_id, language="PYTHON")
print(f"  [INFO] Found {len(python_symbols)} Python symbols")
```

### Prevention

1. **Configuration validation**: Add startup check that verifies API port matches configuration
2. **Symbol distribution logging**: Log symbol kind/language distribution to catch data issues early
3. **Higher limits**: Use higher default limits (5000 instead of 500) for comprehensive validation

---

## General Recommendations

### 1. Service Health Checks

Add comprehensive health checks that verify:
- Database connectivity and database name
- Redis connectivity
- Celery worker queue subscriptions
- Authentication state

### 2. Environment Consistency

Use a single environment configuration source that:
- Defines test vs production database URLs
- Is used by all services (API, Celery, scripts)
- Validates consistency at startup

### 3. Logging Improvements

Add structured logging that includes:
- Database name in connection logs
- Queue subscriptions in Celery worker logs
- Authentication mode in API startup logs

### 4. Validation Script Improvements

The validation script should:
- Check database consistency between services
- Verify Celery queue subscriptions
- Report symbol distribution by language
- Use explicit language filters for language-specific checks

---

## Automated Pre-flight Checks

The validation script now includes automated pre-flight checks that catch common issues before starting services. These checks are documented in the `_preflight_checks()` function in [`scripts/validate_python_retrieval.py`](../scripts/validate_python_retrieval.py).

### Checks Performed

| Check | Warning Condition | Issue Prevented |
|-------|-------------------|-----------------|
| Database URL | `TEST_DATABASE_URL` not set when using test DB | #4 (different databases) |
| Redis URL | `REDIS_URL`/`CELERY_BROKER_URL` not set | Celery connection failures |
| Auth settings | `AUTH_ENABLED=true` without JWT config | #1 (auth blocking startup) |

### Running Pre-flight Checks

Pre-flight checks run automatically when using `--start-services`:

```bash
python scripts/validate_python_retrieval.py --start-services
```

Output example:
```
[STEP] Running pre-flight checks...
  [WARN] Pre-flight checks found potential issues:
    - TEST_DATABASE_URL not set - services may use wrong database
  [HINT] See docs/python_retrieval_validation_lessons_learned.md for guidance
  [INFO] Proceeding anyway (services may fail to start)...
```

### Adding New Checks

To add a new pre-flight check, edit the `_preflight_checks()` function:

```python
def _preflight_checks(env: Dict[str, str], use_test_db: bool = True) -> List[str]:
    warnings = []
    
    # Add new check here
    if some_condition:
        warnings.append("Description of the issue and potential impact")
    
    return warnings
```

---

## Quick Reference: Validation Script Usage

```bash
# Start services and do fresh indexing
python scripts/validate_python_retrieval.py --start-services

# Run validation on existing index
python scripts/validate_python_retrieval.py --repo-id 3 --skip-indexing

# Full reset and fresh index
python scripts/validate_python_retrieval.py --start-services --reset-db

# Stop all services
python scripts/validate_python_retrieval.py --stop-services
```

---

## Files Modified During Troubleshooting

| File | Change |
|------|--------|
| `src/api/main.py` | Added enum existence check before alter |
| `src/extractors/knowledge_extractor.py` | Fixed `file_id` → `file_instance_id` |
| `scripts/validate_python_retrieval.py` | Multiple fixes for service management and queries |
| `docs/SESSION_HANDOVER.md` | Updated with validation results |
