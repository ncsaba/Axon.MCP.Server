# Security Defaults Migration Notes (2026-02-26)

This release hardens default runtime settings for safer out-of-the-box behavior.

## Changed defaults

1. `API_CORS_ORIGINS`
   - **Before:** `["*"]`
   - **Now:** `["http://localhost:3000", "http://127.0.0.1:3000"]`
   - **Why:** Browsers reject `Access-Control-Allow-Origin: *` when credentials are enabled. Explicit origins are required for cookie/header auth.

2. `MCP_AUTH_ENABLED`
   - **Before:** `false`
   - **Now:** `true`
   - **Why:** Prevent accidental unauthenticated MCP HTTP deployments.

## Runtime behavior changes

- CORS middleware now automatically disables credentialed CORS when `API_CORS_ORIGINS` contains `"*"`.
- Startup logs now emit warnings when:
  - wildcard CORS is configured (credentialed browser requests disabled), or
  - MCP HTTP transport is enabled while `MCP_AUTH_ENABLED=false`.

## Required operator actions

If your deployment depends on previous behavior, set explicit overrides:

- Legacy broad CORS (not recommended):
  - `API_CORS_ORIGINS=["*"]`
  - Note: Browser credentialed requests will still not work with wildcard origin by spec.

- Unauthenticated MCP HTTP for local/trusted use only:
  - `MCP_AUTH_ENABLED=false`

## Safe onboarding checklist

1. Keep `MCP_AUTH_ENABLED=true` for any non-local environment.
2. Configure `API_CORS_ORIGINS` to exact frontend origins (scheme + host + port).
