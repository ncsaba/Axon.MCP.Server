import asyncio
import json

from src.api.services.repository_log_bridge import (
    classify_embedding_lifecycle_event,
    infer_repository_progress_stage,
    log_repository_progress_event,
)
from src.utils.redis_logger import REPOSITORY_LOG_EVENTS_CHANNEL, RedisLogPublisher


class _FakeRedis:
    def __init__(self):
        self.xadd_calls = []
        self.expire_calls = []
        self.publish_calls = []

    async def xadd(self, *args, **kwargs):
        self.xadd_calls.append((args, kwargs))

    async def expire(self, *args, **kwargs):
        self.expire_calls.append((args, kwargs))

    async def publish(self, *args, **kwargs):
        self.publish_calls.append((args, kwargs))


def test_classify_embedding_lifecycle_event_detects_start_and_completion():
    assert classify_embedding_lifecycle_event({"message": "Generating embeddings..."}) == "started"
    assert (
        classify_embedding_lifecycle_event(
            {"message": "Embedding generation completed. Generated 42 embeddings."}
        )
        == "completed"
    )
    assert classify_embedding_lifecycle_event({"message": "Parsing completed."}) is None


def test_infer_repository_progress_stage_detects_embedding_and_parsing():
    assert infer_repository_progress_stage({"message": "Generating embeddings..."}) == "embedding"
    assert infer_repository_progress_stage({"message": "Parsing completed. Processed 42 files."}) == "parsing"
    assert infer_repository_progress_stage({"message": "Repository sync completed successfully!"}) == "completed"


def test_log_repository_progress_event_logs_worker_progress(monkeypatch):
    captured = []

    class _Logger:
        def info(self, event, **kwargs):
            captured.append((event, kwargs))

    monkeypatch.setattr(
        "src.api.services.repository_log_bridge.logger",
        _Logger(),
    )

    log_repository_progress_event(
        {
            "repository_id": 4,
            "level": "INFO",
            "message": "Generating embeddings...",
            "details": {"embeddings_generated": 0},
        }
    )
    assert captured == [
        (
            "repository_sync_progress",
            {
                "repository_id": 4,
                "stage": "embedding",
                "phase": "started",
                "message": "Generating embeddings...",
                "level": "INFO",
                "details": {"embeddings_generated": 0},
            },
        )
    ]

    captured.clear()
    log_repository_progress_event(
        {
            "repository_id": 4,
            "level": "INFO",
            "message": "Parsing completed.",
            "details": {"files_processed": 12},
        }
    )
    assert captured == [
        (
            "repository_sync_progress",
            {
                "repository_id": 4,
                "stage": "parsing",
                "phase": None,
                "message": "Parsing completed.",
                "level": "INFO",
                "details": {"files_processed": 12},
            },
        )
    ]


def test_redis_log_publisher_fans_out_repository_log_events():
    async def _run():
        publisher = RedisLogPublisher()
        publisher._redis = _FakeRedis()

        await publisher.publish_log(
            9,
            "Embedding generation completed. Generated 3 embeddings.",
            details={"embeddings_generated": 3},
        )

        assert len(publisher._redis.xadd_calls) == 1
        assert len(publisher._redis.expire_calls) == 1
        assert len(publisher._redis.publish_calls) == 1

        publish_args, _ = publisher._redis.publish_calls[0]
        assert publish_args[0] == REPOSITORY_LOG_EVENTS_CHANNEL
        payload = json.loads(publish_args[1])
        assert payload["repository_id"] == 9
        assert payload["message"].startswith("Embedding generation completed.")
        assert payload["details"]["embeddings_generated"] == 3

    asyncio.run(_run())


def test_redis_log_publisher_mirrors_repository_progress_to_local_logger(monkeypatch):
    captured = []
    printed = []

    class _Logger:
        def info(self, message, **kwargs):
            captured.append(("info", message, kwargs))

        def warning(self, message, **kwargs):
            captured.append(("warning", message, kwargs))

        def error(self, message, **kwargs):
            captured.append(("error", message, kwargs))

    async def _run():
        publisher = RedisLogPublisher()
        publisher._redis = _FakeRedis()

        monkeypatch.setattr("src.utils.redis_logger.logger", _Logger())
        monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append((args, kwargs)))

        await publisher.publish_log(
            12,
            "Repository cloned successfully.",
            details={"path": "/tmp/example"},
        )

    asyncio.run(_run())

    assert captured == [
        (
            "info",
            "repository_progress_log",
            {
                "extra": {
                    "repository_id": 12,
                    "repository_progress_message": "Repository cloned successfully.",
                    "repository_progress_details": {"path": "/tmp/example"},
                    "repository_log_level": "INFO",
                }
            },
        )
    ]
    assert printed == [
        (
            (
                '[repo 12][INFO] Repository cloned successfully. | details={"path": "/tmp/example"}',
            ),
            {"flush": True},
        )
    ]
