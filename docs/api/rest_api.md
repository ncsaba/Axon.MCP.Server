# REST API Reference

## 📋 Overview

The Axon MCP Server provides a comprehensive REST API for programmatic access to code search, repository management, and symbol analysis. This API powers the React Dashboard and can be used to build custom integrations.

**Base URL**: `http://localhost:8080/api/v1`
**OpenAPI Spec**: `http://localhost:8080/api/openapi.json`
**Swagger UI**: `http://localhost:8080/api/docs`

---

## 🔐 Authentication

The API supports two authentication methods:

### 1. API Key (Service-to-Service)
Use this for scripts, CI/CD pipelines, or external tools.

**Header:**
```http
X-API-Key: <your_admin_api_key>
```

### 2. JWT Token (Browser/User)
Used by the frontend dashboard. Tokens are typically stored in HTTP-only cookies.

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
  "provider": "GITLAB",
  "name": "axon-src",
  "path_with_namespace": "team/axon-src",
  "url": "https://gitlab.example.org/team/axon-src.git",
  "clone_url": "https://gitlab.example.org/team/axon-src.git",
  "default_branch": "main",
  "gitlab_project_id": 1234
}
```

Notes:
- This endpoint exists and is the main registration API.
- The runtime has a generic git/local-directory source abstraction, but the current public provider enum is still `GITLAB`-only.
- A successful create request also enqueues the first background sync automatically.

#### `GET /repositories/discover/{group_id}`
Discover GitLab repositories for a group and identify tracked/untracked entries.

#### `POST /repositories/bulk-add`
Register multiple repositories and enqueue sync for newly added entries.

Notes:
- The current implementation accepts GitLab-shaped payloads only.
- Generic Git/GitHub registration is not documented or supported end-to-end yet, even though the runtime sync layer already has generic git primitives.

#### `POST /repositories/{id}/sync`
Manually trigger a full synchronization (pull, parse, analyze) for a repository.

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
- Periodic polling for new commits is not exposed as a repository API yet; sync is currently triggered manually or by internal worker flows.

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
