-- Initialize database for Axon MCP Server
-- This file is executed only on first container startup

-- Enable pgvector extension (required for vector similarity search)
CREATE EXTENSION IF NOT EXISTS vector;

-- Create schemas if needed
CREATE SCHEMA IF NOT EXISTS public;

-- Grant permissions on main database
GRANT ALL PRIVILEGES ON DATABASE axon_mcp TO axon;
GRANT ALL ON SCHEMA public TO axon;

-- Set search path
ALTER DATABASE axon_mcp SET search_path TO public;

-- Note: Tables will be created by Alembic migrations
-- Run: alembic upgrade head
