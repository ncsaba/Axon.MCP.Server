"""Extractor for event publishing and subscriptions from code."""

from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import FileInstance as File, PublishedEvent, EventSubscription
from src.config.enums import LanguageEnum
from src.utils.logging_config import get_logger
from src.parsers.javascript_parser import JavaScriptParser

logger = get_logger(__name__)


class EventExtractor:
    """Extracts event publishing and subscriptions from parsed code."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.js_parser = JavaScriptParser()

    async def extract_events(self, repository_id: int) -> int:
        """Repository-level extractor entrypoint (currently orchestrated per file)."""
        return 0

    async def extract_from_file(self, file: File, repo_path: Any, content: Optional[str] = None) -> Dict[str, List[Any]]:
        """Extract events from a single file."""
        result: Dict[str, List[Any]] = {"published": [], "subscribed": []}
        file_path = repo_path / file.path

        try:
            if content is None:
                if not file_path.exists():
                    return result
                content = file_path.read_text(encoding="utf-8-sig", errors="ignore")

            if file.language in [LanguageEnum.JAVASCRIPT, LanguageEnum.TYPESCRIPT]:
                events = self._extract_js_events(content, file)
                result["published"].extend(events["published"])
                result["subscribed"].extend(events["subscribed"])
        except Exception as e:
            logger.error(f"Error extracting events from {file.path}: {e}")

        return result

    def _extract_js_events(self, content: str, file: File) -> Dict[str, List[Any]]:
        """Extract events from JS/TS code using AST parser."""
        published: List[PublishedEvent] = []
        subscribed: List[EventSubscription] = []

        try:
            parse_result = self.js_parser.parse(content, file.path)

            for event in parse_result.events:
                event_type = event.get("type")

                if event_type == "publish":
                    published.append(
                        PublishedEvent(
                            repository_id=file.repository_id,
                            file_instance_id=file.id,
                            event_type_name=event.get("event_type_name", "UnknownEvent"),
                            messaging_library=event.get("messaging_library", "unknown"),
                            topic_name=event.get("topic_name"),
                            routing_key=event.get("routing_key"),
                            line_number=event.get("line_number", 0),
                            event_metadata=event.get("event_metadata"),
                        )
                    )
                elif event_type == "subscribe":
                    subscribed.append(
                        EventSubscription(
                            repository_id=file.repository_id,
                            file_instance_id=file.id,
                            event_type_name=event.get("event_type_name", "UnknownEvent"),
                            messaging_library=event.get("messaging_library", "unknown"),
                            queue_name=event.get("queue_name"),
                            line_number=event.get("line_number", 0),
                            handler_metadata=event.get("event_metadata"),
                        )
                    )
        except Exception as e:
            logger.error(f"Error parsing JS events in {file.path}: {e}")

        return {"published": published, "subscribed": subscribed}
