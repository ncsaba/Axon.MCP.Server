"""Service layer helpers for REST API.

Keep package exports lazy so importing one service module does not eagerly pull
in the full service layer and trigger worker/task import cycles during test
collection.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "RepositoryService",
    "SearchService",
    "SymbolService",
]


def __getattr__(name: str) -> Any:
    if name == "RepositoryService":
        return import_module(".repository_service", __name__).RepositoryService
    if name == "SearchService":
        return import_module(".search_service", __name__).SearchService
    if name == "SymbolService":
        return import_module(".symbol_service", __name__).SymbolService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
