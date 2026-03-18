#!/usr/bin/env python3
"""Smoke-test the currently implemented retrieval surface against a running API."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional
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
            with request.urlopen(req, timeout=30) as resp:
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
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")


def _extract_text(response: Dict[str, Any]) -> str:
    content = response.get("content", [])
    if not content:
        return ""
    return "\n".join(item.get("text", "") for item in content if item.get("type") == "text")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test current retrieval behavior.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument("--api-key", default="", help="Admin API key")
    parser.add_argument("--query", default="authentication", help="Query used for search checks")
    parser.add_argument("--repo-id", type=int, default=None, help="Repository ID for repo-scoped checks")
    parser.add_argument("--limit", type=int, default=5, help="Max results for search checks")
    args = parser.parse_args()

    loaded_env = _load_dev_env(DEFAULT_DEV_ENV)
    api_key = args.api_key or loaded_env.get("ADMIN_API_KEY", "dev-admin-key")
    client = ApiClient(args.base_url, api_key)

    failures = 0

    try:
        health = client.request_json("GET", "/health")
        _print_step("health", True, f"status={health.get('status', 'ok')}")
    except Exception as exc:
        _print_step("health", False, str(exc))
        return 2

    repo_id = args.repo_id
    try:
        repos = client.request_json("GET", "/repositories", query={"skip": 0, "limit": 5})
        items = repos.get("items", [])
        if items:
            chosen = next((item for item in items if item.get("id") == repo_id), None)
            if repo_id is None:
                chosen = items[0]
                repo_id = chosen.get("id")
            _print_step(
                "repositories",
                True,
                f"count={len(items)} using_repo_id={repo_id}",
            )
        else:
            _print_step("repositories", True, "no repositories returned")
    except Exception as exc:
        failures += 1
        _print_step("repositories", False, str(exc))

    first_symbol_id = None
    try:
        search_results = client.request_json(
            "GET",
            "/search",
            query={
                "query": args.query,
                "limit": args.limit,
                "repository_id": repo_id,
                "hybrid": "true",
            },
        )
        result_count = len(search_results)
        if search_results:
            first_symbol_id = search_results[0].get("symbol_id")
        _print_step(
            "rest_search",
            True,
            f"query={args.query!r} results={result_count} first_symbol_id={first_symbol_id}",
        )
    except Exception as exc:
        failures += 1
        _print_step("rest_search", False, str(exc))

    try:
        mcp_search = client.request_json(
            "POST",
            "/mcp/tools/search_code",
            payload={"query": args.query, "limit": args.limit},
        )
        text = _extract_text(mcp_search)
        _print_step(
            "mcp_search_code",
            not mcp_search.get("isError", False),
            f"text_chars={len(text)}",
        )
        if mcp_search.get("isError", False):
            failures += 1
    except Exception as exc:
        failures += 1
        _print_step("mcp_search_code", False, str(exc))

    if first_symbol_id is not None:
        try:
            symbol_ctx = client.request_json(
                "POST",
                "/mcp/tools/get_symbol_context",
                payload={"symbol_id": first_symbol_id, "include_relationships": True},
            )
            text = _extract_text(symbol_ctx)
            _print_step(
                "mcp_get_symbol_context",
                not symbol_ctx.get("isError", False),
                f"symbol_id={first_symbol_id} text_chars={len(text)}",
            )
            if symbol_ctx.get("isError", False):
                failures += 1
        except Exception as exc:
            failures += 1
            _print_step("mcp_get_symbol_context", False, str(exc))
    else:
        _print_step("mcp_get_symbol_context", True, "skipped because no search result symbol_id was available")

    if repo_id is not None:
        try:
            project_map = client.request_json(
                "POST",
                "/mcp/tools/call",
                payload={
                    "name": "get_project_map",
                    "arguments": {"repository_id": repo_id, "max_depth": 2},
                },
            )
            text = _extract_text(project_map)
            _print_step(
                "mcp_get_project_map",
                not project_map.get("isError", False),
                f"repo_id={repo_id} text_chars={len(text)}",
            )
            if project_map.get("isError", False):
                failures += 1
        except Exception as exc:
            failures += 1
            _print_step("mcp_get_project_map", False, str(exc))
    else:
        _print_step("mcp_get_project_map", True, "skipped because no repository_id was available")

    if failures:
        print(f"\nretrieval_smoke=failed failures={failures}")
        return 1

    print("\nretrieval_smoke=passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
