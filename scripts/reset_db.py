"""Deliberately reset the PostgreSQL schema and recreate Axon tables.

This is intended for manual use only when the local database needs to be
reinitialized for a breaking schema change or a clean validation run.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.models import Base


def _resolve_database_url(cli_database_url: str | None, use_test_db: bool) -> str:
    """Resolve the target database URL from CLI flags and environment."""
    if cli_database_url:
        return cli_database_url
    if use_test_db and os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    raise SystemExit(
        "No database URL configured. Pass --database-url or set DATABASE_URL/TEST_DATABASE_URL."
    )


def _ensure_asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def reset_db(database_url: str) -> None:
    """Drop and recreate the public schema, then recreate Axon tables."""
    engine = create_async_engine(_ensure_asyncpg_url(database_url), echo=False)

    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(sa.text("CREATE SCHEMA public"))
            await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset the Axon PostgreSQL schema and recreate tables."
    )
    parser.add_argument(
        "--database-url",
        help="Explicit target database URL. Defaults to DATABASE_URL or TEST_DATABASE_URL.",
    )
    parser.add_argument(
        "--use-test-db",
        action="store_true",
        help="Prefer TEST_DATABASE_URL over DATABASE_URL when both are set.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation flag. This command drops the public schema.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.yes:
        parser.error("--yes is required because this command drops the public schema.")

    database_url = _resolve_database_url(args.database_url, args.use_test_db)
    print(f"Resetting database schema for: {database_url}")
    asyncio.run(reset_db(database_url))
    print("Database reset completed successfully.")


if __name__ == "__main__":
    main()
