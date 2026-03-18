"""Extractor for outgoing API calls (HTTP) from code."""

from typing import List, Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import FileInstance as File, OutgoingApiCall
from src.database.query_helpers import active_file_filter
from src.config.enums import LanguageEnum
from src.utils.logging_config import get_logger
from src.parsers.javascript_parser import JavaScriptParser

logger = get_logger(__name__)


class OutgoingCallExtractor:
    """Extracts outgoing API calls from parsed code."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.js_parser = JavaScriptParser()

    async def extract_calls(self, repository_id: int) -> int:
        """Repository-level extractor entrypoint (currently orchestrated per file)."""
        calls_extracted = 0
        result = await self.session.execute(
            select(File).where(File.repository_id == repository_id, active_file_filter())
        )
        _ = result.scalars().all()
        return calls_extracted

    async def extract_from_file(self, file: File, repo_path: Any, content: Optional[str] = None) -> List[OutgoingApiCall]:
        """Extract calls from a single file."""
        calls: List[OutgoingApiCall] = []
        file_path = repo_path / file.path

        try:
            if content is None:
                if not file_path.exists():
                    return []

                max_file_size = 10 * 1024 * 1024
                if file_path.stat().st_size > max_file_size:
                    logger.warning(f"File too large, skipping: {file.path}")
                    return []

                content = file_path.read_text(encoding="utf-8-sig", errors="ignore")

            if file.language in [LanguageEnum.JAVASCRIPT, LanguageEnum.TYPESCRIPT]:
                calls.extend(self._extract_js_calls(content, file))
        except Exception as e:
            logger.error(f"Error extracting calls from {file.path}: {e}")

        return calls

    def _extract_js_calls(self, content: str, file: File) -> List[OutgoingApiCall]:
        """Extract HTTP calls from JS/TS code using parser metadata."""
        calls: List[OutgoingApiCall] = []

        try:
            parse_result = self.js_parser.parse(content, file.path)

            for api_call in parse_result.api_calls:
                calls.append(
                    OutgoingApiCall(
                        repository_id=file.repository_id,
                        file_instance_id=file.id,
                        http_method=api_call.get("http_method", "UNKNOWN"),
                        url_pattern=api_call.get("url_pattern", ""),
                        call_type=api_call.get("call_type", "frontend_to_backend"),
                        http_client_library=api_call.get("http_client_library", "unknown"),
                        line_number=api_call.get("line_number", 0),
                        is_dynamic_url=1 if api_call.get("is_dynamic_url", False) else 0,
                        context_metadata=api_call.get("context_metadata"),
                    )
                )
        except Exception as e:
            logger.error(f"Error parsing JS calls in {file.path}: {e}")

        return calls
