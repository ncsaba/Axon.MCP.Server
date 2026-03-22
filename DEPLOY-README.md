# Axon MCP Server - Deployment Guide

This guide covers deployment options for the Axon MCP Server.

---

## Deployment Options Overview

| Mode | Dockerfile | Use Case | Complexity |
|------|------------|----------|------------|
| **Dev-container** | N/A | VS Code local development | Low |
| **Multi-container** | `Dockerfile.optimized` | Production, scalable deployments | Medium |
| **Single-container** | `Dockerfile.standalone` | Demo, quick start, simple hosting | Low |

---

## Option 1: Multi-Container Deployment (Production)

Best for: Production deployments, scalable infrastructure, teams

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Docker Compose Stack                         │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   postgres   │  │    redis     │  │   prometheus/grafana │  │
│  │  (pgvector)  │  │   (queue)    │  │   (monitoring)       │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │     api      │  │  mcp-server  │  │         ui           │  │
│  │  (port 8080) │  │  (port 8001) │  │     (port 80)        │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │    worker    │  │ enrichment-  │  │        beat          │  │
│  │   (Celery)   │  │   worker     │  │    (scheduler)       │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Prerequisites

- Docker 20.10+
- Docker Compose 2.0+
- 8GB+ RAM recommended
- 20GB+ disk space

### Quick Start

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Edit .env with your settings
# - Update DATABASE_URL to use 'postgres' as hostname
# - Update REDIS_URL to use 'redis' as hostname
# - Set ADMIN_API_KEY and ADMIN_PASSWORD
# - Generate API_SECRET_KEY and JWT_SECRET_KEY

# 3. Build base image (first time or after requirements.txt changes)
./rebuild-base.sh

# 4. Start services
docker compose -f docker/docker-compose.yml up -d

# 5. Run database migrations
docker compose -f docker/docker-compose.yml exec api alembic upgrade head

# 6. Check service health
docker compose -f docker/docker-compose.yml ps
```

### Environment Variables for Multi-Container

```bash
# Database - use container service name
DATABASE_URL=postgresql+asyncpg://indexer:indexer@postgres:5432/indexer
TEST_DATABASE_URL=postgresql+asyncpg://indexer:indexer@postgres:5432/indexer_test

# Redis - use container service name with password
REDIS_URL=redis://:indexer@redis:6379/0
CELERY_BROKER_URL=redis://:indexer@redis:6379/0
CELERY_RESULT_BACKEND=redis://:indexer@redis:6379/0

# UI - set to your server's address
VITE_API_BASE_URL=http://your-server-address:8080/
```

### Service Management

```bash
# View logs
docker compose -f docker/docker-compose.yml logs -f api
docker compose -f docker/docker-compose.yml logs -f worker

# Restart a service
docker compose -f docker/docker-compose.yml restart api

# Scale workers
docker compose -f docker/docker-compose.yml up -d --scale worker=3

# Stop all services
docker compose -f docker/docker-compose.yml down

# Stop and remove volumes (full reset)
docker compose -f docker/docker-compose.yml down -v
```

### Production Checklist

- [ ] Change all default passwords in `.env`
- [ ] Generate secure `API_SECRET_KEY` and `JWT_SECRET_KEY`
- [ ] Set `AUTH_ENABLED=true`
- [ ] Configure `VITE_API_BASE_URL` to your public URL
- [ ] Set up TLS/SSL reverse proxy (nginx, traefik, or cloud load balancer)
- [ ] Configure backup for PostgreSQL volume
- [ ] Review resource limits in docker-compose.yml
- [ ] Set up log aggregation (optional)

---

## Option 2: Single-Container Deployment (All-in-One)

Best for: Demo, quick start, simple hosting, testing

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  Single Container (axon-standalone)             │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Supervisor (PID 1)                     │  │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────────────┐ │  │
│  │  │ PostgreSQL │ │   Redis    │ │   API + MCP + Workers│ │  │
│  │  │ (internal) │ │ (internal) │ │   (managed services) │ │  │
│  │  └────────────┘ └────────────┘ └──────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Exposed Ports: 8080 (API), 8001 (MCP)                         │
└─────────────────────────────────────────────────────────────────┘
```

### Prerequisites

- Docker 20.10+
- 4GB+ RAM minimum
- 10GB+ disk space

