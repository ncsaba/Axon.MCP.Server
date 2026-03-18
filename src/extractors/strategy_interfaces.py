"""Language strategy interfaces for extractor specialization seams."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from src.config.enums import LanguageEnum
from src.database.models import FileInstance as File
from src.extractors.call_analyzer import Call


@runtime_checkable
class ImportExtractionStrategy(Protocol):
    """Strategy for extracting/import-resolving language-specific imports."""

    async def extract_imports(
        self,
        code: str,
        file: File,
        file_path: Path,
    ) -> List[Dict[str, Any]]:
        ...


@runtime_checkable
class CallExtractionStrategy(Protocol):
    """Strategy for extracting call sites from a symbol node."""

    def extract_calls(self, symbol_node: "Any", code: str) -> List[Call]:
        ...

    def extract_usages(self, symbol_node: "Any", code: str) -> List[Call]:
        ...


@runtime_checkable
class EndpointExtractionStrategy(Protocol):
    """Strategy for extracting API endpoints for a repository."""

    async def extract_endpoints(self, repository_id: int) -> List["Any"]:
        ...


@runtime_checkable
class DependencyManifestStrategy(Protocol):
    """Strategy for parsing dependency manifests."""

    dependency_type: str

    def supports(self, file_name: str) -> bool:
        ...

    def parse_file(self, file_path: Path) -> List["Any"]:
        ...


class NullCallStrategy:
    """No-op call strategy for unsupported languages."""

    def extract_calls(self, symbol_node: "Any", code: str) -> List[Call]:
        return []

    def extract_usages(self, symbol_node: "Any", code: str) -> List[Call]:
        return []


def language_strategy(
    registry: Dict[LanguageEnum, Any], language: LanguageEnum, default: Optional[Any] = None
) -> Optional[Any]:
    """Resolve a strategy by language with optional default fallback."""
    return registry.get(language, default)
