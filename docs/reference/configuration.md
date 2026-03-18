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
REST_AUTH_METHODS=api_key,local_jwt
ADMIN_API_KEY=replace_me
READ_ONLY_API_KEYS='[]'
MCP_AUTH_ENABLED=true
MCP_AUTH_METHODS=api_key,local_jwt,personal_token
KEYCLOAK_ISSUER_URL=
KEYCLOAK_JWKS_URL=
KEYCLOAK_AUDIENCES='[]'

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
- `REST_AUTH_METHODS`: accepted REST auth methods such as `api_key`, `local_jwt`, `keycloak_jwt`, and `personal_token`
- `ADMIN_API_KEY`: shared admin API key for service access
- `READ_ONLY_API_KEYS`: optional list of read-only API keys
- `JWT_SECRET_KEY`: local JWT validation for browser/cookie auth
- `MCP_AUTH_ENABLED`: separate auth switch for MCP HTTP
- `MCP_AUTH_METHODS`: accepted MCP auth methods such as `api_key`, `local_jwt`, `keycloak_jwt`, and `personal_token`
- `KEYCLOAK_*`: Keycloak issuer, JWKS, audience, and role-mapping settings when `keycloak_jwt` is enabled
- `KEYCLOAK_CLIENT_ID`: required for the browser login redirect/callback flow
- `KEYCLOAK_CLIENT_SECRET`: optional confidential-client secret for Keycloak code exchange
- `KEYCLOAK_REDIRECT_URI`: optional externally visible callback override
- `KEYCLOAK_SCOPES`: scopes requested during browser login; defaults to `openid,profile,email`

Current status:
- local API-key and local JWT auth are implemented
- accepted auth methods are configurable independently for REST and MCP
- Keycloak bearer-token validation is supported when `keycloak_jwt` is enabled and configured
- Keycloak browser login/session handoff is supported when `KEYCLOAK_CLIENT_ID` is configured
- personal access tokens can be created, listed, and revoked through the REST auth endpoints
- full auth bypass is configurable

Explicitly deferred:
- richer local user/account records for external identities
- audit trails for Keycloak login activity and MCP token usage

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