### Quick Start

```bash
# 1. Build the standalone image
docker build -f docker/Dockerfile.standalone -t axon-standalone:latest .

# 2. Run the container
docker run -d \
  --name axon-mcp \
  -p 8080:8080 \
  -p 8001:8001 \
  -e ADMIN_API_KEY=your-admin-key \
  -e ADMIN_PASSWORD=your-admin-password \
  -e API_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
  -e JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(64))") \
  -v axon-data:/var/lib/postgresql/17/main \
  axon-standalone:latest

# 3. Check logs
docker logs -f axon-mcp

# 4. Health check
curl http://localhost:8080/api/v1/health
```

### Environment Variables for Single-Container

The standalone image has built-in defaults for local services:

```bash
# Built-in defaults (no need to override for basic usage)
DATABASE_URL=postgresql+asyncpg://axon:axon@localhost:5432/axon_mcp
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
```

Required overrides:

```bash
# Authentication (REQUIRED)
ADMIN_API_KEY=your-admin-key
ADMIN_PASSWORD=your-admin-password
API_SECRET_KEY=your-secret-key-min-32-chars
JWT_SECRET_KEY=your-jwt-secret-min-64-chars

# External services (optional)
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
GITLAB_URL=https://gitlab.example.org
GITLAB_TOKEN=your-token
```

### Data Persistence

```bash
# Persist PostgreSQL data
-v axon-data:/var/lib/postgresql/17/main

# Persist repository cache
-v ./cache:/app/cache

# Persist logs
-v ./logs:/var/log/supervisor
```

### Management Commands

```bash
# View process status
docker exec axon-mcp supervisorctl status

# Restart a service
docker exec axon-mcp supervisorctl restart api
docker exec axon-mcp supervisorctl restart worker

# View logs
docker exec axon-mcp cat /var/log/supervisor/api.log

# Access PostgreSQL
docker exec -it axon-mcp su - postgres -c "psql -d axon_mcp"

# Run migrations
docker exec axon-mcp alembic upgrade head
```

---

## Test Database Creation

For development and testing, you may need a separate test database. A script is provided for this purpose.

### Script Location

- **In images**: `/docker/create-test-db.sh`
- **Source**: `docker/create-test-db.sh`

### Usage

#### Multi-Container Deployment

The test database is NOT created automatically. Run the script manually when needed:

```bash
# Option 1: Copy script into container and run
docker compose -f docker/docker-compose.yml cp docker/create-test-db.sh postgres:/tmp/
docker compose -f docker/docker-compose.yml exec postgres bash /tmp/create-test-db.sh

# Option 2: Create manually with psql
docker compose -f docker/docker-compose.yml exec postgres \
    psql -U axon -c "CREATE DATABASE axon_mcp_test OWNER axon;"
```

#### Single-Container Deployment

```bash
# The script is included at /docker/create-test-db.sh
docker exec axon-mcp /docker/create-test-db.sh

# Or access PostgreSQL directly
docker exec -it axon-mcp su - postgres -c "psql -c \"CREATE DATABASE axon_mcp_test OWNER axon;\""
```

#### Dev-Container

The dev-container automatically creates the test database on first start via `.devcontainer/scripts/init_postgres.sh`. No manual action needed.

### Test Database Configuration

Set the test database URL in your `.env`:

```bash
# Multi-container
TEST_DATABASE_URL=postgresql+asyncpg://axon:axon@postgres:5432/axon_mcp_test

# Single-container / Dev-container
TEST_DATABASE_URL=postgresql+asyncpg://axon:axon@localhost:5432/axon_mcp_test
```

---

## Option 3: Dev-Container (Development)

Best for: Local development with VS Code

See [`.devcontainer/README.md`](../.devcontainer/README.md) for setup instructions.

### Quick Reference

```bash
# Services start automatically in dev-container
# PostgreSQL: localhost:5432 (via pg_ctl)
# Redis: localhost:6379

# Start API
make api-dev

# Start MCP server
make mcp-dev

# Start Celery worker
celery -A src.workers.celery_app worker \
    --loglevel=info \
    -Q repository_sync,file_parsing,embeddings,inventory,ai_enrichment,repository_aggregation
```

---

## Dockerfile Reference

