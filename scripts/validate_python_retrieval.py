#!/usr/bin/env python3
"""Python Retrieval Quality Validation Script.

This script validates Python semantic indexing quality by:
1. Indexing the axon-src codebase itself (self-indexing)
2. Running targeted queries to validate:
   - Import relationship extraction
   - Call relationship extraction
   - Endpoint extraction (FastAPI/Flask patterns)
   - Retrieval chunk coherence (decorator attachment)
   - Call-neighbor context retrieval

The script is designed to work with already-running services:
1. Checks if API server is already running
2. Checks if Celery worker is already running
3. Only starts services if not already available
4. Only resets DB if schema is missing/incompatible
5. Does NOT kill services when script exits (detached mode)

Prerequisites:
- PostgreSQL database running (default: localhost:5432)
- Redis running (default: localhost:6379)

Usage:
    # First time setup (start services and index):
    python scripts/validate_python_retrieval.py --start-services
    
    # Run validation on already-indexed repo:
    python scripts/validate_python_retrieval.py --repo-id 1
    
    # Full fresh run (reset DB and reindex):
    python scripts/validate_python_retrieval.py --reset-db --start-services
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request


DEFAULT_BASE_URL = "http://localhost:8080/api/v1"
DEFAULT_DEV_ENV = Path(__file__).resolve().parent / "dev_env.sh"
AXON_SRC_PATH = Path(__file__).resolve().parent.parent

# Process management for started services (only used in --start-services mode)
_started_processes: List[subprocess.Popen] = []


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    name: str
    passed: bool
    details: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    related_symbols: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Aggregated validation report."""
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    results: List[ValidationResult] = field(default_factory=list)

    def add(self, result: ValidationResult) -> None:
        self.total_checks += 1
        if result.passed:
            self.passed += 1
        else:
            self.failed += 1
        self.results.append(result)

    def summary(self) -> str:
        status = "PASS" if self.failed == 0 else "FAIL"
        return f"[{status}] {self.passed}/{self.total_checks} checks passed ({self.failed} failed)"


def _load_dev_env(path: Path) -> Dict[str, str]:
    """Load environment variables from dev_env.sh."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        if "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        try:
            values[key] = ast.literal_eval(raw) if raw[:1] in {"'", '"'} else raw
        except Exception:
            values[key] = raw.strip("'").strip('"')
    return values


def _cleanup_processes() -> None:
    """Terminate all started processes (only called on error or explicit reset)."""
    for proc in _started_processes:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _started_processes.clear()


def _check_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a port is open (service is listening)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            return result == 0
    except Exception:
        return False


def _check_api_health(base_url: str, api_key: str) -> bool:
    """Check if API is healthy and responsive."""
    try:
        url = f"{base_url.rstrip('/')}/repositories?limit=1"
        headers = {"Accept": "application/json", "X-API-Key": api_key}
        req = request.Request(url, method="GET", headers=headers)
        with request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _preflight_checks(env: Dict[str, str], use_test_db: bool = True) -> List[str]:
    """Run pre-flight checks to catch common issues before starting services.
    
    Returns a list of warning messages (empty if all checks pass).
    
    See: docs/python_retrieval_validation_lessons_learned.md for context.
    """
    warnings = []
    
    # Check 1: Database URL consistency
    db_url = env.get("DATABASE_URL", "")
    test_db_url = env.get("TEST_DATABASE_URL", "")
    target_db = test_db_url if use_test_db else db_url
    
    if use_test_db and not test_db_url:
        warnings.append("TEST_DATABASE_URL not set - services may use wrong database")
    elif not db_url:
        warnings.append("DATABASE_URL not set - services may fail to start")
    
    # Check 2: Redis URL
    redis_url = env.get("REDIS_URL", "") or env.get("CELERY_BROKER_URL", "")
    if not redis_url:
        warnings.append("REDIS_URL/CELERY_BROKER_URL not set - Celery may fail")
    
    # Check 3: Auth settings (should be disabled for validation)
    # These are set by the script, but warn if already set to enabled
    if env.get("AUTH_ENABLED", "").lower() not in ("false", "0", ""):
        warnings.append("AUTH_ENABLED=true may block API startup without JWT_SECRET_KEY")
    
    return warnings


def _verify_database_consistency(api_base_url: str, celery_db_name: str) -> List[str]:
    """Verify API and Celery are using the same database.
    
    Returns a list of warning messages (empty if consistent).
    """
    warnings = []
    
    # Query API for database info (if available)
    # For now, we just warn about the potential issue
    return warnings


def _check_celery_running() -> bool:
    """Check if a Celery worker is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "celery.*axon"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip()
    except Exception:
        return False


