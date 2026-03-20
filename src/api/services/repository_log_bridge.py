"""Mirror repository worker logs into the API process logs."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import redis.asyncio as redis

from src.config.settings import get_settings
from src.utils.logging_config import get_logger
from src.utils.redis_logger import REPOSITORY_LOG_EVENTS_CHANNEL


logger = get_logger(__name__)


def infer_repository_progress_stage(payload: dict[str, Any]) -> str:
    """Infer a coarse repository sync stage from a worker log payload."""
    message = str(payload.get("message") or "").strip().lower()
    if "cloning" in message or "cloned" in message:
        return "clone"
    if "parsing" in message:
        return "parsing"
    if "api endpoint extraction" in message:
        return "api_extraction"
    if "configuration" in message:
        return "config_extraction"
    if "package dependencies" in message:
        return "dependency_extraction"
    if "import relationship" in message:
        return "import_resolution"
    if "reference relationship" in message:
        return "reference_building"
    if "call graph" in message:
        return "call_graph"
    if "service boundaries" in message:
        return "service_detection"
    if "service documentation" in message:
        return "service_documentation"
    if "pattern" in message:
        return "pattern_detection"
    if "module summar" in message:
        return "module_summary"
    if "embedding" in message:
        return "embedding"
    if "repository sync completed" in message:
        return "completed"
    if "repository sync failed" in message:
        return "failed"
    return "progress"


def classify_embedding_lifecycle_event(payload: dict[str, Any]) -> str | None:
    """Classify repository log payloads into embedding lifecycle events."""
    message = str(payload.get("message") or "").strip().lower()
    if message == "generating embeddings...":
        return "started"
    if message.startswith("embedding generation completed."):
        return "completed"
    return None


def log_repository_progress_event(payload: dict[str, Any]) -> None:
    """Emit an API-side log line for repository progress events."""
    details = payload.get("details") or {}
    phase = classify_embedding_lifecycle_event(payload)
    logger.info(
        "repository_sync_progress",
        repository_id=payload.get("repository_id"),
        stage=infer_repository_progress_stage(payload),
        phase=phase,
        message=payload.get("message"),
        level=payload.get("level"),
        details=details,
    )


async def mirror_repository_log_events() -> None:
    """Subscribe to live repository log fanout and mirror repository progress."""
    redis_client = redis.from_url(
        get_settings().redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(REPOSITORY_LOG_EVENTS_CHANNEL)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if not message or message.get("type") != "message":
                await asyncio.sleep(0)
                continue

            try:
                payload = json.loads(message["data"])
            except (TypeError, json.JSONDecodeError):
                logger.warning(
                    "repository_log_bridge_payload_invalid",
                    raw_message=message.get("data"),
                )
                continue

            log_repository_progress_event(payload)
    except asyncio.CancelledError:
        raise
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(REPOSITORY_LOG_EVENTS_CHANNEL)
        with contextlib.suppress(Exception):
            await pubsub.close()
        with contextlib.suppress(Exception):
            await redis_client.close()
