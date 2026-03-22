#!/bin/bash
# Entrypoint script for Axon MCP standalone container
# This script initializes PostgreSQL, creates databases, runs migrations, and starts supervisor

set -e

echo "=== Axon MCP Standalone Container Starting ==="

# Initialize PostgreSQL if not already initialized
if [ ! -f /var/lib/postgresql/17/main/PG_VERSION ]; then
    echo "Initializing PostgreSQL..."
    su - postgres -c "/usr/lib/postgresql/17/bin/initdb -D /var/lib/postgresql/17/main"
    su - postgres -c "echo \"host all all 0.0.0.0/0 md5\" >> /var/lib/postgresql/17/main/pg_hba.conf"
    su - postgres -c "echo \"local all all trust\" >> /var/lib/postgresql/17/main/pg_hba.conf"
    su - postgres -c "echo \"listen_addresses = '*'\" >> /var/lib/postgresql/17/main/postgresql.conf"
fi

# Start PostgreSQL temporarily for initialization
echo "Starting PostgreSQL for initialization..."
su - postgres -c "/usr/lib/postgresql/17/bin/pg_ctl -D /var/lib/postgresql/17/main -l /var/log/postgresql.log start"
sleep 3

# Create database and user if not exists
echo "Creating database user and database..."
su - postgres -c "psql -c \"CREATE USER axon WITH PASSWORD 'axon';\"" || true
su - postgres -c "psql -c \"CREATE DATABASE axon_mcp OWNER axon;\"" || true

# Enable pgvector extension
echo "Enabling pgvector extension..."
su - postgres -c "psql -d axon_mcp -c \"CREATE EXTENSION IF NOT EXISTS vector;\"" || true

# Grant permissions
su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE axon_mcp TO axon;\"" || true

# Stop PostgreSQL (supervisor will manage it)
su - postgres -c "/usr/lib/postgresql/17/bin/pg_ctl -D /var/lib/postgresql/17/main stop"
echo "PostgreSQL initialization complete."

# Run database migrations
echo "Running database migrations..."
cd /app && alembic upgrade head || echo "Migration warning: check logs"

# Start supervisor (manages all processes)
echo "Starting supervisor..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/axon.conf
