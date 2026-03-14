import asyncio
import os

import psutil
from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess

from src.config.settings import get_settings
from src.utils.metrics import cpu_usage_percent, memory_usage_bytes


router = APIRouter()


def _generate_metrics_payload() -> bytes:
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {
        "status": "healthy",
        "service": get_settings().app_name,
        "version": get_settings().app_version,
        "environment": get_settings().environment,
    }


@router.get("/health/ready")
async def readiness_check() -> dict[str, str]:
    """
    Readiness probe for Kubernetes.
    Checks if service is ready to accept traffic.
    """
    return {"status": "ready"}


@router.get("/health/live")
async def liveness_check() -> dict[str, str]:
    """
    Liveness probe for Kubernetes.
    Checks if service is alive (not deadlocked).
    """
    return {"status": "alive"}


@router.get("/metrics")
async def metrics() -> Response:
    """
    Prometheus metrics endpoint.
    Exposes all application metrics.
    """
    memory_usage_bytes.set(psutil.Process().memory_info().rss)
    cpu_usage_percent.set(psutil.cpu_percent())

    return Response(content=_generate_metrics_payload(), media_type=CONTENT_TYPE_LATEST)

