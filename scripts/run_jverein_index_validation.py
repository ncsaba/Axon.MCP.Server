#!/usr/bin/env python3
"""Start local indexing services, run jverein indexing, and report results.

This script is intended as a repeatable validation harness for the current
streaming indexing path. It can:

1. start the local API and queue workers if needed
2. verify they are reachable
3. create or reuse the jverein repository entry
4. trigger indexing and wait for completion
5. capture repository/job stats plus Prometheus metric deltas for the run
"""

from __future__ import annotations

import argparse
import ast
import atexit
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

import redis


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.local_indexer import ApiClient, DEFAULT_BASE_URL, DEFAULT_DEV_ENV


VENV_PYTHON = "/home/vscode/.venv-dev/bin/python"
VENV_CELERY = "/home/vscode/.venv-dev/bin/celery"


@dataclass
class ManagedProcess:
    name: str
    popen: subprocess.Popen[Any]
    log_path: Path
    started_by_script: bool


class HarnessInterrupted(RuntimeError):
    """Raised when the validation harness receives a termination signal."""


def _load_dev_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
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


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_load_dev_env(DEFAULT_DEV_ENV))
    env["PYTHONPATH"] = str(ROOT_DIR)
    return env


def _prepare_prometheus_multiproc_dir(base_dir: Path) -> Path:
    metrics_dir = base_dir / "prometheus-multiproc"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    for stale in metrics_dir.glob("*.db"):
        stale.unlink()
    return metrics_dir


def _health_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api/v1"):
        return normalized + "/health"
    if normalized.endswith("/api"):
        return normalized + "/v1/health"
    if "/api/v1/" in normalized:
        return normalized.split("/api/v1/", 1)[0] + "/api/v1/health"
    if "/api/" in normalized:
        return normalized.split("/api/", 1)[0] + "/api/v1/health"
    return normalized + "/api/v1/health"


def _metrics_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api/v1"):
        return normalized + "/metrics"
    if normalized.endswith("/api"):
        return normalized + "/v1/metrics"
    if "/api/v1/" in normalized:
        return normalized.split("/api/v1/", 1)[0] + "/api/v1/metrics"
    if "/api/" in normalized:
        return normalized.split("/api/", 1)[0] + "/api/v1/metrics"
    return normalized + "/api/v1/metrics"


def _request_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 10) -> dict[str, Any]:
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def _request_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 10) -> str:
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str), encoding="utf-8")
    tmp_path.replace(path)