| Dockerfile | Purpose | Build Time | Image Size |
|------------|---------|------------|------------|
| `Dockerfile` | Standard multi-stage | ~5min | ~800MB |
| `Dockerfile.base` | Pre-built dependencies | ~8min | ~2GB |
| `Dockerfile.fast` | Uses pre-built base | ~30s | ~800MB |
| `Dockerfile.optimized` | Layer-optimized | ~5min | ~800MB |
| `Dockerfile.standalone` | All-in-one | ~10min | ~1.5GB |

### Build Commands

```bash
# Standard build
docker build -f docker/Dockerfile -t axon:latest .

# Fast development build (requires base image)
./rebuild-base.sh  # First time
docker build -f docker/Dockerfile.fast -t axon:dev .

# Production build
docker build -f docker/Dockerfile.optimized -t axon:prod .

# Standalone build
docker build -f docker/Dockerfile.standalone -t axon-standalone:latest .
```

---

## Troubleshooting

### Common Issues

#### Database Connection Failed

```bash
# Check PostgreSQL is running
docker compose -f docker/docker-compose.yml ps postgres

# Check logs
docker compose -f docker/docker-compose.yml logs postgres

# Verify connection string
echo $DATABASE_URL
```

#### Worker Not Processing Tasks

```bash
# Check worker logs
docker compose -f docker/docker-compose.yml logs worker

# Verify queue assignment
docker compose -f docker/docker-compose.yml exec worker \
    celery -A src.workers.celery_app inspect active_queues
```

#### MCP Server Health Check Failing

The MCP server uses the same FastAPI app as the API server. Health check endpoint:
```
http://localhost:8001/api/v1/health
```

#### Migration Errors

```bash
# Check current migration status
docker compose -f docker/docker-compose.yml exec api alembic current

# View migration history
docker compose -f docker/docker-compose.yml exec api alembic history

# Rollback one migration
docker compose -f docker/docker-compose.yml exec api alembic downgrade -1
```

### Health Check Endpoints

| Service | Endpoint | Expected Response |
|---------|----------|-------------------|
| API | `/api/v1/health` | `{"status": "healthy"}` |
| API Ready | `/api/v1/health/ready` | `{"status": "ready"}` |
| API Live | `/api/v1/health/live` | `{"status": "alive"}` |
| UI | `/health` | HTTP 200 |

---

## Security Considerations

### Production Security Checklist

- [ ] All secrets use strong, unique values
- [ ] `AUTH_ENABLED=true`
- [ ] TLS/SSL enabled (reverse proxy or load balancer)
- [ ] Database not exposed to public (remove `ports:` from docker-compose)
- [ ] Redis password set
- [ ] Regular backups configured
- [ ] Log retention policy configured
- [ ] Resource limits set in docker-compose

### Network Security

For production, consider removing port exposures and using:

```yaml
# Remove external port mappings
services:
  postgres:
    # ports:  # Remove this
    #   - "5432:5432"
    expose:
      - "5432"  # Internal only
```

---

## Upgrading

### Multi-Container Upgrade

```bash
# 1. Pull latest code
git pull

# 2. Backup database
docker compose -f docker/docker-compose.yml exec postgres \
    pg_dump -U axon axon_mcp > backup_$(date +%Y%m%d).sql

# 3. Rebuild images
./rebuild-base.sh
docker compose -f docker/docker-compose.yml build

# 4. Stop services
docker compose -f docker/docker-compose.yml down

# 5. Start new version
docker compose -f docker/docker-compose.yml up -d

# 6. Run migrations
docker compose -f docker/docker-compose.yml exec api alembic upgrade head
```

### Single-Container Upgrade

```bash
# 1. Stop and backup
docker stop axon-mcp
docker commit axon-mcp axon-mcp-backup

# 2. Build new image
docker build -f docker/Dockerfile.standalone -t axon-standalone:latest .

# 3. Remove old container
docker rm axon-mcp

# 4. Start new container (with same volume mounts)
docker run -d \
  --name axon-mcp \
  -p 8080:8080 -p 8001:8001 \
  -v axon-data:/var/lib/postgresql/17/main \
  axon-standalone:latest
```

---

## Support

- Documentation: [`axon-src/docs/`](./docs/)
- Issues: GitHub Issues
- Architecture: [`docs/docker_tooling_assessment.md`](../docs/docker_tooling_assessment.md)
