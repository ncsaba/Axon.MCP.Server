#!/usr/bin/env python3
"""Local indexing CLI for repository create/sync/monitor/query operations.

This script is intended for local development against the running Axon API.
It loads defaults from scripts/dev_env.sh so API auth/env values stay aligned
with scripts/start_api.sh and scripts/start_worker.sh.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict
from urllib import error, parse, request


DEFAULT_BASE_URL = "http://localhost:8080/api/v1"
DEFAULT_DEV_ENV = Path(__file__).resolve().parent / "dev_env.sh"


def _load_dev_env(path: Path) -> Dict[str, str]:
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


class ApiClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: Dict[str, Any] | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            encoded = parse.urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
            url = f"{url}?{encoded}"

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        req = request.Request(url, method=method.upper(), data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc.fp else ""
            detail = body
            try:
                parsed = json.loads(body)
                detail = parsed.get("detail", parsed)
            except Exception:
                pass
            raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} - {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"{method} {path} failed: {exc}") from exc


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=False, default=str))


def _resolve_api_key(args: argparse.Namespace, loaded_env: Dict[str, str]) -> str:
    return args.api_key or loaded_env.get("ADMIN_API_KEY", "dev-admin-key")


def _resolve_base_url(args: argparse.Namespace) -> str:
    return args.base_url or DEFAULT_BASE_URL


def _cmd_repos(client: ApiClient, args: argparse.Namespace) -> int:
    data = client.request_json("GET", "/repositories", query={"skip": args.skip, "limit": args.limit})
    _print_json(data)
    return 0


def _cmd_create(client: ApiClient, args: argparse.Namespace) -> int:
    repo_path = Path(args.path).expanduser().resolve()
    payload = {
        "provider": "GITLAB",
        "name": args.name,
        "path_with_namespace": args.namespace,
        "url": f"file://{repo_path}",
        "clone_url": f"file://{repo_path}",
        "default_branch": args.branch,
    }
    data = client.request_json("POST", "/repositories", payload=payload)
    _print_json(data)
    return 0


def _cmd_sync(client: ApiClient, args: argparse.Namespace) -> int:
    data = client.request_json("POST", f"/repositories/{args.repo_id}/sync")
    _print_json(data)
    return 0


def _cmd_status(client: ApiClient, args: argparse.Namespace) -> int:
    repo = client.request_json("GET", f"/repositories/{args.repo_id}")
    history = client.request_json(
        "GET",
        f"/repositories/{args.repo_id}/sync-history",
        query={"limit": 1, "offset": 0},
    )
    out = {
        "repository": repo,
        "latest_job": history.get("items", [None])[0] if history.get("items") else None,
    }
    _print_json(out)
    return 0


def _cmd_jobs(client: ApiClient, args: argparse.Namespace) -> int:
    query = {"offset": args.offset, "limit": args.limit, "status": args.status}
    data = client.request_json("GET", "/jobs", query=query)
    _print_json(data)
    return 0


def _cmd_wait(client: ApiClient, args: argparse.Namespace) -> int:
    start = time.time()
    while True:
        history = client.request_json(
            "GET",
            f"/repositories/{args.repo_id}/sync-history",
            query={"limit": 1, "offset": 0},
        )
        items = history.get("items", [])
        if items:
            latest = items[0]
            status = latest.get("status")
            print(f"job={latest.get('id')} status={status} retries={latest.get('retry_count')}")
            if status == "COMPLETED":
                return 0
            if status in {"FAILED", "CANCELLED"}:
                return 2
        else:
            print("No sync job yet, waiting...")

        if time.time() - start > args.timeout:
            print(f"Timed out after {args.timeout}s")
            return 3

        time.sleep(args.interval)


def _cmd_stats(client: ApiClient, args: argparse.Namespace) -> int:
    data = client.request_json("GET", f"/repositories/{args.repo_id}/stats")
    _print_json(data)
    return 0


def _cmd_symbols(client: ApiClient, args: argparse.Namespace) -> int:
    query = {"repository_id": args.repo_id, "skip": args.skip, "limit": args.limit}
    data = client.request_json("GET", "/symbols", query=query)
    _print_json(data)
    return 0


def _cmd_search(client: ApiClient, args: argparse.Namespace) -> int:
    query = {"query": args.query, "limit": args.limit, "repos": args.repo_id}
    data = client.request_json("GET", "/search", query=query)
    _print_json(data)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local Axon indexing helper CLI.",
        epilog=(
            "Examples:\n"
            "  python scripts/local_indexer.py create --name domeus-core --path /workspaces/axon-mcp/domeus-core --namespace local/domeus-core\n"
            "  python scripts/local_indexer.py wait --repo-id 1\n"
            "  python scripts/local_indexer.py stats --repo-id 1\n"
            "  python scripts/local_indexer.py search --repo-id 1 --query 'spring controller' --limit 20"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL (default: %(default)s)")
    parser.add_argument("--api-key", default="", help="Admin API key (default: from scripts/dev_env.sh)")

    sub = parser.add_subparsers(dest="command", required=True, help="Operation to run")

    p_repos = sub.add_parser("repos", help="List repositories")
    p_repos.add_argument("--skip", type=int, default=0)
    p_repos.add_argument("--limit", type=int, default=100)

    p_create = sub.add_parser("create", help="Create local-directory repository and auto-start indexing")
    p_create.add_argument("--name", required=True, help="Repository display name")
    p_create.add_argument("--path", required=True, help="Local folder path to index")
    p_create.add_argument("--namespace", required=True, help="Stable logical namespace key (e.g., local/domeus-core)")
    p_create.add_argument("--branch", default="main", help="Default branch label stored in metadata")

    p_sync = sub.add_parser("sync", help="Trigger sync for an existing repository")
    p_sync.add_argument("--repo-id", type=int, required=True)

    p_status = sub.add_parser("status", help="Show repository status + latest sync job")
    p_status.add_argument("--repo-id", type=int, required=True)

    p_jobs = sub.add_parser("jobs", help="List jobs")
    p_jobs.add_argument("--status", default="", help="Optional status filter (PENDING/RUNNING/COMPLETED/FAILED/...)")
    p_jobs.add_argument("--offset", type=int, default=0)
    p_jobs.add_argument("--limit", type=int, default=50)

    p_wait = sub.add_parser("wait", help="Wait until latest repository sync job completes/fails")
    p_wait.add_argument("--repo-id", type=int, required=True)
    p_wait.add_argument("--timeout", type=int, default=3600, help="Max wait seconds")
    p_wait.add_argument("--interval", type=int, default=5, help="Polling interval seconds")

    p_stats = sub.add_parser("stats", help="Get repository stats")
    p_stats.add_argument("--repo-id", type=int, required=True)

    p_symbols = sub.add_parser("symbols", help="List symbols for repository")
    p_symbols.add_argument("--repo-id", type=int, required=True)
    p_symbols.add_argument("--skip", type=int, default=0)
    p_symbols.add_argument("--limit", type=int, default=50)

    p_search = sub.add_parser("search", help="Run search constrained to repository")
    p_search.add_argument("--repo-id", type=int, required=True)
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=20)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    loaded_env = _load_dev_env(DEFAULT_DEV_ENV)
    client = ApiClient(_resolve_base_url(args), _resolve_api_key(args, loaded_env))

    handlers = {
        "repos": _cmd_repos,
        "create": _cmd_create,
        "sync": _cmd_sync,
        "status": _cmd_status,
        "jobs": _cmd_jobs,
        "wait": _cmd_wait,
        "stats": _cmd_stats,
        "symbols": _cmd_symbols,
        "search": _cmd_search,
    }
    try:
        return handlers[args.command](client, args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
