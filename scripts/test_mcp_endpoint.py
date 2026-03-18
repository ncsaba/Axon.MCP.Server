#!/usr/bin/env python3
"""Test script for MCP HTTP endpoint."""

import ast
import json
import sys
from pathlib import Path
from typing import Dict, Any
from urllib import error, request


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


def _request_json(url: str, *, payload: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None, timeout: int = 10) -> tuple[int, Dict[str, Any] | str]:
    data = None
    req_headers = headers.copy() if headers else {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url, data=data, headers=req_headers)

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.getcode(), json.loads(body) if body else {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return exc.code, parsed


def _request_json_with_headers(
    url: str,
    *,
    payload: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
    timeout: int = 10,
) -> tuple[int, Dict[str, Any] | str, Dict[str, str]]:
    data = None
    req_headers = headers.copy() if headers else {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url, data=data, headers=req_headers)

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return resp.getcode(), parsed, dict(resp.headers.items())
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return exc.code, parsed, dict(exc.headers.items()) if exc.headers else {}


def test_mcp_endpoint(base_url: str = "http://localhost:8001", api_key: str = "dev-admin-key") -> bool:
    """Test MCP HTTP endpoint functionality."""
    
    print(f"Testing MCP HTTP endpoint at {base_url}")
    print("=" * 50)
    
    mcp_url = f"{base_url}/mcp"
    headers = {"X-API-Key": api_key}
    
    # Test 1: Initialize
    print("1. Testing initialize...")
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {
                "name": "test-client",
                "version": "1.0.0"
            }
        }
    }
    
    try:
        status_code, result, response_headers = _request_json_with_headers(
            mcp_url,
            payload=init_request,
            headers=headers,
            timeout=10,
        )
        if status_code == 200:
            if "result" in result:
                print("   ✅ Initialize successful")
                print(f"   📋 Server: {result['result']['serverInfo']['name']}")
                session_id = response_headers.get("mcp-session-id")
            else:
                print(f"   ❌ Initialize failed: {result}")
                return False
        else:
            print(f"   ❌ HTTP error: {status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Request failed: {e}")
        return False

    if not session_id:
        print("   ❌ Initialize did not return an mcp-session-id header")
        return False
    
    # Test 2: List tools
    print("2️⃣  Testing tools/list...")
    list_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    
    try:
        status_code, result = _request_json(
            mcp_url,
            payload=list_request,
            headers={**headers, "mcp-session-id": session_id},
            timeout=10,
        )
        if status_code == 200:
            if "result" in result and "tools" in result["result"]:
                tools = result["result"]["tools"]
                print(f"   ✅ Found {len(tools)} tools")
                for tool in tools:
                    print(f"      🔧 {tool['name']}: {tool['description']}")
            else:
                print(f"   ❌ List tools failed: {result}")
                return False
        else:
            print(f"   ❌ HTTP error: {status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Request failed: {e}")
        return False
    
    # Test 3: Call a tool (search_code)
    print("3️⃣  Testing tools/call (search_code)...")
    call_request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "search_code",
            "arguments": {
                "query": "test",
                "limit": 5
            }
        }
    }
    
    try:
        status_code, result = _request_json(
            mcp_url,
            payload=call_request,
            headers={**headers, "mcp-session-id": session_id},
            timeout=30,
        )
        if status_code == 200:
            if "result" in result:
                print("   ✅ Tool call successful")
                content = result["result"].get("content", [])
                print(f"   📄 Response content length: {len(content)} items")
            else:
                print(f"   ❌ Tool call failed: {result}")
                return False
        else:
            print(f"   ❌ HTTP error: {status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Request failed: {e}")
        return False
    
    print("")
    print("🎉 All tests passed! MCP HTTP endpoint is working correctly.")
    return True


def test_health_endpoint(base_url: str = "http://localhost:8001") -> bool:
    """Test health endpoint."""
    print(f"🏥 Testing health endpoint at {base_url}")
    
    try:
        status_code, _ = _request_json(f"{base_url}/api/v1/health", timeout=10)
        if status_code == 200:
            print("   ✅ Health endpoint is working")
            return True
        else:
            print(f"   ❌ Health endpoint failed: {status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Health check failed: {e}")
        return False


def main():
    """Main test function."""
    env = _load_dev_env(DEFAULT_DEV_ENV)
    api_key = env.get("ADMIN_API_KEY", "dev-admin-key")

    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = "http://localhost:8001"
    
    print("MCP Server HTTP Transport Test")
    print("=" * 40)
    print(f"Target URL: {base_url}")
    print("")
    
    # Test health first
    if not test_health_endpoint(base_url):
        print("❌ Health check failed. Is the server running?")
        sys.exit(1)
    
    print("")
    
    # Test MCP functionality
    if test_mcp_endpoint(base_url, api_key=api_key):
        print("✅ All tests completed successfully!")
        print("")
        print("🤖 Your MCP server is ready for AI integration!")
        print(f"   Use this URL: {base_url}/mcp")
        print(f"   API key: {api_key}")
        sys.exit(0)
    else:
        print("❌ Some tests failed. Check the server logs.")
        sys.exit(1)


if __name__ == "__main__":
    main()
