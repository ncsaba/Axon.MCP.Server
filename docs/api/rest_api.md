# REST API Reference

## 📋 Overview

The Axon MCP Server provides a comprehensive REST API for programmatic access to code search, repository management, and symbol analysis. This API powers the React Dashboard and can be used to build custom integrations.

**Base URL**: `http://localhost:8080/api/v1`
**OpenAPI Spec**: `http://localhost:8080/api/openapi.json`
**Swagger UI**: `http://localhost:8080/api/docs`

---

## 🔐 Authentication

The API accepts one or more configured authentication methods:

### 1. API Key (Service-to-Service)
Use this for scripts, CI/CD pipelines, or external tools.

**Header:**
```http
X-API-Key: <your_admin_api_key>
```

### 2. JWT Token (Browser/User)
Used by the frontend dashboard. Tokens can be Axon-issued local JWTs or Keycloak-issued JWTs and are typically stored in HTTP-only cookies.

**Header:**
```http
Authorization: Bearer <your_jwt_token>
```

---

## 🚀 Key Endpoints

### 🔎 Search

#### `GET /search`
Perform a hybrid code search (Semantic + Keyword).

**Parameters:**
- `query` (string, required): Search terms (e.g., "auth controller").
- `limit` (int, default: 10): Max results.
- `repos` (list[int]): Filter by repository IDs.
- `hybrid` (bool, default: true): Enable vector search fusion.

**Example:**
```bash
curl -H "X-API-Key: $KEY" "http://localhost:8080/api/v1/search?query=User&limit=5"
```

---

### 📦 Repositories

#### `GET /repositories`
List all indexed repositories.

#### `POST /repositories`
Create a tracked repository and enqueue its initial sync immediately.

Current request shape:

```json
{
  "provider": "GIT",
  "name": "axon-src",
  "path_with_namespace": "team/axon-src",
  "url": "https://gitlab.example.org/team/axon-src.git",
  "clone_url": "https://gitlab.example.org/team/axon-src.git",
  "default_branch": "main"
}
```

Notes:
- This endpoint exists and is the main registration API.
- Supported providers are now `GITLAB`, `GITHUB`, and provider-neutral `GIT`.
- `gitlab_project_id` remains optional and is only used for `GITLAB`.
- A successful create request also enqueues the first background sync automatically.

#### `POST /repositories/register-url`
Register a GitHub or generic HTTPS git repository from just its URL and enqueue its first sync.

Request shape:

```json
{
  "repository_url": "https://github.com/octocat/example-repo",
  "default_branch": "main"
}
```

Notes:
- `provider` is optional; Axon infers `GITHUB` from `github.com`, `GITLAB` from GitLab hosts, otherwise `GIT`.
- `name`, `path_with_namespace`, and `clone_url` are derived server-side from the URL.
- If the requested branch is wrong on the first clone, clone bootstrap retries against the remote default branch instead of failing immediately.

#### `POST /repositories/delete-url`
Delete a tracked repository from just its URL.

Request shape:

```json
{
  "repository_url": "https://github.com/octocat/example-repo",
  "cleanup_cache": false
}
```

Notes:
- This is the URL-based counterpart to `POST /repositories/register-url`.
- The server derives provider and path metadata from the URL, finds the tracked repository row, and deletes it.
- `cleanup_cache=true` also removes Axon's cached git checkout for remote repositories.
- Local directory sources are never deleted by cache cleanup.
- Returns `204` on success and `404` if the repository is not currently tracked.

#### `GET /repositories/discover/{group_id}`
Discover GitLab repositories for a group and identify tracked/untracked entries.

#### `POST /repositories/bulk-add`
Register multiple repositories and enqueue sync for newly added entries.

Notes:
- Bulk registration accepts the same provider-neutral payload shape as single-repository create.
- GitLab discovery remains the only discovery-oriented bulk source today.

#### `POST /repositories/{id}/sync`
Manually trigger a full synchronization (pull, parse, analyze) for a repository.

#### `DELETE /repositories/{id}`
Delete one tracked repository and its indexed data by repository ID.