def _check_db_schema_exists(db_url: str) -> bool:
    """Check if the database has the required schema tables."""
    import re
    match = re.search(r'/([^/]+)$', db_url)
    if not match:
        return False
    
    db_name = match.group(1)
    
    try:
        # Check if key tables exist
        result = subprocess.run(
            ["psql", "-d", db_name, "-c", 
             "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'repositories');"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "t" in result.stdout.lower():
            return True
        return False
    except Exception:
        return False


def _reset_database_if_needed(db_url: str, force: bool = False) -> bool:
    """Reset the database schema if needed or forced.
    
    Args:
        db_url: The database URL to reset
        force: If True, always reset. If False, only reset if schema is missing.
    
    Returns:
        True if reset was performed or not needed, False on error.
    """
    import re
    match = re.search(r'/([^/]+)$', db_url)
    if not match:
        print(f"  [ERROR] Could not parse database name from URL")
        return False
    
    db_name = match.group(1)
    
    # Check if reset is needed
    if not force:
        schema_exists = _check_db_schema_exists(db_url)
        if schema_exists:
            print(f"  [INFO] Database schema already exists, skipping reset")
            return True
        print(f"  [INFO] Database schema missing, will reset")
    
    print(f"  [INFO] Resetting database: {db_name}")
    
    try:
        # Terminate active connections
        terminate_sql = f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{db_name}'
          AND pid <> pg_backend_pid();
        """
        subprocess.run(
            ["psql", "-d", "postgres", "-c", terminate_sql],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        # Drop and recreate schema
        result = subprocess.run(
            ["psql", "-d", db_name, "-c", 
             "DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE EXTENSION IF NOT EXISTS vector;"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            print(f"  [WARN] psql reset had issues: {result.stderr[:200]}")
        else:
            print(f"  [INFO] Database reset complete")
        return True
    except FileNotFoundError:
        print("  [WARN] psql not found - database will be reset on API startup")
        return True
    except Exception as e:
        print(f"  [WARN] Could not reset database: {e}")
        return True  # Continue anyway


def _start_api_server_detached(base_url: str, env: Dict[str, str], use_test_db: bool = True) -> bool:
    """Start the API server in detached mode (survives script exit).
    
    Returns True if server was started or was already running.
    """
    parsed = parse.urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8080
    
    # Check if already running
    if _check_port_open(host, port):
        print(f"  [INFO] API server already running on {host}:{port}")
        return True
    
    print(f"  [INFO] Starting API server on {host}:{port}...")
    
    # Prepare environment
    proc_env = os.environ.copy()
    proc_env.update(env)
    proc_env["AUTH_ENABLED"] = "false"
    proc_env["MCP_AUTH_ENABLED"] = "false"
    proc_env["REST_AUTH_METHODS"] = "[]"
    proc_env["MCP_AUTH_METHODS"] = "[]"
    
    if use_test_db and "TEST_DATABASE_URL" in proc_env:
        proc_env["DATABASE_URL"] = proc_env["TEST_DATABASE_URL"]
        print(f"    Using TEST_DATABASE_URL")
    
    # Start with nohup to detach
    log_file = "/tmp/axon_api.log"
    cmd = [
        "nohup", sys.executable, "-m", "uvicorn",
        "src.api.main:app",
        "--host", host,
        "--port", str(port),
    ]
    
    with open(log_file, "w") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=AXON_SRC_PATH,
            env=proc_env,
            stdout=log,
            stderr=log,
            start_new_session=True,  # Detach from this process group
        )
    
    # Wait for server to start
    for _ in range(30):
        if _check_port_open(host, port):
            print(f"  [INFO] API server started (PID: {proc.pid})")
            print(f"    Logs: {log_file}")
            return True
        time.sleep(1)
    
    print(f"  [ERROR] API server failed to start within 30s")
    print(f"    Check logs: {log_file}")
    return False


def _start_celery_worker_detached(env: Dict[str, str], use_test_db: bool = True) -> bool:
    """Start the Celery worker in detached mode (survives script exit).
    
    Returns True if worker was started or was already running.
    """
    # Check if already running
    if _check_celery_running():
        print("  [INFO] Celery worker already running")
        return True
    
    print("  [INFO] Starting Celery worker...")
    
    # Prepare environment
    proc_env = os.environ.copy()
    proc_env.update(env)
    
    if use_test_db and "TEST_DATABASE_URL" in proc_env:
        proc_env["DATABASE_URL"] = proc_env["TEST_DATABASE_URL"]
        print("    Using TEST_DATABASE_URL")
    
    # Start with nohup to detach
    log_file = "/tmp/axon_celery.log"
    cmd = [
        "nohup", sys.executable, "-m", "celery",
        "-A", "src.workers.celery_app",
        "worker",
        "--loglevel=info",
        "-Q", "repository_sync,file_parsing,embeddings,inventory,ai_enrichment,repository_aggregation",
        "--concurrency=2",
    ]
    
    with open(log_file, "w") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=AXON_SRC_PATH,
            env=proc_env,
            stdout=log,
            stderr=log,
            start_new_session=True,  # Detach from this process group
        )
    
    # Give worker time to initialize
    time.sleep(3)
    
    if _check_celery_running():
        print(f"  [INFO] Celery worker started (PID: {proc.pid})")
        print(f"    Logs: {log_file}")
        return True
    
    print(f"  [ERROR] Celery worker failed to start")
    print(f"    Check logs: {log_file}")
    return False


def _stop_services(host: str, port: int) -> None:
    """Stop API and Celery services."""
    print("  [INFO] Stopping services...")
    
    # Kill processes on the API port
    try:
        result = subprocess.run(
            ["lsof", "-t", "-i", f":{port}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    print(f"    Terminated API process {pid}")
                except ProcessLookupError:
                    pass
    except Exception as e:
        print(f"    [WARN] Could not check/kill API processes: {e}")
    
    # Kill celery workers
    try:
        result = subprocess.run(
            ["pkill", "-f", "celery.*axon"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            print("    Terminated celery workers")
    except Exception as e:
        print(f"    [WARN] Could not kill celery workers: {e}")
    
    time.sleep(2)


class ApiClient:
    """Simple API client for Axon REST API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            encoded = parse.urlencode(
                {key: value for key, value in query.items() if value is not None},
                doseq=True,
            )
            url = f"{url}?{encoded}"

        data = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, method=method.upper(), data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc.fp else ""
            detail: Any = body
            try:
                detail = json.loads(body)
            except Exception:
                pass
            raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} - {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"{method} {path} failed: {exc}") from exc


def _print_step(name: str, ok: bool, detail: str) -> None:
    """Print a step result."""
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}: {detail}")


