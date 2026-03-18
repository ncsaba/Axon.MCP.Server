# Setup Guide

## Prerequisites

**Required:**
- Python 3.11 or higher
- PostgreSQL 17+ with pgvector extension
- Redis 7+
- Git 2.30+

**Optional:**
- Docker & Docker Compose (recommended for local development)
- Kubernetes 1.24+ (for production deployment)
- OpenAI API key (for embeddings) OR local GPU for sentence-transformers

**System Requirements:**
- 8GB RAM minimum (16GB recommended)
- 20GB disk space for caching
- Network access to GitLab instance

## Installation

### Option 1: Docker (Recommended)

```bash
# Clone repository
git clone https://devops.example.org/axon/devops/axon.mcp.server.git
cd axon.mcp.server

# Configure environment
cp .env.example .env
# Edit .env with your credentials
# Keep DATABASE_URL and TEST_DATABASE_URL pointed at different databases.

# Start all services
docker-compose -f docker/docker-compose.yml up -d

# Check service health
docker-compose logs -f api
```

### Option 2: Local Development

```bash
# Clone repository
git clone https://github.com/ali-kamali/Axon.MCP.Server.git
cd axon.mcp.server

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
make dev-install

# Start PostgreSQL and Redis (or use Docker for just these)
docker-compose -f docker/docker-compose.yml up -d postgres redis

# For the current WIP cycle, prefer a fresh schema from the ORM baseline.
# Alembic is still available if you explicitly want the migration path.
python scripts/reset_db.py --yes

# Start API server
uvicorn src.api.main:app --host 0.0.0.0 --port 8080 --reload

# In another terminal, start Celery worker
celery -A src.workers.celery_app.celery_app worker --loglevel=info
```

## Quick Start

Get up and running in 5 minutes:

```bash
# Clone the repository
git clone https://github.com/ali-kamali/Axon.MCP.Server.git
cd axon.mcp.server

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your GitLab token, OpenAI key, and database URL

# Start services with Docker Compose
make docker-up

# Optional Alembic path:
# make migrate
# Current WIP default for this branch:
make db-reset

# Verify installation
curl http://localhost:8080/api/v1/health
```

Both workflows remain valid. For current development on this fork, use `make db-reset` when you want a clean schema quickly; use `make migrate` only when you intentionally want to exercise the Alembic path.

For DB-backed tests, keep a dedicated test database, for example:

```bash
source /home/vscode/.venv-dev/bin/activate
```

In the devcontainer, `DATABASE_URL` and `TEST_DATABASE_URL` are already set to `indexer` and `indexer_test`, and the Postgres bootstrap initializes both databases automatically after rebuild.
