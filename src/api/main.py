"""FastAPI application entry point."""

from contextlib import asynccontextmanager
import time

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.api.dependencies import get_limiter
from src.api.routes.health import router as health_router
from src.api.routes.auth import router as auth_router
from src.api.routes.jobs import router as jobs_router
from src.api.routes.mcp_test import router as mcp_test_router
from src.api.routes.repositories import router as repositories_router
from src.api.routes.search import router as search_router
from src.api.routes.symbols import router as symbols_router
from src.api.routes.workers import router as workers_router
from src.api.routes.statistics import router as statistics_router
from src.api.routes.analysis import router as analysis_router
from src.api.routes.enrichment import router as enrichment_router
from src.config.settings import get_settings
from src.database.models import Base
from src.database.session import engine
from src.utils.logging_config import configure_logging, get_logger
from src.utils.metrics import api_request_duration, api_requests_total


configure_logging()
logger = get_logger(__name__)
settings = get_settings()


async def _ensure_semantic_search_vector_index() -> None:
    """Ensure the fixed-dimension pgvector ANN index exists for semantic search."""
    try:
        from src.database.session import AsyncSessionLocal
        from src.vector_store.pgvector_store import PgVectorStore

        async with AsyncSessionLocal() as session:
            store = PgVectorStore(session)
            status = await store.ensure_vector_index(index_type="hnsw")
            await session.commit()
            logger.info(
                "semantic_search_vector_index_ready",
                status=status,
                preferred_index_type="hnsw",
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("semantic_search_vector_index_ensure_failed", error=str(exc))


async def _ensure_source_control_provider_enum_values(conn) -> None:
    """Ensure new provider enum values exist in PostgreSQL before ORM usage."""
    try:
        for provider in ("GITHUB", "GIT"):
            await conn.execute(
                sa.text(
                    f"""
                    DO $$
                    BEGIN
                        ALTER TYPE sourcecontrolproviderenum ADD VALUE '{provider}';
                    EXCEPTION
                        WHEN duplicate_object THEN NULL;
                    END $$;
                    """
                )
            )
        logger.info("source_control_provider_enum_aligned")
    except Exception as exc:
        logger.warning("source_control_provider_enum_alignment_failed", error=str(exc))


@asynccontextmanager
async def _lifespan(_: FastAPI):
    logger.info("application_startup", environment=settings.environment)
    mcp_http_manager_cm = None

    if "*" in settings.api_cors_origins:
        logger.warning(
            "cors_wildcard_configured",
            message="API_CORS_ORIGINS contains '*'; credentialed browser requests are disabled by design. "
                    "Set explicit origins to enable cookies/auth headers in browsers."
        )

    # Validate auth configuration
    if settings.auth_enabled and not settings.rest_auth_methods:
        logger.warning(
            "auth_methods_not_configured",
            message="AUTH_ENABLED=true but REST_AUTH_METHODS is empty; authenticated REST access will always fail.",
        )

    if settings.auth_enabled and "api_key" in settings.rest_auth_methods and not settings.admin_api_key and not settings.read_only_api_keys:
        logger.warning(
            "auth_misconfiguration",
            message="API key auth is enabled for REST but no API keys are configured. "
                    "Set ADMIN_API_KEY / READ_ONLY_API_KEYS, remove api_key from REST_AUTH_METHODS, or disable auth."
        )

    # Validate MCP HTTP auth posture
    if settings.mcp_transport == "http" and not settings.mcp_auth_enabled:
        logger.warning(
            "mcp_http_auth_disabled",
            message="MCP HTTP transport is running without MCP auth. This should only be used for trusted local development. "
                    "Set MCP_AUTH_ENABLED=true for shared or network-exposed deployments."
        )

    if "local_jwt" in settings.rest_auth_methods and not settings.jwt_secret_key:
        logger.error(
            "jwt_secret_key_missing",
            message="JWT_SECRET_KEY is required when local_jwt auth is enabled for REST. Generate with: "
                    "python -c 'import secrets; print(secrets.token_urlsafe(64))'"
        )
        raise RuntimeError("JWT_SECRET_KEY not configured")

    if settings.mcp_transport == "http" and settings.mcp_auth_enabled and not settings.mcp_auth_methods:
        logger.warning(
            "mcp_auth_methods_not_configured",
            message="MCP auth is enabled but MCP_AUTH_METHODS is empty; authenticated MCP access will always fail.",
        )

    if settings.mcp_transport == "http" and settings.mcp_auth_enabled and "local_jwt" in settings.mcp_auth_methods and not settings.jwt_secret_key:
        logger.error(
            "mcp_jwt_secret_key_missing",
            message="JWT_SECRET_KEY is required when local_jwt auth is enabled for MCP HTTP."
        )
        raise RuntimeError("JWT_SECRET_KEY not configured")

    keycloak_needed = (
        "keycloak_jwt" in settings.rest_auth_methods
        or (
            settings.mcp_transport == "http"
            and settings.mcp_auth_enabled
            and "keycloak_jwt" in settings.mcp_auth_methods
        )
    )
    if keycloak_needed and not settings.keycloak_issuer_url:
        logger.error(
            "keycloak_issuer_missing",
            message="KEYCLOAK_ISSUER_URL is required when keycloak_jwt auth is enabled."
        )
        raise RuntimeError("KEYCLOAK_ISSUER_URL not configured")

    # Initialize database tables on first startup
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("database_extension_enabled", extension="vector")
            await _ensure_source_control_provider_enum_values(conn)
            await conn.run_sync(Base.metadata.create_all)
            logger.info("database_tables_initialized")
    except Exception as exc:
        logger.error("database_initialization_failed", error=str(exc))

    # In this WIP fork, schema drift is handled by reset/recreate rather than
    # automatic migration execution on startup. Alembic remains available for
    # manual use, but runtime boot should not mutate schema history.
    logger.info("database_schema_mode", mode="reset_or_create_all")

    await _ensure_semantic_search_vector_index()

    # Reset any interrupted jobs from previous run
    try:
        from src.database.session import AsyncSessionLocal
        from src.workers.job_monitor import JobMonitor

        async with AsyncSessionLocal() as session:
            monitor = JobMonitor(session)
            reset_count = await monitor.reset_running_jobs_on_startup()
            if reset_count > 0:
                logger.warning("interrupted_jobs_reset_on_startup", count=reset_count)
    except Exception as exc:
        logger.error("startup_job_cleanup_failed", error=str(exc))

    try:
        if settings.mcp_transport == "http":
            from src.api.routes.mcp_http import (
                create_mcp_http_session_manager,
                set_mcp_http_session_manager,
            )

            manager = create_mcp_http_session_manager()
            set_mcp_http_session_manager(manager)
            mcp_http_manager_cm = manager.run()
            await mcp_http_manager_cm.__aenter__()
            logger.info("mcp_http_session_manager_started", json_response=True)

        yield
    finally:
        if mcp_http_manager_cm is not None:
            from src.api.routes.mcp_http import set_mcp_http_session_manager

            await mcp_http_manager_cm.__aexit__(None, None, None)
            set_mcp_http_session_manager(None)
        logger.info("application_shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="MCP Server for semantic code search and analysis",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=_lifespan,
)

limiter = get_limiter()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_origins = settings.api_cors_origins
_cors_has_wildcard = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _cors_has_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """Collect request metrics and structured logs."""

    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        logger.error("api_request_failed", path=request.url.path, method=request.method, error=str(exc))
        raise

    duration = time.perf_counter() - start
    api_requests_total.labels(method=request.method, endpoint=request.url.path, status=response.status_code).inc()
    api_request_duration.labels(method=request.method, endpoint=request.url.path).observe(duration)
    response.headers["X-Process-Time"] = f"{duration:.6f}"

    logger.info(
        "api_request_completed",
        path=request.url.path,
        method=request.method,
        status=response.status_code,
        duration=duration,
    )
    return response


@app.exception_handler(404)
async def _not_found_handler(_: Request, __: Exception):
    return JSONResponse(status_code=404, content={"detail": "Resource not found"})


@app.exception_handler(500)
async def _internal_error_handler(request: Request, exc: Exception):
    logger.error("internal_server_error", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health_router, prefix="/api/v1", tags=["Health"])
app.include_router(auth_router, prefix="/api/v1", tags=["Auth"])
app.include_router(search_router, prefix="/api/v1", tags=["Search"])
app.include_router(repositories_router, prefix="/api/v1", tags=["Repositories"])
app.include_router(symbols_router, prefix="/api/v1", tags=["Symbols"])
app.include_router(jobs_router, prefix="/api/v1", tags=["Jobs"])
app.include_router(workers_router, prefix="/api/v1", tags=["Workers"])
app.include_router(statistics_router, prefix="/api/v1", tags=["Statistics"])
app.include_router(analysis_router, prefix="/api/v1", tags=["Analysis"])
app.include_router(enrichment_router, prefix="/api/v1", tags=["Enrichment"])
app.include_router(mcp_test_router, prefix="/api/v1", tags=["MCP Testing"])

# MCP HTTP transport endpoint (no prefix - root level)
# Import lazily only when HTTP transport is enabled to reduce API startup/import overhead
# for the default stdio deployment mode.
if settings.mcp_transport == "http":
    from src.api.routes.mcp_http import mount_mcp_http_app, router as mcp_http_router

    app.include_router(mcp_http_router, tags=["MCP HTTP Transport"])
    mount_mcp_http_app(app)