Notes:
- Optional query param: `cleanup_cache=true`
- When set, Axon also removes the cached git checkout for remote repositories.
- Local directory sources are never deleted by cache cleanup.

#### `GET /repositories/{id}`
Get repository details, including status, totals, and last commit metadata when available.

#### `GET /repositories/{id}/stats`
Get repository statistics.

#### `GET /repositories/{id}/sync-history`
Get background sync job history for a repository.

#### `GET /repositories/{id}/samples`
Get representative repository sample data.

Status note:
- Repository registration is API-driven, not config-only.
- Periodic polling for new commits is handled internally by Celery Beat; there is no separate repository polling REST endpoint.
- The normal sync path now performs a full initial index, then prefers incremental commit-diff refresh when the repository already has a known `last_commit_sha`.

### 🔐 Authentication

#### `POST /auth/login`
Login with the local admin password and receive the local JWT cookie when `local_jwt` is enabled for REST.

#### `GET /auth/methods`
Return the browser-login methods currently available to the UI.

#### `GET /auth/keycloak/login`
Start the Keycloak browser login redirect flow.

#### `GET /auth/keycloak/callback`
Complete the Keycloak auth-code callback and establish the browser session cookie.

#### `POST /auth/logout`
Clear the local session cookie.

#### `POST /auth/mcp-tokens`
Create a personal MCP access token for the authenticated user.

#### `GET /auth/mcp-tokens`
List the authenticated user's personal MCP access tokens.

#### `DELETE /auth/mcp-tokens/{token_id}`
Revoke one of the authenticated user's personal MCP access tokens.

Current auth status:
- REST and MCP each accept one or more configured auth methods.
- Available methods currently include shared API keys, local JWT, Keycloak JWT, and personal tokens.
- Browser login can use either the local password flow or the Keycloak redirect/callback flow, depending on configuration.
- Personal tokens are intended primarily for MCP/non-interactive client use.

---

### 🧩 Symbols

#### `GET /symbols`
List symbols with pagination and optional filters.

**Common query params:**
- `skip` (int, default: 0)
- `limit` (int, default: 20, max: 100)
- `repository_id` (int, optional)
- `language` (enum, optional)
- `symbol_kind` (enum, optional)

#### `GET /symbols/{symbol_id}`
Get detailed metadata for a symbol.

#### `GET /symbols/{symbol_id}/relationships`
Get a symbol with its outgoing relation edges.

#### `GET /files/{file_id}/symbols`
List symbols defined in a specific file (with pagination and optional `symbol_kind` filter).

---

### 🏗️ Services & Architecture

#### `GET /services`
List all detected services (APIs, Workers, Libraries) across the codebase.

#### `GET /services/{id}/map`
Get a dependency map for a specific service.

---

## 💻 Client Examples

### Python (using `requests`)

```python
import requests

API_KEY = "your_secret_key"
BASE_URL = "http://localhost:8080/api/v1"

headers = {"X-API-Key": API_KEY}

# 1. Search for code
response = requests.get(
    f"{BASE_URL}/search", 
    headers=headers,
    params={"query": "authentication", "limit": 3}
)
results = response.json()
print(f"Found {len(results)} matches")

# 2. Trigger sync for repo ID 1
requests.post(f"{BASE_URL}/repositories/1/sync", headers=headers)
print("Sync started...")
```

### TypeScript (using `fetch`)

```typescript
const API_KEY = "your_secret_key";

async function searchCode(query: string) {
  const response = await fetch(
    `http://localhost:8080/api/v1/search?query=${query}`,
    {
      headers: { "X-API-Key": API_KEY }
    }
  );
  return await response.json();
}

searchCode("login flow").then(console.log);
```

---

## 🛑 Error Handling

The API uses standard HTTP status codes:

- `200 OK`: Success
- `400 Bad Request`: Invalid parameters
- `401 Unauthorized`: Missing or invalid API Key/Token
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource does not exist
- `429 Too Many Requests`: Rate limit exceeded
- `500 Internal Server Error`: Server-side processing failed
