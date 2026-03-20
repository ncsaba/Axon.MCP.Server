"""
Celery application configuration for Axon MCP Server.

This module configures the Celery app for distributed task processing
including repository synchronization, parsing, extraction, and embedding generation.
"""

from celery import Celery, signals
from datetime import timedelta
from src.config.settings import get_settings
from src.utils.logging_config import configure_logging, get_logger


configure_logging()
logger = get_logger(__name__)

# Initialize Celery app
celery_app = Celery(
    'axon_mcp_server',
    broker=get_settings().celery_broker_url,
    backend=get_settings().celery_result_backend
)

# Configure Celery
celery_app.conf.update(
    # Serialization
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    
    # Timezone
    timezone='UTC',
    enable_utc=True,
    
    # Task tracking
    task_track_started=True,
    task_send_sent_event=True,
    
    # Time limits
    task_time_limit=get_settings().celery_task_time_limit,
    task_soft_time_limit=get_settings().celery_task_soft_time_limit,
    
    # Worker settings
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    worker_log_format='[%(asctime)s: %(levelname)s/%(processName)s] %(message)s',
    
    # Task routing
    task_routes={
        'src.workers.tasks.sync_repository': {'queue': 'repository_sync'},
        'src.workers.tasks.parse_file_task': {'queue': 'file_parsing'},
        'src.workers.tasks.generate_embeddings_task': {'queue': 'embeddings'},
        'src.workers.inventory_worker.process_discovery_batch': {
            'queue': get_settings().inventory_queue_name
        },
        'src.workers.enrichment_worker.enrich_batch': {'queue': 'ai_enrichment'},
        'src.workers.aggregation_worker.aggregate_repository_summary': {'queue': 'repository_aggregation'},
        'src.workers.file_lifecycle_worker.cleanup_missing_file_instances': {'queue': 'repository_aggregation'},
        'src.workers.tasks.poll_repositories_for_updates': {'queue': 'repository_sync'},
    },
    
    # Default queue settings
    task_default_queue='default',
    task_default_exchange='default',
    task_default_exchange_type='direct',
    task_default_routing_key='default',
    
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    result_persistent=True,
    
    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    
    # Connection settings
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
)

from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "refresh-system-context-every-hour": {
        "task": "src.workers.system_context_worker.generate_context",
        "schedule": crontab(minute=0),  # Every hour
        "args": (None,),
    },
    "cleanup-missing-file-instances-daily": {
        "task": "src.workers.file_lifecycle_worker.cleanup_missing_file_instances",
        "schedule": crontab(minute=30, hour=2),
        "args": (),
    },
}

if get_settings().repository_poll_enabled:
    celery_app.conf.beat_schedule["poll-repositories-for-updates"] = {
        "task": "src.workers.tasks.poll_repositories_for_updates",
        "schedule": timedelta(minutes=get_settings().repository_poll_interval_minutes),
        "args": (),
    }

# Auto-discover tasks
celery_app.autodiscover_tasks(['src.workers'])


@signals.worker_process_init.connect
def setup_worker_credentials(**kwargs):
    """
    Setup git credentials when worker process starts.
    
    This ensures credentials are configured in each worker process
    before any git operations are performed.
    """
    logger.info("celery_worker_process_initialized")
    return None


@signals.worker_ready.connect
def log_worker_ready(sender=None, **kwargs):
    """Log a clear worker-ready event on startup."""
    logger.info(
        "celery_worker_ready",
        hostname=str(getattr(sender, "hostname", "unknown")),
        queues=sorted(celery_app.conf.task_routes.keys()),
    )


@signals.task_prerun.connect
def log_task_start(task=None, task_id=None, args=None, kwargs=None, **extras):
    """Log clear task start markers for the main indexing pipeline."""
    task_name = str(getattr(task, "name", ""))
    if task_name not in {
        "src.workers.tasks.sync_repository",
        "src.workers.tasks.generate_embeddings_task",
    }:
        return

    logger.info(
        "celery_task_started",
        task_name=task_name,
        task_id=task_id,
        args=list(args or []),
    )
