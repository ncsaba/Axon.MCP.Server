# GitHub Repository Registration And Polling Scenario

## Scope

This guide covers one exact end-to-end validation path that is supported now:

1. register a GitHub repository through the REST API
2. run the initial full index automatically
3. poll the repository regularly for new commits
4. refresh it through the normal sync path, with incremental commit-diff processing after the first index when safe

This guide is intentionally narrow. It is the recommended manual test path for the current repository-sync implementation.

## Supported Scenario

`✅` supported now, `🚧` not part of this guide.

| Capability | Status | Notes |
| --- | --- | --- |
| Register one GitHub repository through REST | `✅` | `provider: "GITHUB"` |
| Initial indexing after registration | `✅` | Automatic |
| Poll regularly for updates | `✅` | Celery Beat enqueues refresh on cadence |
| Incremental refresh after first index | `✅` | Normal sync task prefers incremental commit-diff refresh when `last_commit_sha` is known |
| GitHub discovery API | `🚧` | Not part of this guide |
| GitHub webhook-triggered refresh | `🚧` | Not part of this guide |
| Per-repository credentials | `🚧` | Runtime credentials are configured globally |

## Prerequisites

You need:

- API server running
- Celery worker running
- Celery Beat running
- PostgreSQL and Redis running
- a GitHub repository URL

For a public repository, `GITHUB_TOKEN` is optional.

For a private repository, set:

```bash
export GITHUB_TOKEN=your_github_token
```

Also ensure polling is enabled:

```bash
export REPOSITORY_POLL_ENABLED=true
export REPOSITORY_POLL_INTERVAL_MINUTES=15
```

## Recommended Test Target

Start with a public repository you can safely modify or observe.

Good properties:

- small to medium repository
- known default branch, usually `main`
- easy to push one extra commit for refresh validation

## Start The Runtime

From the project root:

```bash
source /home/vscode/.venv-dev/bin/activate
make api-dev
```

In a second terminal:

```bash
source /home/vscode/.venv-dev/bin/activate
celery -A src.workers.celery_app.celery_app worker --loglevel=info
```

In a third terminal:

```bash
source /home/vscode/.venv-dev/bin/activate
celery -A src.workers.celery_app.celery_app beat --loglevel=info
```

## Register The Repository

Example request:

```bash
curl -X POST http://localhost:8080/api/v1/repositories \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -d '{
    "provider": "GITHUB",
    "name": "example-repo",
    "path_with_namespace": "octocat/example-repo",
    "url": "https://github.com/octocat/example-repo",
    "clone_url": "https://github.com/octocat/example-repo.git",
    "default_branch": "main"
  }'
```

URL-only variant:

```bash
curl -X POST http://localhost:8080/api/v1/repositories/register-url \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -d '{
    "repository_url": "https://github.com/octocat/example-repo"
  }'
```

URL-only delete variant:

```bash
curl -X POST http://localhost:8080/api/v1/repositories/delete-url \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -d '{
    "repository_url": "https://github.com/octocat/example-repo",
    "cleanup_cache": true
  }'
```

Notes:
- `cleanup_cache` is optional and defaults to `false`.
- When `true`, Axon also removes the cached remote checkout under its local repo cache.
- It does not delete anything on GitHub.
- It does not delete local directory source repos tracked via `file://` or absolute paths.

Expected result:

- HTTP `201`
- repository row is created
- first background sync is queued automatically

## Verify Initial Index

Check repository state:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8080/api/v1/repositories
```

Then inspect the specific repository:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8080/api/v1/repositories/<repo_id>
```

Expected result after the first sync completes:

- `status` becomes `COMPLETED`
- `last_commit_sha` is populated
- `total_files` is greater than `0`

## Validate Regular Polling

Once the first index is complete, create one new commit in the GitHub repository.

Then wait for the next poll interval, or temporarily lower it before startup:

```bash
export REPOSITORY_POLL_INTERVAL_MINUTES=1
```

Expected runtime behavior:

1. Celery Beat enqueues the repository refresh
2. the sync worker updates the local clone
3. because `last_commit_sha` is already known, the sync path tries incremental commit-diff refresh first
4. on success, the repository remains `COMPLETED` and `last_commit_sha` advances
5. if incremental refresh is unsafe, the worker falls back to a full sync automatically

## Verify Refresh

Re-check repository details:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8080/api/v1/repositories/<repo_id>
```

What to confirm:

- `last_commit_sha` changed to the new HEAD
- `last_synced_at` moved forward
- repository still reports `COMPLETED`

You can also check sync history:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8080/api/v1/repositories/<repo_id>/sync-history
```

## Troubleshooting

| Symptom | Likely Cause | Action |
| --- | --- | --- |
| Registration succeeds but first sync fails | clone auth problem | Set `GITHUB_TOKEN` for private repos or test with a public repo first |
| Repository never refreshes | Celery Beat not running | Start Beat and verify `REPOSITORY_POLL_ENABLED=true` |
| Repository refreshes but appears to full-sync | incremental path deemed unsafe | This is acceptable fallback behavior for the current slice |
| Private GitHub clone prompts for auth | token not configured | Set `GITHUB_TOKEN` and restart worker/API |

## Current Limits

This exact scenario does not require or validate:

1. GitHub discovery support
2. webhook-triggered sync
3. MCP-based repository registration
4. per-repository secrets stored in the database