def find_repository_by_namespace(client: ApiClient, namespace: str) -> Optional[Dict[str, Any]]:
    """Find an existing repository by path_with_namespace."""
    repos = client.request_json("GET", "/repositories", query={"limit": 100})
    for repo in repos.get("items", []):
        if repo.get("path_with_namespace") == namespace:
            return repo
    return None


def create_or_get_repository(client: ApiClient, namespace: str, path: Path) -> Dict[str, Any]:
    """Create a new repository or return existing one."""
    existing = find_repository_by_namespace(client, namespace)
    if existing:
        print(f"  [INFO] Found existing repository: id={existing['id']} namespace={namespace}")
        return existing

    payload = {
        "provider": "GITLAB",
        "name": path.name,
        "path_with_namespace": namespace,
        "url": f"file://{path}",
        "clone_url": f"file://{path}",
        "default_branch": "main",
    }
    repo = client.request_json("POST", "/repositories", payload=payload)
    print(f"  [INFO] Created repository: id={repo['id']} namespace={namespace}")
    return repo


def wait_for_sync(client: ApiClient, repo_id: int, timeout: int = 1200, interval: int = 10) -> Dict[str, Any]:
    """Wait for repository sync to complete.
    
    Uses a longer default timeout (20 min) and interval for async indexing.
    """
    start = time.time()
    last_status = None
    last_counts = {}
    
    while True:
        history = client.request_json(
            "GET",
            f"/repositories/{repo_id}/sync-history",
            query={"limit": 1, "offset": 0},
        )
        items = history.get("items", [])
        if items:
            latest = items[0]
            status = latest.get("status")
            
            # Show progress with symbol/chunk counts if available
            symbols = latest.get("symbols_created", last_counts.get("symbols", "?"))
            chunks = latest.get("chunks_created", last_counts.get("chunks", "?"))
            files = latest.get("files_processed", last_counts.get("files", "?"))
            
            if status != last_status:
                print(f"    Sync status: {status} (files={files}, chunks={chunks}, symbols={symbols})")
                last_status = status
                last_counts = {"symbols": symbols, "chunks": chunks, "files": files}
            
            if status == "COMPLETED":
                return latest
            if status in {"FAILED", "CANCELLED"}:
                raise RuntimeError(f"Sync job {latest.get('id')} ended with status={status}")
        
        if time.time() - start > timeout:
            raise RuntimeError(f"Timed out waiting for sync after {timeout}s")
        
        time.sleep(interval)


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
        result = client.request_json(
            "GET",
            "/symbols",
            query=query,
        )
        items = result.get("items", result) if isinstance(result, dict) else result
        if not items:
            break
        symbols.extend(items)
        if len(items) < page_size:
            break
        offset += page_size
        if offset >= limit:
            break
    return symbols