def _safe_request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    try:
        return _request_json(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return {"_request_error": str(exc), "_url": url}


def _safe_request_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> str:
    try:
        return _request_text(url, headers=headers, timeout=timeout)
    except Exception:
        return ""


def _trigger_task_snapshot(task_id: str | None) -> dict[str, Any]:
    if not task_id:
        return {}
    from celery.result import AsyncResult
    from src.workers.celery_app import celery_app

    task_result = AsyncResult(task_id, app=celery_app)
    payload: dict[str, Any] = {"trigger_task_id": task_id, "trigger_task_state": str(task_result.state)}
    result = task_result.result
    if isinstance(result, dict):
        payload["trigger_task_result"] = result
        if result.get("status") is not None:
            payload["trigger_task_status"] = str(result.get("status"))
        if result.get("reason") is not None:
            payload["trigger_task_reason"] = str(result.get("reason"))
    elif task_result.state in {"SUCCESS", "FAILURE", "REVOKED"} and result is not None:
        payload["trigger_task_result"] = str(result)[:1000]
    return payload


def _repository_lock_snapshot(redis_url: str, repository_id: int | None) -> dict[str, Any]:
    if repository_id is None:
        return {}

    lock_key = f"lock:repository:{repository_id}"
    client = None
    try:
        client = redis.from_url(redis_url, decode_responses=True)
        value = client.get(lock_key)
        ttl_seconds = int(client.ttl(lock_key))
        return {
            "lock_key": lock_key,
            "exists": value is not None or ttl_seconds >= 0,
            "token": value,
            "ttl_seconds": ttl_seconds,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "lock_key": lock_key,
            "exists": None,
            "error": str(exc),
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _wait_for_http_health(url: str, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            payload = _request_json(url, timeout=5)
            if payload.get("status") == "healthy":
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for API health at {url}")


def _spawn_process(
    name: str,
    argv: list[str],
    *,
    env: dict[str, str],
    log_dir: Path,
) -> ManagedProcess:
    log_path = log_dir / f"{name}.log"
    handle = log_path.open("w", encoding="utf-8")
    popen = subprocess.Popen(
        argv,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return ManagedProcess(name=name, popen=popen, log_path=log_path, started_by_script=True)


def _terminate_processes(processes: list[ManagedProcess]) -> None:
    for managed in reversed(processes):
        if not managed.started_by_script:
            continue
        if managed.popen.poll() is not None:
            continue
        managed.popen.terminate()
        try:
            managed.popen.wait(timeout=10)
        except subprocess.TimeoutExpired:
            managed.popen.kill()
            managed.popen.wait(timeout=5)


def _inspect_ping(
    worker_name: str,
    *,
    env: dict[str, str],
) -> bool:
    result = subprocess.run(
        [
            VENV_CELERY,
            "-A",
            "src.workers.celery_app.celery_app",
            "inspect",
            "ping",
            "--timeout=2",
            "-d",
            worker_name,
        ],
        cwd=str(ROOT_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "pong" in output.lower()


def _wait_for_worker_ping(worker_name: str, *, env: dict[str, str], timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _inspect_ping(worker_name, env=env):
            return
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for worker ping: {worker_name}")


def _find_repository(client: ApiClient, namespace: str) -> dict[str, Any] | None:
    offset = 0
    limit = 100
    while True:
        payload = client.request_json("GET", "/repositories", query={"skip": offset, "limit": limit})
        items = payload.get("items", [])
        for item in items:
            if item.get("path_with_namespace") == namespace:
                return item
        offset += len(items)
        if not items or offset >= int(payload.get("total", 0) or 0):
            return None


def _latest_sync_job(client: ApiClient, repo_id: int) -> dict[str, Any] | None:
    payload = client.request_json(
        "GET",
        f"/repositories/{repo_id}/sync-history",
        query={"limit": 1, "offset": 0},
    )
    items = payload.get("items", [])
    return items[0] if items else None


def _wait_for_new_sync_job(
    client: ApiClient,
    repo_id: int,
    previous_job_id: int | None,
    *,
    trigger_task_id: str | None = None,
    timeout_seconds: int,
    interval_seconds: int,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            raise HarnessInterrupted(f"Interrupted while waiting for new sync job for repository {repo_id}")
        latest = _latest_sync_job(client, repo_id)
        if latest and int(latest["id"]) != int(previous_job_id or -1):
            if heartbeat is not None:
                heartbeat(
                    {
                        "status": "job_discovered",
                        "job": latest,
                        "trigger_task_id": trigger_task_id,
                    }
                )
            return latest
        if heartbeat is not None:
            payload: dict[str, Any] = {
                "status": "waiting_for_job_record",
                "trigger_task_id": trigger_task_id,
                "latest_sync_history": latest,
            }
            payload.update(_trigger_task_snapshot(trigger_task_id))
            heartbeat(payload)
            if payload.get("trigger_task_state") == "SUCCESS" and payload.get("trigger_task_status") == "skipped":
                return {
                    "status": "skipped",
                    "reason": payload.get("trigger_task_reason"),
                    "trigger_task": payload,
                }
        time.sleep(interval_seconds)
    details = [f"Timed out waiting for new sync job for repository {repo_id}"]
    if trigger_task_id:
        task_snapshot = _trigger_task_snapshot(trigger_task_id)
        details.append(f"trigger_task_id={trigger_task_id}")
        if task_snapshot.get("trigger_task_state"):
            details.append(f"trigger_task_state={task_snapshot['trigger_task_state']}")
        if task_snapshot.get("trigger_task_result"):
            details.append(f"trigger_task_result={task_snapshot['trigger_task_result']}")
    raise TimeoutError(" | ".join(details))


def _wait_for_job_completion(
    client: ApiClient,
    job_id: int,
    *,
    timeout_seconds: int,
    interval_seconds: int,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            raise HarnessInterrupted(f"Interrupted while waiting for job {job_id}")
        job = client.request_json("GET", f"/jobs/{job_id}")
        status = str(job.get("status"))
        if heartbeat is not None:
            heartbeat(job)
        display_duration = _display_duration_seconds(job)
        print(
            f"job={job_id} status={status} retries={job.get('retry_count')} duration={display_duration}",
            flush=True,
        )
        if status == "COMPLETED":
            return job
        if status in {"FAILED", "CANCELLED"}:
            return job
        time.sleep(interval_seconds)
    raise TimeoutError(f"Timed out waiting for job {job_id}")


_METRIC_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)$"
)


def _parse_metrics(text: str, prefixes: tuple[str, ...]) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _METRIC_LINE_RE.match(stripped)
        if not match:
            continue
        name = match.group("name")
        if not name.startswith(prefixes):
            continue
        labels = match.group("labels") or ""
        key = f"{name}{labels}"
        values[key] = float(match.group("value"))
    return values


def _metric_deltas(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    keys = sorted(set(before) | set(after))
    deltas: dict[str, float] = {}
    for key in keys:
        delta = after.get(key, 0.0) - before.get(key, 0.0)
        if abs(delta) > 1e-12:
            deltas[key] = delta
    return deltas


def _display_duration_seconds(job: dict[str, Any]) -> float | int | None:
    duration = job.get("duration_seconds")
    if duration is not None:
        return duration

    started_at = job.get("started_at")
    if not started_at:
        return None

    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return None

    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return round((datetime.now(UTC) - started.astimezone(UTC)).total_seconds(), 1)


def _write_performance_report(report_path: Path, summary: dict[str, Any]) -> None:
    """Generate a markdown performance report from the validation summary."""
    lines: list[str] = []
    
    # Header
    lines.append("# Pipeline Performance Report")
    lines.append("")
    lines.append(f"**Generated**: {summary.get('completed_at', summary.get('updated_at', 'N/A'))}")
    lines.append(f"**Status**: {summary.get('status', 'unknown')}")
    lines.append(f"**Log Directory**: `{summary.get('log_dir', 'N/A')}`")
    lines.append("")
    
    # Repository info
    repo = summary.get("repository", {})
    lines.append("## Repository")
    lines.append("")
    lines.append(f"- **ID**: {repo.get('id', 'N/A')}")
    lines.append(f"- **Name**: {repo.get('name', 'N/A')}")
    lines.append(f"- **Namespace**: {repo.get('namespace', 'N/A')}")
    lines.append(f"- **Path**: `{repo.get('path', 'N/A')}`")
    lines.append("")
    
    # Job info
    job = summary.get("job", {})
    lines.append("## Job")
    lines.append("")
    lines.append(f"- **Job ID**: {job.get('id', 'N/A')}")
    lines.append(f"- **Status**: {job.get('status', 'N/A')}")
    lines.append(f"- **Duration**: {job.get('duration_seconds', 'N/A')}s")
    lines.append(f"- **Retry Count**: {job.get('retry_count', 0)}")
    lines.append("")
    
    # Job metadata (pipeline counters)
    job_metadata = job.get("job_metadata", {}) or {}
    if job_metadata:
        lines.append("### Pipeline Counters")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for key, value in sorted(job_metadata.items()):
            lines.append(f"| {key} | {value} |")
        lines.append("")
    
    # Stage timing breakdown from metrics_delta
    metrics_delta = summary.get("metrics_delta", {})
    stage_durations: dict[str, dict[str, float]] = {}
    stage_items: dict[str, dict[str, float]] = {}
    
    for key, value in metrics_delta.items():
        # Parse streaming_stage_duration_seconds_sum{stage="X",mode="Y"}
        if key.startswith("streaming_stage_duration_seconds_sum"):
            # Extract stage name from labels
            match = re.search(r'stage="([^"]+)"', key)
            if match:
                stage = match.group(1)
                if stage not in stage_durations:
                    stage_durations[stage] = {}
                stage_durations[stage]["duration"] = value
        elif key.startswith("streaming_stage_items_total"):
            # Extract stage and result from labels
            stage_match = re.search(r'stage="([^"]+)"', key)
            result_match = re.search(r'result="([^"]+)"', key)
            if stage_match:
                stage = stage_match.group(1)
                result = result_match.group(1) if result_match else "unknown"
                if stage not in stage_items:
                    stage_items[stage] = {}
                stage_items[stage][result] = value
    
    if stage_durations:
        lines.append("## Stage Timing Breakdown")
        lines.append("")
        
        # Calculate total duration for percentage
        total_duration = sum(s.get("duration", 0) for s in stage_durations.values())
        
        lines.append("| Stage | Duration (s) | % of Total | Items Created |")
        lines.append("|-------|--------------|------------|---------------|")
        
        # Sort by duration descending
        for stage, data in sorted(stage_durations.items(), key=lambda x: x[1].get("duration", 0), reverse=True):
            duration = data.get("duration", 0)
            pct = (duration / total_duration * 100) if total_duration > 0 else 0
            items = stage_items.get(stage, {})
            items_str = ", ".join(f"{v:.0f} {k}" for k, v in items.items()) if items else "-"
            lines.append(f"| {stage} | {duration:.3f} | {pct:.1f}% | {items_str} |")
        
        lines.append("")
        lines.append(f"**Total Measured Duration**: {total_duration:.3f}s")
        lines.append("")
    
    # Metadata gate decisions
    gate_decisions = {}
    for key, value in metrics_delta.items():
        if key.startswith("metadata_gate_files_total"):
            match = re.search(r'decision="([^"]+)"', key)
            if match:
                gate_decisions[match.group(1)] = value
    
    if gate_decisions:
        lines.append("## Metadata Gate Decisions")
        lines.append("")
        lines.append("| Decision | Count |")
        lines.append("|----------|-------|")
        for decision, count in sorted(gate_decisions.items()):
            lines.append(f"| {decision} | {count:.0f} |")
        lines.append("")
    
    # Raw metrics delta (collapsed)
    lines.append("## Raw Metrics Delta")
    lines.append("")
    lines.append("<details>")
    lines.append("<summary>Click to expand</summary>")
    lines.append("")
    lines.append("```")
    for key, value in sorted(metrics_delta.items()):
        lines.append(f"{key} {value}")
    lines.append("```")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start local indexing services, run jverein indexing, and report the results.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--repo-path", default="/workspaces/axon-mcp/jverein")
    parser.add_argument("--repo-name", default="jverein")
    parser.add_argument("--namespace", default="local/jverein")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--leave-running", action="store_true")
    parser.add_argument("--output", default="", help="Write final summary JSON to this file path.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = _base_env()
    os.environ.update(env)
    if args.api_key:
        env["ADMIN_API_KEY"] = args.api_key
        os.environ["ADMIN_API_KEY"] = args.api_key

    base_url = args.base_url
    api_key = args.api_key or env.get("ADMIN_API_KEY", "dev-admin-key")
    client = ApiClient(base_url, api_key)
    health_url = _health_url(base_url)
    metrics_url = _metrics_url(base_url)
    repo_path = Path(args.repo_path).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise SystemExit(f"Repository path does not exist or is not a directory: {repo_path}")

    log_dir = Path("/tmp") / f"axon-index-validation-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir = _prepare_prometheus_multiproc_dir(log_dir)
    env["PROMETHEUS_MULTIPROC_DIR"] = str(metrics_dir)
    summary_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else log_dir / "validation-summary.json"
    )

    hostname = socket.gethostname()
    worker_specs = [
        {
            "name": "repository_sync_worker",
            "worker_name": f"axon-repository-sync@{hostname}",
            "queues": "repository_sync",
            "concurrency": "1",
        },
        {
            "name": "file_parsing_worker",
            "worker_name": f"axon-file-parsing@{hostname}",
            "queues": "file_parsing,embeddings,default,ai_enrichment,repository_aggregation",
            "concurrency": "4",
        },
    ]

    managed_processes: list[ManagedProcess] = []
    cleanup_enabled = not args.leave_running
    if cleanup_enabled:
        atexit.register(_terminate_processes, managed_processes)

    summary: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "log_dir": str(log_dir),
        "summary_path": str(summary_path),
        "api": {},
        "workers": [],
        "repository": {},
        "job": {},
        "metrics_delta": {},
        "prometheus_multiproc_dir": str(metrics_dir),
        "status": "starting",
    }
    metrics_before: dict[str, float] = {}
    repo_id: int | None = None
    job_id: int | None = None
    stop_requested = False

    def persist_summary() -> None:
        summary["updated_at"] = datetime.now(UTC).isoformat()
        _write_json_file(summary_path, summary)

    def should_stop() -> bool:
        return stop_requested

    def mark_job_heartbeat(job_payload: dict[str, Any]) -> None:
        summary["job"] = job_payload
        summary["status"] = f"job_{str(job_payload.get('status', 'unknown')).lower()}"
        persist_summary()

    def mark_pre_job_heartbeat(payload: dict[str, Any]) -> None:
        summary["job_discovery"] = payload
        summary["status"] = str(payload.get("status") or "waiting_for_job_record")
        persist_summary()

    def capture_latest_state() -> None:
        nonlocal repo_id, job_id
        headers = {
            "X-API-Key": api_key,
            "Accept": "application/json",
        }
        if repo_id is not None:
            summary["repository"] = {
                **summary.get("repository", {}),
                "detail": _safe_request_json(
                    f"{client.base_url}/repositories/{repo_id}",
                    headers=headers,
                    timeout=15,
                ),
                "stats": _safe_request_json(
                    f"{client.base_url}/repositories/{repo_id}/stats",
                    headers=headers,
                    timeout=15,
                ),
            }
            summary["repository_lock"] = _repository_lock_snapshot(env["REDIS_URL"], repo_id)
        if job_id is not None:
            summary["job"] = _safe_request_json(
                f"{client.base_url}/jobs/{job_id}",
                headers=headers,
                timeout=15,
            )
        metrics_after = _parse_metrics(
            _safe_request_text(metrics_url, headers=headers, timeout=15),
            prefixes=("inventory_", "metadata_gate_", "streaming_stage_"),
        )
        summary["metrics_after"] = metrics_after
        if metrics_before:
            summary["metrics_delta"] = _metric_deltas(metrics_before, metrics_after)
        persist_summary()

    def _handle_signal(signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        summary["status"] = "interrupted"
        summary["interrupted_by_signal"] = signum
        summary["interrupted_at"] = datetime.now(UTC).isoformat()
        persist_summary()

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    persist_summary()

    try:
        api_reused = True
        try:
            _wait_for_http_health(health_url, timeout_seconds=2)
        except Exception:
            api_reused = False
            managed_processes.append(
                _spawn_process(
                    "api",
                    [VENV_PYTHON, "-m", "uvicorn", "src.api.main:app", "--host", env.get("API_HOST", "0.0.0.0"), "--port", env.get("API_PORT", "8080")],
                    env=env,
                    log_dir=log_dir,
                )
            )
            _wait_for_http_health(health_url, timeout_seconds=90)

        summary["api"] = {
            "health_url": health_url,
            "metrics_url": metrics_url,
            "status": "healthy",
            "reused_existing": api_reused,
            "log_path": str(log_dir / "api.log") if not api_reused else None,
        }
        summary["status"] = "api_ready"
        persist_summary()

        for spec in worker_specs:
            reused = _inspect_ping(spec["worker_name"], env=env)
            if not reused:
                managed_processes.append(
                    _spawn_process(
                        spec["name"],
                        [
                            VENV_CELERY,
                            "-A",
                            "src.workers.celery_app.celery_app",
                            "worker",
                            "--loglevel=info",
                            f"--hostname={spec['worker_name']}",
                            f"--queues={spec['queues']}",
                            f"--concurrency={spec['concurrency']}",
                            "--pool=prefork",
                        ],
                        env=env,
                        log_dir=log_dir,
                    )
                )
                _wait_for_worker_ping(spec["worker_name"], env=env, timeout_seconds=90)

            summary["workers"].append(
                {
                    "name": spec["name"],
                    "worker_name": spec["worker_name"],
                    "queues": spec["queues"],
                    "reused_existing": reused,
                    "log_path": str(log_dir / f"{spec['name']}.log") if not reused else None,
                    "status": "ready",
                }
            )
            persist_summary()

        metrics_before = _parse_metrics(
            _request_text(metrics_url, timeout=15),
            prefixes=("inventory_", "metadata_gate_", "streaming_stage_"),
        )
        summary["metrics_before"] = metrics_before
        summary["status"] = "pre_sync_snapshot_captured"
        persist_summary()

        existing_repo = _find_repository(client, args.namespace)
        previous_job_id = None
        repo_created = False

        if existing_repo is not None:
            repo_id = int(existing_repo["id"])
            previous_latest_job = _latest_sync_job(client, repo_id)
            previous_job_id = int(previous_latest_job["id"]) if previous_latest_job else None
            trigger_response = client.request_json("POST", f"/repositories/{repo_id}/sync")
            trigger_mode = "sync_existing"
        else:
            payload = {
                "provider": "GITLAB",
                "name": args.repo_name,
                "path_with_namespace": args.namespace,
                "url": f"file://{repo_path}",
                "clone_url": f"file://{repo_path}",
                "default_branch": args.branch,
            }
            create_response = client.request_json("POST", "/repositories", payload=payload)
            repo_id = int(create_response["id"])
            repo_created = True
            trigger_response = {"status": "queued_from_create", "repository_id": repo_id}
            trigger_mode = "create_and_sync"

        summary["repository"] = {
            "id": repo_id,
            "path": str(repo_path),
            "name": args.repo_name,
            "namespace": args.namespace,
            "created_in_this_run": repo_created,
            "trigger_mode": trigger_mode,
            "trigger_response": trigger_response,
            "previous_job_id": previous_job_id,
        }
        summary["repository_lock"] = _repository_lock_snapshot(env["REDIS_URL"], repo_id)
        summary["status"] = "sync_triggered"
        persist_summary()

        job_stub = _wait_for_new_sync_job(
            client,
            repo_id,
            previous_job_id,
            trigger_task_id=str(trigger_response.get("task_id") or ""),
            timeout_seconds=min(args.timeout, 300),
            interval_seconds=args.interval,
            heartbeat=mark_pre_job_heartbeat,
            should_stop=should_stop,
        )
        if str(job_stub.get("status")) == "skipped":
            summary["job"] = job_stub
            summary["status"] = "skipped_existing_inflight_sync"
            capture_latest_state()
            if args.json:
                print(json.dumps(summary, indent=2, sort_keys=False, default=str), flush=True)
            return 3
        job_id = int(job_stub["id"])
        summary["job"] = job_stub
        summary["status"] = "job_started"
        persist_summary()
        job_detail = _wait_for_job_completion(
            client,
            job_id,
            timeout_seconds=args.timeout,
            interval_seconds=args.interval,
            heartbeat=mark_job_heartbeat,
            should_stop=should_stop,
        )

        repo_detail = client.request_json("GET", f"/repositories/{repo_id}")
        repo_stats = client.request_json("GET", f"/repositories/{repo_id}/stats")
        metrics_after = _parse_metrics(
            _request_text(metrics_url, timeout=15),
            prefixes=("inventory_", "metadata_gate_", "streaming_stage_"),
        )

        summary["repository"] = {
            "id": repo_id,
            "path": str(repo_path),
            "name": args.repo_name,
            "namespace": args.namespace,
            "created_in_this_run": repo_created,
            "trigger_mode": trigger_mode,
            "trigger_response": trigger_response,
            "detail": repo_detail,
            "stats": repo_stats,
        }
        summary["job"] = job_detail
        summary["metrics_after"] = metrics_after
        summary["metrics_delta"] = _metric_deltas(metrics_before, metrics_after)
        summary["repository_lock"] = _repository_lock_snapshot(env["REDIS_URL"], repo_id)
        summary["completed_at"] = datetime.now(UTC).isoformat()
        summary["status"] = "completed" if str(job_detail.get("status")) == "COMPLETED" else "finished_with_job_status"
        persist_summary()

        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=False, default=str), flush=True)
        else:
            print(f"api_status=healthy reused={summary['api']['reused_existing']} health_url={health_url}", flush=True)
            for worker in summary["workers"]:
                print(
                    f"worker={worker['worker_name']} status={worker['status']} "
                    f"reused={worker['reused_existing']} queues={worker['queues']}",
                    flush=True,
                )
            print(
                f"repository_id={repo_id} trigger_mode={trigger_mode} "
                f"created_in_this_run={repo_created}",
                flush=True,
            )
            print(
                f"job_id={job_id} status={job_detail.get('status')} "
                f"duration_seconds={job_detail.get('duration_seconds')} retries={job_detail.get('retry_count')}",
                flush=True,
            )
            print(
                f"repo_total_files={repo_detail.get('total_files')} "
                f"repo_total_symbols={repo_detail.get('total_symbols')} "
                f"repo_status={repo_detail.get('status')}",
                flush=True,
            )
            print(
                f"stats_total_files={repo_stats.get('total_files')} "
                f"stats_total_symbols={repo_stats.get('total_symbols')} "
                f"stats_total_endpoints={repo_stats.get('total_endpoints')}",
                flush=True,
            )
            print("metrics_delta_begin", flush=True)
            for key, value in summary["metrics_delta"].items():
                print(f"{key} {value}", flush=True)
            print("metrics_delta_end", flush=True)
            print(f"log_dir={log_dir}", flush=True)
            
            # Generate markdown report
            report_path = log_dir / "performance-report.md"
            _write_performance_report(report_path, summary)
            print(f"performance_report={report_path}", flush=True)

        if str(job_detail.get("status")) != "COMPLETED":
            return 2
        return 0
    except BaseException as exc:  # noqa: BLE001
        summary["status"] = "error"
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        try:
            capture_latest_state()
        except Exception as capture_exc:  # noqa: BLE001
            summary["state_capture_error"] = str(capture_exc)
            persist_summary()
        raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if cleanup_enabled:
            _terminate_processes(managed_processes)


if __name__ == "__main__":
    raise SystemExit(main())
