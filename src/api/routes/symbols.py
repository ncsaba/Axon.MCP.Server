"""Symbol inspection and listing endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db_session
from src.api.schemas.symbols import SymbolResponse, SymbolWithRelations
from src.api.schemas.repositories import PaginatedResponse
from src.api.services.symbol_service import SymbolService
from src.api.auth import get_current_user
from src.config.enums import LanguageEnum, SymbolKindEnum


router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/symbols", response_model=PaginatedResponse[SymbolResponse])
async def list_symbols(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    repository_id: int | None = Query(None, ge=1),
    language: LanguageEnum | None = None,
    symbol_kind: SymbolKindEnum | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[SymbolResponse]:
    """Return a paginated list of symbols across repositories."""

    service = SymbolService(session)
    items, total = await service.list_symbols(
        offset=skip,
        limit=limit,
        repository_id=repository_id,
        language=language,
        symbol_kind=symbol_kind,
    )
    return PaginatedResponse(items=items, total=total, limit=limit, offset=skip)


@router.get("/files/{file_id}/symbols", response_model=PaginatedResponse[SymbolResponse])
async def list_file_symbols(
    file_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    symbol_kind: SymbolKindEnum | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[SymbolResponse]:
    """Return symbols defined in a specific file."""

    service = SymbolService(session)
    items, total = await service.list_symbols(
        offset=skip,
        limit=limit,
        file_id=file_id,
        symbol_kind=symbol_kind,
    )
    return PaginatedResponse(items=items, total=total, limit=limit, offset=skip)


@router.get("/symbols/{symbol_id}", response_model=SymbolResponse)
async def get_symbol(symbol_id: int, session: AsyncSession = Depends(get_db_session)) -> SymbolResponse:
    """Fetch metadata for a single symbol."""

    service = SymbolService(session)
    symbol = await service.get_symbol(symbol_id)
    if symbol is None:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return symbol


@router.get("/symbols/{symbol_id}/relationships", response_model=SymbolWithRelations)
async def get_symbol_relations(
    symbol_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> SymbolWithRelations:
    """Fetch symbol details including outgoing relationships."""

    service = SymbolService(session)
    symbol = await service.get_symbol_with_relations(symbol_id)
    if symbol is None:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return symbol

