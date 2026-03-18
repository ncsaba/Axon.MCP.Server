"""Symbol query helpers."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.symbols import RelationEdge, SymbolResponse, SymbolWithRelations
from src.config.enums import LanguageEnum, SymbolKindEnum
from src.database.models import File, Relation, Repository, Symbol
from src.database.query_helpers import active_file_filter


class SymbolService:
    """Provide read operations for symbols and relationships."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _normalize_parameters(parameters: object) -> Optional[dict]:
        """Normalize legacy parameter payloads to schema-compatible dict values."""
        if parameters is None:
            return None

        if isinstance(parameters, dict):
            return parameters

        if isinstance(parameters, (list, tuple)):
            if not parameters:
                return None
            return {f"param_{i}": value for i, value in enumerate(parameters)}

        # Unknown/invalid payload shape should not break API responses.
        return None

    def _to_symbol_response(self, symbol: Symbol, file: File, repository: Repository) -> SymbolResponse:
        """Convert DB rows to response schema."""
        return SymbolResponse(
            id=symbol.id,
            file_id=file.id,
            repository_id=repository.id,
            language=symbol.language,
            kind=symbol.kind,
            access_modifier=symbol.access_modifier,
            name=symbol.name,
            fully_qualified_name=symbol.fully_qualified_name,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            signature=symbol.signature,
            documentation=symbol.documentation,
            parameters=self._normalize_parameters(symbol.parameters),
            return_type=symbol.return_type,
            parent_symbol_id=symbol.parent_symbol_id,
            created_at=symbol.created_at,
        )

    async def get_symbol(self, symbol_id: int) -> Optional[SymbolResponse]:
        stmt: Select = (
            select(Symbol, File, Repository)
            .join(File, Symbol.file_id == File.id)
            .join(Repository, File.repository_id == Repository.id)
            .where(Symbol.id == symbol_id, active_file_filter())
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None

        symbol, file, repository = row
        return self._to_symbol_response(symbol, file, repository)

    async def list_symbols(
        self,
        offset: int,
        limit: int,
        repository_id: Optional[int] = None,
        file_id: Optional[int] = None,
        language: Optional[LanguageEnum] = None,
        symbol_kind: Optional[SymbolKindEnum] = None,
    ) -> tuple[list[SymbolResponse], int]:
        """Return paginated symbols with optional filters."""
        filters = []
        filters.append(active_file_filter())
        if repository_id is not None:
            filters.append(File.repository_id == repository_id)
        if file_id is not None:
            filters.append(Symbol.file_id == file_id)
        if language is not None:
            filters.append(Symbol.language == language)
        if symbol_kind is not None:
            filters.append(Symbol.kind == symbol_kind)

        stmt: Select = (
            select(Symbol, File, Repository)
            .join(File, Symbol.file_id == File.id)
            .join(Repository, File.repository_id == Repository.id)
            .order_by(Symbol.id.asc())
            .offset(offset)
            .limit(limit)
        )
        count_stmt: Select = (
            select(func.count(Symbol.id))
            .join(File, Symbol.file_id == File.id)
        )

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        rows = (await self._session.execute(stmt)).all()
        items = [self._to_symbol_response(symbol, file, repository) for symbol, file, repository in rows]
        total = int((await self._session.execute(count_stmt)).scalar_one())
        return items, total

    async def get_symbol_with_relations(self, symbol_id: int) -> Optional[SymbolWithRelations]:
        symbol = await self.get_symbol(symbol_id)
        if symbol is None:
            return None

        relations_stmt: Select = (
            select(Relation, Symbol)
            .join(Symbol, Relation.to_symbol_id == Symbol.id)
            .where(Relation.from_symbol_id == symbol_id)
            .order_by(Relation.id.asc())
        )
        result = await self._session.execute(relations_stmt)
        edges = []
        for relation, target in result.all():
            edges.append(
                RelationEdge(
                    id=relation.id,
                    relation_type=relation.relation_type,
                    to_symbol_id=relation.to_symbol_id,
                    to_symbol_name=target.name,
                    to_symbol_kind=target.kind,
                )
            )

        return SymbolWithRelations(**symbol.model_dump(), relations=edges)
