#!/bin/bash
# Create test database for Axon MCP Server
# Use this script to create a test database for integration tests
#
# Usage:
#   Multi-container:  docker compose -f docker/docker-compose.yml exec postgres /docker-entrypoint-initdb.d/create-test-db.sh
#   Single-container: docker exec axon-mcp /docker/create-test-db.sh
#   Dev-container:    Not needed - test DB created automatically by init_postgres.sh
#
# Environment variables:
#   TEST_DB_NAME - Test database name (default: axon_mcp_test)
#   DB_USER      - Database user (default: axon for standalone, indexer for multi-container)

set -e

# Defaults
TEST_DB_NAME="${TEST_DB_NAME:-axon_mcp_test}"
DB_USER="${DB_USER:-axon}"

echo "Creating test database: ${TEST_DB_NAME}"

# Check if we're running inside PostgreSQL container (multi-container) or standalone
if command -v psql &> /dev/null; then
    # PostgreSQL container - use psql directly
    psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-axon}" -d postgres <<-EOSQL
        CREATE DATABASE ${TEST_DB_NAME} OWNER ${DB_USER};
        \connect ${TEST_DB_NAME}
        CREATE EXTENSION IF NOT EXISTS vector;
        GRANT ALL ON SCHEMA public TO ${DB_USER};
EOSQL
    echo "Test database '${TEST_DB_NAME}' created successfully."
elif command -v su &> /dev/null; then
    # Standalone container - use su to run as postgres user
    su - postgres -c "psql -c \"CREATE DATABASE ${TEST_DB_NAME} OWNER ${DB_USER};\"" || echo "Database may already exist"
    su - postgres -c "psql -d ${TEST_DB_NAME} -c \"CREATE EXTENSION IF NOT EXISTS vector;\"" || true
    su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE ${TEST_DB_NAME} TO ${DB_USER};\"" || true
    echo "Test database '${TEST_DB_NAME}' created successfully."
else
    echo "ERROR: Cannot determine how to connect to PostgreSQL"
    exit 1
fi
