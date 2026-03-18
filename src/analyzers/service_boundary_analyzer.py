import logging
from pathlib import PurePosixPath
from typing import Dict, List

from sqlalchemy import String, cast, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.enums import SymbolKindEnum
from src.database.models import File, Repository, Service, Symbol
from src.database.query_helpers import active_file_filter
from src.utils.async_compat import maybe_await

logger = logging.getLogger(__name__)


class ServiceBoundaryAnalyzer:
    """Detect service boundaries from file/symbol structure (project-table free)."""

    def _service_key_for_path(self, file_path: str) -> str:
        parts = PurePosixPath(file_path).parts
        if not parts:
            return "root"
        return parts[0]

    def _service_name(self, repository_name: str, service_key: str) -> str:
        if service_key == "root":
            return repository_name
        return f"{repository_name}:{service_key}"

    def _service_type_for_key(self, service_key: str, has_controller: bool) -> str:
        if has_controller:
            return "API"
        key = service_key.lower()
        if "worker" in key or "job" in key or "queue" in key:
            return "Worker"
        if "cli" in key or "console" in key:
            return "Console"
        return "Library"

    async def detect_services(self, repository: Repository, session: AsyncSession) -> List[Service]:
        """Detect and upsert services grouped by top-level path segment."""
        # Find potential controller classes and their files.
        result = await session.execute(
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository.id,
                active_file_filter(),
                Symbol.kind == SymbolKindEnum.CLASS,
                (Symbol.name.like("%Controller"))
                | (cast(Symbol.attributes, String).like("%ApiController%"))
                | (cast(Symbol.attributes, String).like("%Route%")),
            )
        )
        controller_rows = result.all()

        controllers_by_group: Dict[str, List[Symbol]] = {}
        for symbol, file_obj in controller_rows:
            group = self._service_key_for_path(file_obj.path)
            controllers_by_group.setdefault(group, []).append(symbol)

        # If no controllers exist, still ensure one repository-level service fallback.
        using_root_fallback = not controllers_by_group
        candidate_groups = set(controllers_by_group.keys()) or {"root"}
        detected_services: List[Service] = []

        for group in sorted(candidate_groups):
            service_name = self._service_name(repository.name, group)
            controllers = controllers_by_group.get(group, [])
            has_controller = len(controllers) > 0

            service_type = self._service_type_for_key(group, has_controller)
            reasons = []
            if has_controller:
                reasons.append(f"Found {len(controllers)} controllers")
            else:
                reasons.append("No controllers found; fallback grouping")

            entry_points = [
                {
                    "type": "controller",
                    "name": ctrl.name,
                    "fully_qualified_name": ctrl.fully_qualified_name,
                }
                for ctrl in controllers
            ]

            existing_result = await session.execute(
                select(Service).where(
                    Service.repository_id == repository.id,
                    Service.name == service_name,
                )
            )
            service_obj = existing_result.scalar_one_or_none()

            if service_obj:
                service_obj.service_type = service_type
                service_obj.description = f"Detected service. Reasons: {', '.join(reasons)}"
                service_obj.project_path = group
                service_obj.entry_points = entry_points if entry_points else None
            else:
                service_obj = Service(
                    repository_id=repository.id,
                    name=service_name,
                    service_type=service_type,
                    description=f"Detected service. Reasons: {', '.join(reasons)}",
                    project_path=group,
                    entry_points=entry_points if entry_points else None,
                )
                await maybe_await(session.add(service_obj))
                await session.flush()

            # Link symbols under the same top-level group to the detected service.
            if group == "root":
                if using_root_fallback:
                    # No controller-derived grouping exists, so the repository-wide
                    # fallback service should own the whole repository.
                    root_ids_result = await session.execute(
                        select(File.id).where(
                            File.repository_id == repository.id,
                            active_file_filter(),
                        )
                    )
                    file_ids = [row[0] for row in root_ids_result.all()]
                else:
                    # root service: symbols in files without '/' segment
                    root_ids_result = await session.execute(
                        select(File.id).where(
                            File.repository_id == repository.id,
                            active_file_filter(),
                            ~File.path.contains("/"),
                        )
                    )
                    file_ids = [row[0] for row in root_ids_result.all()]
            else:
                file_ids_result = await session.execute(
                    select(File.id).where(
                        File.repository_id == repository.id,
                        active_file_filter(),
                        File.path.like(f"{group}/%"),
                    )
                )
                file_ids = [row[0] for row in file_ids_result.all()]

            if file_ids:
                await session.execute(
                    update(Symbol)
                    .where(Symbol.file_id.in_(file_ids))
                    .values(service_id=service_obj.id)
                )

            detected_services.append(service_obj)

        logger.info(
            "service_detection_completed repository_id=%s services_detected=%s",
            repository.id,
            len(detected_services),
        )
        return detected_services
