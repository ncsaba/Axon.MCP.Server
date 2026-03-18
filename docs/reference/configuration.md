# Configuration Reference

## Environment Variables

Create a `.env` file in the project root:

```env
# Application
APP_NAME=Axon.MCP.Server
DEBUG=false
ENVIRONMENT=development

# GitLab Configuration
GITLAB_URL=https://gitlab.example.org
GITLAB_TOKEN=your_gitlab_token_here
GITLAB_GROUP_ID=your_group_id  # Optional

# Database Configuration
DATABASE_URL=postgresql+asyncpg://axon:password@localhost:5432/axon_mcp
TEST_DATABASE_URL=postgresql+asyncpg://axon:password@localhost:5432/axon_mcp_test

# Redis Configuration
REDIS_URL=redis://localhost:6379/0

# Celery Configuration
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Embedding Configuration
EMBEDDING_PROVIDER=openai  # or "local"
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Security
API_SECRET_KEY=your_secure_api_secret_key_here_min_32_chars
JWT_SECRET_KEY=your_secure_jwt_secret_key_here_min_64_chars
AUTH_ENABLED=true
ADMIN_API_KEY=replace_me
READ_ONLY_API_KEYS='[]'
MCP_AUTH_ENABLED=true

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

## Configuration Files

- **`.env`**: Environment-specific configuration
- **`alembic.ini`**: Alembic migration settings for the migration-based workflow
- **`docker-compose.yml`**: Docker service definitions
- **`pyproject.toml`**: Python project metadata

## Authentication Modes

Current shipped auth controls:

- `AUTH_ENABLED`: when `false`, REST/API route auth is bypassed entirely
- `ADMIN_API_KEY`: shared admin API key for service access
- `READ_ONLY_API_KEYS`: optional list of read-only API keys
- `JWT_SECRET_KEY`: local JWT validation for browser/cookie auth
- `MCP_AUTH_ENABLED`: separate auth switch for MCP HTTP

Current status:
- local API-key and local JWT auth are implemented
- full auth bypass is configurable
- Keycloak/OIDC auth is not implemented yet
- personalized MCP tokens are not implemented yet

See:
- `docs/architecture/authentication_and_mcp_token_plan.md`

## Database Separation

- `DATABASE_URL`: main runtime database for the API, workers, and manual local runs
- `TEST_DATABASE_URL`: dedicated test database used by `pytest`

Keep them different. The test harness creates and drops schema in `TEST_DATABASE_URL`.

In the workspace devcontainer, both are exported by default and point at:
- `indexer`
- `indexer_test`

The devcontainer Postgres bootstrap initializes both databases automatically.