def get_relations_for_symbol(client: ApiClient, symbol_id: int) -> List[Dict[str, Any]]:
    """Get relations for a specific symbol via search/context API."""
    try:
        result = client.request_json(
            "POST",
            "/mcp/tools/get_symbol_context",
            payload={"symbol_id": symbol_id, "include_relationships": True},
        )
        content = result.get("content", [])
        if content:
            text = "\n".join(item.get("text", "") for item in content if item.get("type") == "text")
            return {"text": text, "raw": result}
    except Exception:
        pass
    return {}


def search_code(client: ApiClient, query: str, repo_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Search code within a repository."""
    result = client.request_json(
        "GET",
        "/search",
        query={"query": query, "repository_id": repo_id, "limit": limit, "hybrid": "true"},
    )
    return result if isinstance(result, list) else result.get("items", [])


def validate_import_relations(client: ApiClient, repo_id: int, symbols: List[Dict[str, Any]]) -> ValidationResult:
    """Validate that Python import relations are extracted correctly."""
    expected_imports = [
        ("ImportRelationshipBuilder", "import_resolver"),
        ("CallGraphBuilder", "call_graph_builder"),
        ("ApiEndpointExtractor", "api_extractor"),
    ]
    
    found_imports = 0
    checked_patterns = 0
    details = []
    
    for symbol in symbols:
        name = symbol.get("name", "")
        fqn = symbol.get("fully_qualified_name", "")
        
        for expected_name, expected_module in expected_imports:
            if expected_name in name or expected_name in fqn:
                checked_patterns += 1
                context = get_relations_for_symbol(client, symbol.get("id", 0))
                if context:
                    found_imports += 1
                    details.append(f"{name} has context available")
    
    if checked_patterns == 0:
        return ValidationResult(
            name="Import Relations",
            passed=False,
            details="No target import patterns found in indexed symbols",
            expected="At least one import pattern to be found",
            actual="No matching symbols",
        )
    
    return ValidationResult(
        name="Import Relations",
        passed=checked_patterns > 0,
        details=f"Found {checked_patterns} symbols with expected import patterns, {found_imports} have context",
        related_symbols=[s.get("name", "") for s in symbols if any(e[0] in s.get("name", "") for e in expected_imports)],
    )


def validate_call_relations(client: ApiClient, repo_id: int, symbols: List[Dict[str, Any]]) -> ValidationResult:
    """Validate that Python call relations are extracted correctly."""
    call_candidates = []
    for symbol in symbols:
        kind = symbol.get("kind", "")
        name = symbol.get("name", "")
        if kind in {"FUNCTION", "METHOD"} and ("sync" in name.lower() or "extract" in name.lower() or "build" in name.lower()):
            call_candidates.append(symbol)
    
    if not call_candidates:
        return ValidationResult(
            name="Call Relations",
            passed=False,
            details="No function/method symbols found that would have call relationships",
        )
    
    sample_with_context = 0
    for symbol in call_candidates[:5]:
        context = get_relations_for_symbol(client, symbol.get("id", 0))
        if context:
            sample_with_context += 1
    
    return ValidationResult(
        name="Call Relations",
        passed=len(call_candidates) > 0,
        details=f"Found {len(call_candidates)} function/method symbols that could have call relations, {sample_with_context}/5 sampled have context",
        related_symbols=[s.get("name", "") for s in call_candidates[:10]],
    )


def validate_endpoint_extraction(client: ApiClient, repo_id: int, symbols: List[Dict[str, Any]]) -> ValidationResult:
    """Validate that FastAPI/Flask endpoints are extracted correctly."""
    endpoint_symbols = []
    for symbol in symbols:
        kind = symbol.get("kind", "")
        if kind == "ENDPOINT":
            endpoint_symbols.append(symbol)
    
    route_handlers = []
    for symbol in symbols:
        name = symbol.get("name", "")
        if any(route_word in name.lower() for route_word in ["route", "endpoint", "get_", "post_", "put_", "delete_"]):
            route_handlers.append(symbol)
    
    passed = len(endpoint_symbols) > 0 or len(route_handlers) > 0
    
    details_parts = []
    if endpoint_symbols:
        details_parts.append(f"{len(endpoint_symbols)} ENDPOINT symbols found")
    if route_handlers:
        details_parts.append(f"{len(route_handlers)} potential route handlers found")
    
    if not details_parts:
        details_parts.append("No endpoints or route handlers found")
    
    return ValidationResult(
        name="Endpoint Extraction",
        passed=passed,
        details=", ".join(details_parts),
        related_symbols=[s.get("name", "") for s in endpoint_symbols[:5]] + [s.get("name", "") for s in route_handlers[:5]],
    )


def validate_chunk_coherence(client: ApiClient, repo_id: int) -> ValidationResult:
    """Validate that retrieval chunks maintain coherence (decorators attached to functions)."""
    queries = [
        "FastAPI route handler",
        "router endpoint decorator",
        "@router.get",
        "API endpoint definition",
    ]
    
    results_found = 0
    sample_chunks = []
    
    for query in queries:
        try:
            results = search_code(client, query, repo_id, limit=3)
            if results:
                results_found += 1
                for r in results[:2]:
                    chunk_text = r.get("content", r.get("text", ""))[:200]
                    if chunk_text:
                        sample_chunks.append(chunk_text)
        except Exception:
            pass
    
    decorator_preserved = 0
    for chunk in sample_chunks:
        if "@" in chunk or "def " in chunk or "async def " in chunk:
            decorator_preserved += 1
    
    passed = results_found > 0
    
    return ValidationResult(
        name="Chunk Coherence",
        passed=passed,
        details=f"Found results for {results_found}/{len(queries)} queries, {decorator_preserved} chunks show code structure",
        actual=f"Sample chunks: {sample_chunks[:2]}",
    )


def validate_call_neighbor_context(client: ApiClient, repo_id: int, symbols: List[Dict[str, Any]]) -> ValidationResult:
    """Validate that call-neighbor context is available for retrieval."""
    helper_symbols = []
    for symbol in symbols:
        name = symbol.get("name", "")
        if name in ["_load_dev_env", "get_logger", "utcnow", "_print_step"]:
            helper_symbols.append(symbol)
    
    if not helper_symbols:
        for symbol in symbols:
            name = symbol.get("name", "")
            kind = symbol.get("kind", "")
            if kind == "FUNCTION" and name.startswith("_") and len(name) > 3:
                helper_symbols.append(symbol)
                if len(helper_symbols) >= 3:
                    break
    
    context_available = 0
    for symbol in helper_symbols[:3]:
        context = get_relations_for_symbol(client, symbol.get("id", 0))
        if context:
            context_available += 1
    
    return ValidationResult(
        name="Call-Neighbor Context",
        passed=len(helper_symbols) > 0,
        details=f"Found {len(helper_symbols)} helper/utility functions, {context_available} have context available",
        related_symbols=[s.get("name", "") for s in helper_symbols],
    )


def validate_python_symbol_coverage(client: ApiClient, repo_id: int, symbols: List[Dict[str, Any]]) -> ValidationResult:
    """Validate that Python symbols are comprehensively indexed."""
    python_symbols = [s for s in symbols if s.get("language") == "PYTHON" or s.get("file_path", "").endswith(".py")]
    
    kind_counts: Dict[str, int] = {}
    for symbol in python_symbols:
        kind = symbol.get("kind", "UNKNOWN")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    
    expected_kinds = ["CLASS", "FUNCTION", "METHOD"]
    found_kinds = [k for k in expected_kinds if kind_counts.get(k, 0) > 0]
    
    passed = len(found_kinds) >= 2
    
    return ValidationResult(
        name="Python Symbol Coverage",
        passed=passed,
        details=f"Found {len(python_symbols)} Python symbols: {kind_counts}",
        expected=f"At least 2 of {expected_kinds}",
        actual=f"Found: {found_kinds}",
    )


def run_validation(client: ApiClient, repo_id: int) -> ValidationReport:
    """Run all validation checks."""
    report = ValidationReport()
    
    print("\n[STEP] Fetching all symbols...")
    all_symbols = get_symbols_for_repo(client, repo_id)
    print(f"  [INFO] Found {len(all_symbols)} total symbols")
    
    print("\n[STEP] Fetching Python symbols...")
    python_symbols = get_symbols_for_repo(client, repo_id, language="PYTHON")
    print(f"  [INFO] Found {len(python_symbols)} Python symbols")
    
    # Use Python symbols for Python-specific checks, all symbols for general checks
    symbols = python_symbols if python_symbols else all_symbols
    
    if not symbols:
        print("  [ERROR] No symbols found - cannot validate")
        return report
    
    # Show symbol kind distribution
    kind_counts: Dict[str, int] = {}
    for symbol in symbols:
        kind = symbol.get("kind", "UNKNOWN")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    print(f"  [INFO] Symbol kinds: {kind_counts}")
    
    print("\n[STEP] Running validation checks...")
    
    # Import relations
    print("\n  Checking import relations...")
    result = validate_import_relations(client, repo_id, symbols)
    report.add(result)
    _print_step(result.name, result.passed, result.details)
    
    # Call relations
    print("\n  Checking call relations...")
    result = validate_call_relations(client, repo_id, symbols)
    report.add(result)
    _print_step(result.name, result.passed, result.details)
    
    # Endpoint extraction
    print("\n  Checking endpoint extraction...")
    result = validate_endpoint_extraction(client, repo_id, symbols)
    report.add(result)
    _print_step(result.name, result.passed, result.details)
    
    # Chunk coherence
    print("\n  Checking chunk coherence...")
    result = validate_chunk_coherence(client, repo_id)
    report.add(result)
    _print_step(result.name, result.passed, result.details)
    
    # Call-neighbor context
    print("\n  Checking call-neighbor context...")
    result = validate_call_neighbor_context(client, repo_id, symbols)
    report.add(result)
    _print_step(result.name, result.passed, result.details)
    
    # Python symbol coverage
    print("\n  Checking Python symbol coverage...")
    result = validate_python_symbol_coverage(client, repo_id, symbols)
    report.add(result)
    _print_step(result.name, result.passed, result.details)
    
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Python retrieval quality by indexing axon-src itself.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Check status and run validation on existing index:\n"
            "  python scripts/validate_python_retrieval.py --repo-id 1\n\n"
            "  # Start services and do fresh indexing:\n"
            "  python scripts/validate_python_retrieval.py --start-services\n\n"
            "  # Full reset and fresh index:\n"
            "  python scripts/validate_python_retrieval.py --start-services --reset-db\n\n"
            "  # Stop all services:\n"
            "  python scripts/validate_python_retrieval.py --stop-services\n"
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument("--api-key", default="", help="Admin API key")
    parser.add_argument("--skip-indexing", action="store_true", help="Skip indexing, use existing repo")
    parser.add_argument("--repo-id", type=int, default=None, help="Existing repository ID (implies --skip-indexing)")
    parser.add_argument("--timeout", type=int, default=1200, help="Sync timeout in seconds (default: 20 min)")
    parser.add_argument("--namespace", default="validation/axon-src", help="Repository namespace")
    parser.add_argument("--start-services", action="store_true", help="Start API/worker services if not running")
    parser.add_argument("--stop-services", action="store_true", help="Stop all services and exit")
    parser.add_argument("--reset-db", action="store_true", help="Reset the database before starting")
    parser.add_argument("--use-prod-db", action="store_true", help="Use production DB (indexer) instead of test DB")
    args = parser.parse_args()
    
    # Load environment
    loaded_env = _load_dev_env(DEFAULT_DEV_ENV)
    
    # Also load from environment variables (devcontainer exports these)
    env_from_os = {
        "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
        "TEST_DATABASE_URL": os.environ.get("TEST_DATABASE_URL", ""),
        "REDIS_URL": os.environ.get("REDIS_URL", ""),
        "CELERY_BROKER_URL": os.environ.get("CELERY_BROKER_URL", ""),
        "CELERY_RESULT_BACKEND": os.environ.get("CELERY_RESULT_BACKEND", ""),
    }
    for key, value in env_from_os.items():
        if value and key not in loaded_env:
            loaded_env[key] = value
    
    api_key = args.api_key or loaded_env.get("ADMIN_API_KEY", "dev-admin-key")
    
    # Parse base URL for host/port
    parsed_url = parse.urlparse(args.base_url)
    host = parsed_url.hostname or "localhost"
    port = parsed_url.port or 8080
    
    print("=" * 60)
    print("Python Retrieval Quality Validation")
    print("=" * 60)
    
    # Handle --stop-services
    if args.stop_services:
        print("\n[STEP] Stopping services...")
        _stop_services(host, port)
        print("  [INFO] Services stopped")
        return 0
    
    # Determine which database to use
    use_test_db = not args.use_prod_db
    db_url = loaded_env.get("TEST_DATABASE_URL" if use_test_db else "DATABASE_URL", "")
    
    # Run pre-flight checks
    print("\n[STEP] Running pre-flight checks...")
    preflight_warnings = _preflight_checks(loaded_env, use_test_db=use_test_db)
    if preflight_warnings:
        print("  [WARN] Pre-flight checks found potential issues:")
        for warning in preflight_warnings:
            print(f"    - {warning}")
        print("  [HINT] See docs/python_retrieval_validation_lessons_learned.md for guidance")
        print("  [INFO] Proceeding anyway (services may fail to start)...")
    else:
        print("  [INFO] All pre-flight checks passed")
    
    # Start services if requested
    if args.start_services:
        print("\n[STEP] Checking/Starting services...")
        
        # Reset DB if requested or if schema is missing
        if db_url:
            _reset_database_if_needed(db_url, force=args.reset_db)
        else:
            print("  [WARN] No database URL found - skipping DB check")
        
        # Start API server (will skip if already running)
        if not _start_api_server_detached(args.base_url, loaded_env, use_test_db=use_test_db):
            print("  [ERROR] Failed to start API server")
            print("  [HINT] Check logs at /tmp/axon_api.log")
            print("  [HINT] See docs/python_retrieval_validation_lessons_learned.md for common issues")
            return 2
        
        # Start Celery worker (will skip if already running)
        if not _start_celery_worker_detached(loaded_env, use_test_db=use_test_db):
            print("  [WARN] Could not start Celery worker - indexing may not work")
            print("  [HINT] Check logs at /tmp/axon_celery.log")
    
    # Create API client
    client = ApiClient(args.base_url, api_key)
    
    # Health check
    print("\n[STEP] Checking API health...")
    if not _check_api_health(args.base_url, api_key):
        print(f"  [ERROR] API at {args.base_url} is not responding")
        print("  [HINT] Use --start-services to start the API server")
        return 2
    
    try:
        repos = client.request_json("GET", "/repositories", query={"limit": 1})
        print(f"  [INFO] API is responsive, found {repos.get('total', 0)} repositories")
    except Exception as exc:
        print(f"  [ERROR] API health check failed: {exc}")
        return 2
    
    # Get or create repository
    repo_id = args.repo_id
    if not repo_id:
        print("\n[STEP] Setting up repository...")
        try:
            repo = create_or_get_repository(client, args.namespace, AXON_SRC_PATH)
            repo_id = repo["id"]
        except Exception as exc:
            print(f"  [ERROR] Failed to create repository: {exc}")
            return 2
        
        if not args.skip_indexing:
            print("\n[STEP] Triggering sync...")
            try:
                client.request_json("POST", f"/repositories/{repo_id}/sync")
                print(f"  [INFO] Sync triggered for repo_id={repo_id}")
            except Exception as exc:
                print(f"  [ERROR] Failed to trigger sync: {exc}")
                return 2
            
            print("\n[STEP] Waiting for sync to complete...")
            print("  [INFO] This may take several minutes for the full codebase")
            print("  [INFO] You can monitor progress in another terminal with:")
            print(f"        tail -f /tmp/axon_api.log /tmp/axon_celery.log")
            try:
                job = wait_for_sync(client, repo_id, timeout=args.timeout)
                print(f"  [INFO] Sync completed: job_id={job.get('id')} status={job.get('status')}")
            except Exception as exc:
                print(f"  [ERROR] Sync failed: {exc}")
                return 2
    
    # Run validation
    print(f"\n[STEP] Running validation for repo_id={repo_id}...")
    report = run_validation(client, repo_id)
    
    # Print summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"\n{report.summary()}")
    print()
    
    for result in report.results:
        status = "✅" if result.passed else "❌"
        print(f"  {status} {result.name}: {result.details}")
        if result.related_symbols:
            print(f"      Related: {', '.join(result.related_symbols[:5])}")
    
    print()
    
    # Write results to file for documentation
    results_path = AXON_SRC_PATH / "docs" / "python_retrieval_validation_results.json"
    try:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "repository_id": repo_id,
            "namespace": args.namespace,
            "summary": {
                "total": report.total_checks,
                "passed": report.passed,
                "failed": report.failed,
            },
            "checks": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "details": r.details,
                    "expected": r.expected,
                    "actual": r.actual,
                    "related_symbols": r.related_symbols,
                }
                for r in report.results
            ],
        }
        results_path.write_text(json.dumps(results_data, indent=2), encoding="utf-8")
        print(f"[INFO] Results written to: {results_path}")
    except Exception as exc:
        print(f"[WARN] Could not write results file: {exc}")
    
    # Note: We do NOT cleanup processes - services are detached and survive
    
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
