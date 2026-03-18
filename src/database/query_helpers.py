"""Common query helpers for database filtering semantics."""

from src.config.enums import FileLifecycleStateEnum
from src.database.models import FileInstance as File


def active_file_filter():
    """Return the default filter for user-facing active file instances."""
    return File.lifecycle_state == FileLifecycleStateEnum.ACTIVE
