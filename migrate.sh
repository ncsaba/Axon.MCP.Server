#!/bin/bash
# Migration script for Docker containers
# This script applies database migrations

set -e

echo "🔄 Running database migrations..."

# Run the migration script
python scripts/run_migrations.py

echo "✅ Migration completed!"
