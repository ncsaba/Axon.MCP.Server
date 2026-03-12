#!/usr/bin/env bash
# Local development environment used by helper scripts in this folder.
# WARNING: hard-coded local secrets for developer convenience only.

export ENVIRONMENT=development
export environment=development
export DEBUG=false

export DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer'
export TEST_DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer'

export AUTH_ENABLED=true
export ADMIN_API_KEY='dev-admin-key'
export ADMIN_PASSWORD='dev-password'
export API_SECRET_KEY='dev-api-secret-key-32-characters-min'
export JWT_SECRET_KEY='dev-jwt-secret-key-local-only-change-for-shared-envs-64'
export GITLAB_TOKEN='local-dev-token'

export API_HOST=0.0.0.0
export API_PORT=8080
export REDIS_URL='redis://localhost:6379/0'
export CELERY_BROKER_URL='redis://localhost:6379/0'
export CELERY_RESULT_BACKEND='redis://localhost:6379/0'
