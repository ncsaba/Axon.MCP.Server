"""Search service for symbol lookup with hybrid search capabilities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional

from sqlalchemy import select, or_, case
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.search import SearchResult
from src.api.services.repository_grouping_service import (
    RepositoryGroupingService,
    RepositoryQueryScope,
)
from src.config.enums import LanguageEnum, SymbolKindEnum
from src.database.models import (
    FileInstance as File,
    Repository,
    Symbol,
    Chunk,
    ChunkSymbolLink,
    Embedding,
)
from src.database.query_helpers import active_file_filter
from src.embeddings.generator import EmbeddingGenerator
from src.vector_store.pgvector_store import PgVectorStore
from src.utils.logging_config import get_logger
from src.utils.metrics import search_duration, search_queries_total, search_results_count

logger = get_logger(__name__)

_CONFIG_QUERY_TERMS = {
    "config",
    "configuration",
    "setting",
    "settings",
    "database",
    "datasource",
    "jdbc",
    "db",
    "sql",
    "mysql",
    "postgres",
    "postgresql",
    "h2",
    "liquibase",
    "property",
    "properties",
    "build",
    "dependency",
    "dependencies",
    "jameica",
}

_API_QUERY_TERMS = {
    "api",
    "apis",
    "route",
    "routes",
    "router",
    "routers",
    "endpoint",
    "endpoints",
    "controller",
    "controllers",
    "http",
    "rest",
    "request",
    "requests",
}

_FRAMEWORK_QUERY_TERMS = {
    "framework",
    "frameworks",
    "plugin",
    "plugins",
    "wiring",
    "module",
    "modules",
    "jameica",
    "hibiscus",
    "mongo",
    "mongodb",
    "spring",
}

_UI_SURFACE_QUERY_TERMS = {
    "view",
    "views",
    "screen",
    "screens",
    "dialog",
    "dialogs",
    "menu",
    "menus",
    "gui",
    "ui",
}

_BACKGROUND_QUERY_TERMS = {
    "schedule",
    "scheduled",
    "scheduler",
    "job",
    "jobs",
    "background",
    "task",
    "tasks",
    "timer",
    "timers",
    "appointment",
    "appointments",
    "wiedervorlage",
}

_SCORING_QUERY_TERMS = {
    "score",
    "scores",
    "scoring",
    "ranking",
    "objective",
    "optimization",
    "optimizer",
    "quality",
    "evaluate",
    "evaluation",
    "logic",
}

_MEMBER_FLOW_QUERY_TERMS = {
    "member",
    "members",
    "mitglied",
    "mitglieder",
    "booking",
    "bookings",
    "buchung",
    "buchungen",
    "entrypoint",
    "flow",
    "import",
    "imports",
}

_CSV_MEMBER_IMPORT_QUERY_TERMS = {
    "csv",
    "import",
    "imports",
    "member",
    "members",
    "mitglied",
    "mitglieder",
}

_FRAMEWORK_VENDOR_TOKENS = {
    "jameica",
    "hibiscus",
    "mongo",
    "mongodb",
    "spring",
    "plugin",
    "mysql",
    "h2",
    "jdbc",
}

_CONFIG_PATH_MARKERS = (
    "config",
    "settings",
    "property",
    "properties",
    "database",
    "datasource",
    "jdbc",
    "liquibase",
    "build.xml",
    "pom.xml",
    "application.",
    ".sql",
    ".ddl",
)

_API_PATH_MARKERS = (
    "controller",
    "api",
    "route",
    "router",
    "rest",
    "application",
)

_BACKGROUND_PATH_MARKERS = (
    "calendar",
    "wiedervorlage",
    "background",
    "job",
    "queue",
    "thread",
    "task",
)

_UI_PATH_MARKERS = (
    "/gui/view/",
    "/gui/dialog",
    "/gui/menu/",
    "view.java",
    "dialog.java",
)

_SCORING_PATH_MARKERS = (
    "/optimizer/",
    "objectivefunction",
    "evaluator",
    "ruleevaluation",
    "orchestrator",
)

# Lazy import Redis cache (optional dependency)
_redis_cache = None


@dataclass
class SnippetSelection:
    """Selected snippet plus provenance for result packaging."""

    content: str
    match_type: str
    chunk_subtype: Optional[str] = None


async def _get_redis_cache():
    """Get Redis cache instance (lazy loaded)."""
    global _redis_cache
    if _redis_cache is None:
        try:
            from src.utils.redis_cache import get_cache
            _redis_cache = await get_cache()
        except Exception as e:
            logger.warning("redis_cache_unavailable", error=str(e))
            _redis_cache = False  # Mark as unavailable
    return _redis_cache if _redis_cache else None


class SearchService:
    """Hybrid search service combining keyword and semantic search."""
    
    # Singleton pattern for EmbeddingGenerator to avoid loading model multiple times
    # Note: Not thread-safe, but FastAPI's startup ensures single-threaded initialization
    _embedding_generator: Optional[EmbeddingGenerator] = None
    
    def __init__(self, session: AsyncSession, embedding_generator: Optional[EmbeddingGenerator] = None):
        """
        Initialize search service.
        
        Args:
            session: Database session
            embedding_generator: Optional pre-initialized embedding generator for dependency injection
        """
        self.session = session
        self.vector_store = PgVectorStore(session)
        self.grouping_service = RepositoryGroupingService(session)
        
        # Use provided generator or shared singleton
        if embedding_generator:
            self.embedding_generator = embedding_generator
        else:
            # Use class-level singleton (lazy-loaded on first use)
            # Note: This is not async-safe for the very first initialization,
            # but in practice FastAPI's startup ensures single-threaded init
            if SearchService._embedding_generator is None:
                logger.info("initializing_shared_embedding_generator")
                SearchService._embedding_generator = EmbeddingGenerator()
            self.embedding_generator = SearchService._embedding_generator
    
    async def search(
        self,
        query: str,
        limit: int = 20,
        repository_id: Optional[int] = None,
        language: Optional[LanguageEnum] = None,
        symbol_kind: Optional[SymbolKindEnum] = None,
        hybrid: bool = True
    ) -> List[SearchResult]:
        """
        Search for code symbols using hybrid approach with caching.
        
        Args:
            query: Search query
            limit: Maximum results
            repository_id: Filter by repository
            language: Filter by language
            symbol_kind: Filter by symbol kind
            hybrid: Use hybrid search (keyword + semantic)
            
        Returns:
            List of search results
            
        Raises:
            ValueError: If parameters are invalid
        """
        # SECURITY: Validate inputs to prevent abuse
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        
        if len(query) > 1000:
            raise ValueError(f"Query too long (max 1000 chars, got {len(query)})")
        
        if limit < 1 or limit > 100:
            raise ValueError(f"Limit must be between 1 and 100, got {limit}")
        
        if repository_id is not None and repository_id < 1:
            raise ValueError(f"Invalid repository_id: {repository_id}")
        
        search_type = "hybrid" if hybrid else "keyword"
        query_scope: Optional[RepositoryQueryScope] = None
        repository_scope_ids: Optional[List[int]] = None

        if repository_id is not None:
            query_scope = await self.grouping_service.get_query_scope(repository_id)
            repository_scope_ids = query_scope.repository_ids
        
        # Try to get from cache
        cache = await _get_redis_cache()
        cache_key = None
        
        if cache:
            # Generate cache key from parameters
            cache_key = self._generate_cache_key(
                query,
                limit,
                repository_id,
                repository_scope_ids,
                language,
                symbol_kind,
                hybrid,
            )
            
            # Try to get cached results
            cached_results = await cache.get(cache_key)
            if cached_results:
                logger.debug("search_cache_hit", query=query, cache_key=cache_key)
                # Convert cached dicts back to SearchResult objects
                # Need to deserialize updated_at from ISO string back to datetime
                for result in cached_results:
                    if result.get('updated_at') and isinstance(result['updated_at'], str):
                        result['updated_at'] = datetime.fromisoformat(result['updated_at'])
                return [SearchResult(**r) for r in cached_results]
        
        # FIXED: Proper error handling with metrics
        try:
            with search_duration.labels(search_type=search_type).time():
                if hybrid:
                    results = await self._hybrid_search(
                        query,
                        limit,
                        repository_id,
                        language,
                        symbol_kind,
                        repository_scope_ids=repository_scope_ids,
                        query_scope=query_scope,
                    )
                else:
                    results = await self._keyword_search(
                        query,
                        limit,
                        repository_id,
                        language,
                        symbol_kind,
                        repository_scope_ids=repository_scope_ids,
                        query_scope=query_scope,
                    )
                
                # Cache results if cache is available
                if cache and cache_key:
                    # Convert SearchResult objects to dicts for caching
                    cached_data = [
                        {
                            'symbol_id': r.symbol_id,
                            'file_id': r.file_id,
                            'repository_id': r.repository_id,
                            'repository_name': r.repository_name,
                            'file_path': r.file_path,
                            'language': r.language,
                            'kind': r.kind,
                            'name': r.name,
                            'fully_qualified_name': r.fully_qualified_name,
                            'signature': r.signature,
                            'documentation': r.documentation,
                            'code_snippet': r.code_snippet,
                            'start_line': r.start_line,
                            'end_line': r.end_line,
                            'score': r.score,
                            'match_type': r.match_type,
                            'snippet_match_type': r.snippet_match_type,
                            'match_reason': r.match_reason,
                            'follow_up_tools': r.follow_up_tools,
                            'query_scope_group': r.query_scope_group,
                            'query_scope_repositories': r.query_scope_repositories,
                            'updated_at': r.updated_at.isoformat() if r.updated_at else None,
                            'context_url': r.context_url
                        }
                        for r in results
                    ]
                    await cache.set(cache_key, cached_data, ttl=300)  # 5 minute TTL
                    logger.debug("search_results_cached", query=query, count=len(results))
                
                search_queries_total.labels(
                    search_type=search_type,
                    status="success"
                ).inc()
                
                search_results_count.labels(search_type=search_type).observe(len(results))
                
                logger.info(
                    "search_completed",
                    query=query,
                    result_count=len(results),
                    hybrid=hybrid
                )
                
                return results
                
        except ValueError as e:
            # Validation errors - don't log as errors, user input issue
            error_msg = f"Failed to search: {str(e)}"
            search_queries_total.labels(
                search_type=search_type,
                status="validation_error"
            ).inc()
            logger.warning("search_validation_failed", query=query, error=error_msg)
            raise
            
        except Exception as e:
            # System errors - log and track
            error_msg = f"Failed to search: {str(e)}"
            search_queries_total.labels(
                search_type=search_type,
                status="error"
            ).inc()
            logger.error(
                "search_failed",
                query=query,
                search_type=search_type,
                error=error_msg,
                exc_info=True
            )
            raise
    
    async def _hybrid_search(
        self,
        query: str,
        limit: int,
        repository_id: Optional[int],
        language: Optional[LanguageEnum],
        symbol_kind: Optional[SymbolKindEnum],
        repository_scope_ids: Optional[List[int]] = None,
        query_scope: Optional[RepositoryQueryScope] = None,
    ) -> List[SearchResult]:
        """Combine keyword and semantic search using reciprocal rank fusion."""
        keyword_task = asyncio.create_task(
            self._keyword_search(
                query,
                limit * 2,
                repository_id,
                language,
                symbol_kind,
                repository_scope_ids=repository_scope_ids,
                query_scope=query_scope,
                include_snippets=False,
            )
        )

        query_vector = await self.embedding_generator.generate_single_embedding(query)
        semantic_results: List[SearchResult] = []

        if query_vector:
            semantic_task = asyncio.create_task(
                self._semantic_search(
                    query,
                    limit * 2,
                    repository_id,
                    language,
                    symbol_kind,
                    repository_scope_ids=repository_scope_ids,
                    query_scope=query_scope,
                    query_vector=query_vector,
                    include_snippets=False,
                )
            )
            keyword_results, semantic_results = await asyncio.gather(keyword_task, semantic_task)
        else:
            keyword_results = await keyword_task

        query_tokens = self._tokenize_query(query)
        query_intents = self._infer_query_intents(query.lower(), query_tokens)
        keyword_weight, semantic_weight = self._get_hybrid_source_weights(query_intents)
        
        # Reciprocal rank fusion
        fused_results = self._reciprocal_rank_fusion(
            keyword_results, semantic_results, limit, keyword_weight=keyword_weight, semantic_weight=semantic_weight
        )
        await self._hydrate_search_results_with_snippets(
            fused_results,
            query,
            query_vector=query_vector if query_vector else None,
            embedding_model_name=self.embedding_generator.model_name if query_vector else None,
            embedding_model_version=self.embedding_generator.model_version if query_vector else None,
        )
        return fused_results
    
    async def _keyword_search(
        self,
        query: str,
        limit: int,
        repository_id: Optional[int],
        language: Optional[LanguageEnum],
        symbol_kind: Optional[SymbolKindEnum],
        repository_scope_ids: Optional[List[int]] = None,
        query_scope: Optional[RepositoryQueryScope] = None,
        include_snippets: bool = True,
    ) -> List[SearchResult]:
        """
        Perform keyword-based search with multi-word tokenization.
        
        ENHANCED: Tokenizes multi-word queries and searches for individual words,
        scoring higher when multiple words match. This makes search more flexible
        and likely to return relevant results.
        """
        query_lower = query.lower()
        
        # Tokenize query into individual words
        # Remove common words and split on whitespace/special chars
        query_tokens = self._tokenize_query(query)
        query_intents = self._infer_query_intents(query_lower, query_tokens)
        
        # Build search conditions for both full phrase and individual tokens.
        # Chunk content and file path are included so import/context enriched chunks
        # can participate in "uses X framework" style searches.
        search_conditions = []
        
        # Full phrase match (highest priority)
        search_conditions.append(Symbol.name.ilike(f"%{query}%"))
        search_conditions.append(Symbol.signature.ilike(f"%{query}%"))
        search_conditions.append(Symbol.documentation.ilike(f"%{query}%"))
        search_conditions.append(Symbol.fully_qualified_name.ilike(f"%{query}%"))
        search_conditions.append(File.path.ilike(f"%{query}%"))
        search_conditions.append(Chunk.content.ilike(f"%{query}%"))
        
        # Individual word matches (more flexible)
        for token in query_tokens:
            if len(token) >= 2:  # Skip very short tokens
                search_conditions.append(Symbol.name.ilike(f"%{token}%"))
                search_conditions.append(Symbol.signature.ilike(f"%{token}%"))
                search_conditions.append(Symbol.documentation.ilike(f"%{token}%"))
                search_conditions.append(Symbol.fully_qualified_name.ilike(f"%{token}%"))
                search_conditions.append(File.path.ilike(f"%{token}%"))
                search_conditions.append(Chunk.content.ilike(f"%{token}%"))
        
        # Build base query
        stmt = select(Symbol, File, Repository, Chunk.content).join(
            File, Symbol.file_instance_id == File.id
        ).join(
            Repository, File.repository_id == Repository.id
        ).outerjoin(
            ChunkSymbolLink, ChunkSymbolLink.symbol_id == Symbol.id
        ).outerjoin(
            Chunk, Chunk.id == ChunkSymbolLink.chunk_id
        )
        
        stmt = stmt.where(or_(*search_conditions), active_file_filter())
        
        # Apply filters
        if repository_scope_ids:
            stmt = stmt.where(Repository.id.in_(repository_scope_ids))
        elif repository_id:
            stmt = stmt.where(Repository.id == repository_id)
        if language:
            stmt = stmt.where(Symbol.language == language)
        if symbol_kind:
            stmt = stmt.where(Symbol.kind == symbol_kind)
        
        # Apply safety limit to prevent memory issues
        SAFETY_LIMIT = 10000
        stmt = stmt.limit(SAFETY_LIMIT)
        
        result = await self.session.execute(stmt)
        rows = result.all()

        # Get symbol IDs to fetch code snippets
        normalized_rows = [self._unpack_keyword_candidate_row(row) for row in rows]
        symbol_ids = list({symbol.id for symbol, _, _, _ in normalized_rows})
        code_snippets = (
            await self._get_code_snippets(symbol_ids, query=query)
            if include_snippets
            else {}
        )

        # Convert to SearchResult and calculate multi-word scores.
        # When chunk joins create multiple candidate rows per symbol, keep the best.
        best_results: Dict[int, SearchResult] = {}
        for symbol, file, repo, chunk_content in normalized_rows:
            snippet_selection = code_snippets.get(symbol.id)
            # Enhanced scoring with multi-word matching
            score = self._calculate_keyword_score_multiword(
                symbol,
                query_lower,
                query_tokens,
                file_path=file.path,
                chunk_content=chunk_content,
            )
            score += self._calculate_query_intent_boost(
                symbol=symbol,
                file_path=file.path,
                chunk_content=chunk_content,
                query_intents=query_intents,
                query_tokens=query_tokens,
            )
            score += self._calculate_repository_scope_boost(
                result_repository_id=repo.id,
                query_scope=query_scope,
            )

            search_result = SearchResult(
                symbol_id=symbol.id,
                file_id=file.id,
                repository_id=repo.id,
                name=symbol.name,
                kind=symbol.kind,
                language=symbol.language,
                signature=symbol.signature or "",
                file_path=file.path,
                repository_name=repo.name,
                fully_qualified_name=symbol.fully_qualified_name,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                documentation=symbol.documentation,
                code_snippet=snippet_selection.content if snippet_selection else None,
                score=score,
                match_type="keyword",
                snippet_match_type=snippet_selection.match_type if snippet_selection else None,
                match_reason=self._build_match_reason(
                    sources=["keyword"],
                    snippet_selection=snippet_selection,
                ),
                follow_up_tools=self._build_follow_up_tools(symbol),
                query_scope_group=query_scope.group_display_name if query_scope and query_scope.expanded else None,
                query_scope_repositories=query_scope.repository_names if query_scope and query_scope.expanded else [],
                updated_at=symbol.created_at,
                context_url=f"/api/symbols/{symbol.id}"  # Add context URL
            )

            current = best_results.get(symbol.id)
            if current is None or search_result.score > current.score:
                best_results[symbol.id] = search_result
        
        # Sort by refined score descending, then by name for deterministic ordering
        search_results = list(best_results.values())
        search_results.sort(key=lambda x: (-x.score, x.name))
        
        # Return top N results after refined scoring
        return search_results[:limit]
    
    async def _semantic_search(
        self,
        query: str,
        limit: int,
        repository_id: Optional[int],
        language: Optional[LanguageEnum],
        symbol_kind: Optional[SymbolKindEnum],
        repository_scope_ids: Optional[List[int]] = None,
        query_scope: Optional[RepositoryQueryScope] = None,
        query_vector: Optional[List[float]] = None,
        include_snippets: bool = True,
    ) -> List[SearchResult]:
        """Perform semantic search using embeddings."""
        if query_vector is None:
            query_vector = await self.embedding_generator.generate_single_embedding(query)
        
        if not query_vector:
            logger.warning("query_embedding_failed")
            return []
        
        # Build filters
        filters = {}
        if repository_scope_ids:
            filters['repository_ids'] = repository_scope_ids
        elif repository_id:
            filters['repository_id'] = repository_id
        if language:
            filters['language'] = language
        if symbol_kind:
            filters['symbol_kind'] = symbol_kind
        filters['embedding_model_name'] = self.embedding_generator.model_name
        filters['embedding_model_version'] = self.embedding_generator.model_version
        filters['embedding_dimension'] = self.embedding_generator.dimension
        query_tokens = self._tokenize_query(query)
        query_intents = self._infer_query_intents(query.lower(), query_tokens)
        
        # Perform vector search with file and repo info in single query
        # This fixes the N+1 query problem
        # ENHANCED: Lowered threshold from 0.7 to 0.5 for more flexible results
        similar_symbols = await self.vector_store.search_similar(
            query_vector=query_vector,
            query_text=query,
            limit=limit,
            threshold=0.5,  # More permissive threshold
            filters=filters,
            include_file_repo=True  # Fetch File and Repository in one query
        )

        if not similar_symbols:
            similar_symbols = await self.vector_store.search_similar(
                query_vector=query_vector,
                query_text=query,
                limit=limit,
                threshold=0.35,
                filters=filters,
                include_file_repo=True,
            )
        
        # Get code snippets for symbols
        symbol_ids = [symbol.id for symbol, _, _, _ in similar_symbols]
        code_snippets = (
            await self._get_code_snippets(
                symbol_ids,
                query=query,
                query_vector=query_vector,
                embedding_model_name=self.embedding_generator.model_name,
                embedding_model_version=self.embedding_generator.model_version,
            )
            if include_snippets
            else {}
        )
        
        # Convert to SearchResult
        search_results = []
        for symbol, similarity, file, repo in similar_symbols:
            if file and repo:  # Should always be present when include_file_repo=True
                snippet_selection = code_snippets.get(symbol.id)
                search_results.append(SearchResult(
                    symbol_id=symbol.id,
                    file_id=file.id,
                    repository_id=repo.id,
                    name=symbol.name,
                    kind=symbol.kind,
                    language=symbol.language,
                    signature=symbol.signature or "",
                    file_path=file.path,
                    repository_name=repo.name,
                    fully_qualified_name=symbol.fully_qualified_name,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    documentation=symbol.documentation,
                    code_snippet=snippet_selection.content if snippet_selection else None,
                    score=similarity + self._calculate_query_intent_boost(
                        symbol=symbol,
                        file_path=file.path,
                        chunk_content=snippet_selection.content if snippet_selection else None,
                        query_intents=query_intents,
                        query_tokens=query_tokens,
                    ) + self._calculate_repository_scope_boost(
                        result_repository_id=repo.id,
                        query_scope=query_scope,
                    ),
                    match_type="semantic",
                    snippet_match_type=snippet_selection.match_type if snippet_selection else None,
                    match_reason=self._build_match_reason(
                        sources=["semantic"],
                        snippet_selection=snippet_selection,
                    ),
                    follow_up_tools=self._build_follow_up_tools(symbol),
                    query_scope_group=query_scope.group_display_name if query_scope and query_scope.expanded else None,
                    query_scope_repositories=query_scope.repository_names if query_scope and query_scope.expanded else [],
                    updated_at=symbol.created_at,
                    context_url=f"/api/symbols/{symbol.id}"  # Add context URL
                ))
        
        return search_results
    
    def _generate_cache_key(
        self,
        query: str,
        limit: int,
        repository_id: Optional[int],
        repository_scope_ids: Optional[List[int]],
        language: Optional[LanguageEnum],
        symbol_kind: Optional[SymbolKindEnum],
        hybrid: bool
    ) -> str:
        """Generate cache key from search parameters."""
        # Create deterministic key
        key_parts = [
            "search",
            query,
            str(limit),
            str(repository_id) if repository_id else "all",
            ",".join(str(repo_id) for repo_id in (repository_scope_ids or [])) or "scope:all",
            language.value if language else "all",
            symbol_kind.value if symbol_kind else "all",
            "hybrid" if hybrid else "keyword"
        ]
        
        key_str = ":".join(key_parts)
        
        # Hash if too long
        if len(key_str) > 200:
            key_hash = hashlib.md5(key_str.encode()).hexdigest()
            return f"search:{key_hash}"
        
        return key_str
    
    async def _get_code_snippets(
        self,
        symbol_ids: List[int],
        query: Optional[str] = None,
        query_vector: Optional[List[float]] = None,
        embedding_model_name: Optional[str] = None,
        embedding_model_version: Optional[str] = None,
        max_length: int = 2000,
    ) -> Dict[int, SnippetSelection]:
        """
        Get code snippets for symbols from their chunks.
        
        Args:
            symbol_ids: List of symbol IDs
            max_length: Maximum snippet length
            
        Returns:
            Dict mapping symbol_id to code snippet
        """
        if not symbol_ids:
            return {}
        
        try:
            snippets: Dict[int, SnippetSelection] = {}

            if query_vector and embedding_model_name and embedding_model_version:
                snippets.update(
                    await self._get_semantic_code_snippets(
                        symbol_ids=symbol_ids,
                        query_vector=query_vector,
                        embedding_model_name=embedding_model_name,
                        embedding_model_version=embedding_model_version,
                        max_length=max_length,
                    )
                )

            missing_symbol_ids = [symbol_id for symbol_id in symbol_ids if symbol_id not in snippets]
            if missing_symbol_ids:
                snippets.update(
                    await self._get_ranked_code_snippets(
                        symbol_ids=missing_symbol_ids,
                        query=query,
                        max_length=max_length,
                    )
                )

            return snippets
            
        except Exception as e:
            logger.warning("failed_to_fetch_code_snippets", error=str(e))
            return {}

    async def _hydrate_search_results_with_snippets(
        self,
        results: List[SearchResult],
        query: str,
        *,
        query_vector: Optional[List[float]] = None,
        embedding_model_name: Optional[str] = None,
        embedding_model_version: Optional[str] = None,
    ) -> None:
        """Populate snippets once for the final ranked result set."""
        if not results:
            return

        snippets = await self._get_code_snippets(
            [result.symbol_id for result in results],
            query=query,
            query_vector=query_vector,
            embedding_model_name=embedding_model_name,
            embedding_model_version=embedding_model_version,
        )
        for result in results:
            snippet_selection = snippets.get(result.symbol_id)
            if snippet_selection is None:
                continue
            result.code_snippet = snippet_selection.content
            result.snippet_match_type = snippet_selection.match_type
            result.match_reason = self._build_match_reason(
                sources=self._extract_match_reason_sources(result),
                snippet_selection=snippet_selection,
            )

    def _extract_match_reason_sources(self, result: SearchResult) -> List[str]:
        """Recover the source labels for match-reason rebuilding after snippet hydration."""
        if result.match_type == "hybrid":
            if result.match_reason:
                source_label = result.match_reason.split(" via ", 1)[0]
                sources = [source for source in source_label.split("+") if source]
                if sources:
                    return sources
            return ["keyword", "semantic"]

        if result.match_type:
            return [result.match_type]

        return ["keyword"]

    async def _get_semantic_code_snippets(
        self,
        symbol_ids: List[int],
        query_vector: List[float],
        embedding_model_name: str,
        embedding_model_version: str,
        max_length: int,
    ) -> Dict[int, SnippetSelection]:
        stmt = (
            select(
                ChunkSymbolLink.symbol_id,
                Chunk.content,
                (1 - Embedding.vector.cosine_distance(query_vector)).label("vector_score"),
            )
            .join(Chunk, Chunk.id == ChunkSymbolLink.chunk_id)
            .join(Embedding, Embedding.chunk_id == Chunk.id)
            .where(
                ChunkSymbolLink.symbol_id.in_(symbol_ids),
                Embedding.model_name == embedding_model_name,
                Embedding.model_version == embedding_model_version,
            )
            .order_by(ChunkSymbolLink.symbol_id, case((Chunk.chunk_subtype == "implementation", 0), else_=1), (1 - Embedding.vector.cosine_distance(query_vector)).desc(), Chunk.id)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        snippets: Dict[int, SnippetSelection] = {}
        for symbol_id, content, _ in rows:
            if symbol_id not in snippets and content:
                snippets[symbol_id] = SnippetSelection(
                    content=self._truncate_snippet(content, max_length=max_length),
                    match_type="semantic",
                    chunk_subtype="implementation",
                )

        return snippets

    async def _get_ranked_code_snippets(
        self,
        symbol_ids: List[int],
        query: Optional[str],
        max_length: int,
    ) -> Dict[int, SnippetSelection]:
        stmt = (
            select(
                ChunkSymbolLink.symbol_id,
                Chunk.content,
                Chunk.chunk_subtype,
                Chunk.id,
            )
            .join(Chunk, Chunk.id == ChunkSymbolLink.chunk_id)
            .where(ChunkSymbolLink.symbol_id.in_(symbol_ids))
            .order_by(ChunkSymbolLink.symbol_id, Chunk.id)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        query_lower = query.lower() if query else None
        query_tokens = self._tokenize_query(query) if query else []

        best_rows: Dict[int, tuple[float, int, int, str, Optional[str]]] = {}
        for symbol_id, content, chunk_subtype, chunk_id in rows:
            if not content:
                continue

            score = self._score_chunk_content(
                content=content,
                chunk_subtype=chunk_subtype,
                query=query_lower,
                query_tokens=query_tokens,
            )
            subtype_rank = 0 if chunk_subtype == "implementation" else 1
            candidate = (score, -subtype_rank, -int(chunk_id), content, chunk_subtype)
            current = best_rows.get(symbol_id)
            if current is None or candidate > current:
                best_rows[symbol_id] = candidate

        return {
            symbol_id: SnippetSelection(
                content=self._truncate_snippet(content, max_length=max_length),
                match_type="text" if score > 0 else "fallback",
                chunk_subtype=chunk_subtype,
            )
            for symbol_id, (score, _, _, content, chunk_subtype) in best_rows.items()
        }

    def _score_chunk_content(
        self,
        content: str,
        chunk_subtype: Optional[str],
        query: Optional[str],
        query_tokens: List[str],
    ) -> float:
        score = 0.0
        content_lower = content.lower()

        if query:
            if query in content_lower:
                score += 10.0

            for token in query_tokens:
                if token in content_lower:
                    score += 1.5

        if chunk_subtype == "implementation":
            score += 0.5

        return score

    def _calculate_repository_scope_boost(
        self,
        *,
        result_repository_id: int,
        query_scope: Optional[RepositoryQueryScope],
    ) -> float:
        """Keep the requested repository ahead of related-group spillover results."""
        if query_scope is None or not query_scope.expanded:
            return 0.0

        if result_repository_id == query_scope.primary_repository_id:
            return 2.5

        if result_repository_id in (query_scope.support_repository_ids or []):
            return 0.2

        if result_repository_id in query_scope.repository_ids:
            return 0.8

        return 0.0

    def _infer_query_intents(self, query: str, query_tokens: List[str]) -> set[str]:
        intents: set[str] = set()
        token_set = set(query_tokens)

        if query and any(term in query for term in _CONFIG_QUERY_TERMS):
            intents.add("config_dependency")
        if token_set & _CONFIG_QUERY_TERMS:
            intents.add("config_dependency")

        if query and any(term in query for term in _API_QUERY_TERMS):
            intents.add("api_surface")
        if token_set & _API_QUERY_TERMS:
            intents.add("api_surface")

        if query and any(term in query for term in _FRAMEWORK_QUERY_TERMS):
            intents.add("framework_usage")
        if token_set & _FRAMEWORK_QUERY_TERMS:
            intents.add("framework_usage")

        if query and any(term in query for term in _UI_SURFACE_QUERY_TERMS):
            intents.add("ui_surface")
        if token_set & _UI_SURFACE_QUERY_TERMS:
            intents.add("ui_surface")

        if query and any(term in query for term in _BACKGROUND_QUERY_TERMS):
            intents.add("background_work")
        if token_set & _BACKGROUND_QUERY_TERMS:
            intents.add("background_work")

        if query and any(term in query for term in _SCORING_QUERY_TERMS):
            intents.add("scoring_logic")
        if token_set & _SCORING_QUERY_TERMS:
            intents.add("scoring_logic")

        member_tokens = {"member", "members", "mitglied", "mitglieder"}
        flow_tokens = {"booking", "bookings", "buchung", "buchungen", "entrypoint", "flow", "import", "imports"}
        if (token_set & member_tokens and token_set & flow_tokens) or (
            token_set & {"entrypoint", "flow"} and token_set & _MEMBER_FLOW_QUERY_TERMS
        ):
            intents.add("member_booking_flow")

        if "csv" in token_set and token_set & member_tokens and token_set & {"import", "imports"}:
            intents.add("csv_member_import")

        return intents

    def _calculate_query_intent_boost(
        self,
        symbol: Symbol,
        file_path: Optional[str],
        chunk_content: Optional[str],
        query_intents: set[str],
        query_tokens: Optional[List[str]] = None,
    ) -> float:
        file_path_lower = (file_path or "").lower()
        chunk_content_lower = (chunk_content or "").lower()
        symbol_name_lower = symbol.name.lower()
        symbol_fqn_lower = (symbol.fully_qualified_name or "").lower()
        symbol_doc_lower = (symbol.documentation or "").lower()
        token_set = set(query_tokens or [])

        score = 0.0

        if "test" not in token_set and any(
            marker in file_path_lower for marker in ("src/test/", "/tests/", "/test/", "/junit/")
        ):
            score -= 2.5

        if "config_dependency" in query_intents:
            if any(marker in file_path_lower for marker in _CONFIG_PATH_MARKERS):
                score += 2.0

            if any(marker in file_path_lower for marker in ("dbsupport", "dbtool", "datasource", "jdbc", "jvereindbservice")):
                score += 1.5

            if any(marker in symbol_name_lower for marker in ("config", "setting", "database", "datasource", "db", "jdbc")):
                score += 1.5

            if any(marker in symbol_name_lower for marker in ("dbsupport", "dbtool", "jvereindbservice", "jdbcdriver")):
                score += 2.0

            if any(marker in symbol_fqn_lower for marker in ("config", "setting", "database", "datasource", "db", "jdbc")):
                score += 1.0

            if any(marker in symbol_doc_lower for marker in ("database", "configuration", "datasource", "jdbc", "mysql", "postgres", "jameica", "liquibase", "mongo", "mongodb")):
                score += 0.75

            if any(marker in chunk_content_lower for marker in ("database", "configuration", "datasource", "jdbc", "mysql", "postgres", "jameica", "liquibase", "dbservice", "mongo", "mongodb")):
                score += 1.5

            for token in token_set & _FRAMEWORK_VENDOR_TOKENS:
                if token in file_path_lower:
                    score += 2.0
                if token in symbol_name_lower:
                    score += 2.0
                if token in symbol_fqn_lower or token in symbol_doc_lower or token in chunk_content_lower:
                    score += 1.5

            if symbol.kind in {SymbolKindEnum.MODULE, SymbolKindEnum.DOCUMENT_SECTION}:
                score += 0.5
            elif symbol.kind in {SymbolKindEnum.CLASS, SymbolKindEnum.INTERFACE}:
                score += 0.75

        if "api_surface" in query_intents:
            if any(marker in file_path_lower for marker in _API_PATH_MARKERS):
                score += 2.5
            if any(marker in symbol_name_lower for marker in ("controller", "api", "router", "route", "endpoint", "application")):
                score += 2.0
            if any(marker in symbol_fqn_lower for marker in ("controller", "api", "router", "endpoint")):
                score += 1.5
            if any(marker in chunk_content_lower for marker in ("restmodule", "requestmapping", "restcontroller", "controller", "endpoint", "http")):
                score += 2.5
            if any(marker in symbol_doc_lower for marker in ("controller", "endpoint", "route", "http", "rest")):
                score += 1.0
            if symbol.kind in {SymbolKindEnum.CLASS, SymbolKindEnum.METHOD, SymbolKindEnum.FUNCTION}:
                score += 0.75
            if symbol.kind == SymbolKindEnum.DOCUMENT_SECTION:
                score -= 0.5

        if "framework_usage" in query_intents:
            if any(token in file_path_lower for token in token_set & _FRAMEWORK_VENDOR_TOKENS):
                score += 2.5
            if any(token in symbol_name_lower for token in token_set & _FRAMEWORK_VENDOR_TOKENS):
                score += 2.0
            if any(token in symbol_fqn_lower for token in token_set & _FRAMEWORK_VENDOR_TOKENS):
                score += 2.0
            if any(token in symbol_doc_lower for token in token_set & _FRAMEWORK_VENDOR_TOKENS):
                score += 1.0
            if any(token in chunk_content_lower for token in token_set & _FRAMEWORK_VENDOR_TOKENS):
                score += 2.5
            if "plugin" in token_set and file_path_lower.endswith("plugin.xml"):
                score += 3.0
            if any(marker in symbol_name_lower for marker in ("plugin", "module")):
                score += 1.0
            if any(marker in chunk_content_lower for marker in ("de.willuhn.jameica", "application.getpluginloader", "abstractplugin", "lookup(jvereinplugin", "restmodule", "mongodb")):
                score += 2.0

        if "ui_surface" in query_intents:
            if any(marker in file_path_lower for marker in _UI_PATH_MARKERS):
                score += 3.0
            if any(marker in symbol_name_lower for marker in ("view", "dialog", "menu", "control")):
                score += 2.5
            if any(marker in symbol_fqn_lower for marker in (".gui.view.", ".gui.dialog", ".gui.menu.")):
                score += 2.0
            if any(marker in chunk_content_lower for marker in ("abstractview", "gui.getview", "buttonarea", "labelgroup")):
                score += 2.0
            if symbol.kind == SymbolKindEnum.CLASS:
                score += 0.5

        if "scoring_logic" in query_intents:
            if any(marker in file_path_lower for marker in _SCORING_PATH_MARKERS):
                score += 2.5
            if any(marker in symbol_name_lower for marker in ("objective", "evaluator", "evaluation", "optimizer", "orchestrator", "scoring", "ranking")):
                score += 2.0
            if any(marker in symbol_fqn_lower for marker in ("optimizer", "objective", "evaluation", "recoservice")):
                score += 1.5
            if any(marker in chunk_content_lower for marker in ("objective function", "rule count score", "coverage score", "quality score", "efficiency penalty", "evaluate(", "optimizationresult", "recommendation generation")):
                score += 2.5
            if any(marker in symbol_doc_lower for marker in ("objective function", "optimization", "recommendation quality", "evaluation", "scoring")):
                score += 1.5
            if symbol.kind in {SymbolKindEnum.CLASS, SymbolKindEnum.METHOD, SymbolKindEnum.FUNCTION}:
                score += 0.5
            if symbol.kind == SymbolKindEnum.DOCUMENT_SECTION:
                score -= 1.5

        if "background_work" in query_intents:
            if any(marker in file_path_lower for marker in _BACKGROUND_PATH_MARKERS):
                score += 2.0
            if any(marker in symbol_name_lower for marker in ("job", "queue", "thread", "task", "background", "appointmentprovider", "wiedervorlage")):
                score += 2.0
            if any(marker in chunk_content_lower for marker in ("backgroundtask", "runnable", "asyncExec", "appointmentprovider", "wiedervorlage")):
                score += 2.5
            if symbol.kind in {SymbolKindEnum.CLASS, SymbolKindEnum.METHOD, SymbolKindEnum.FUNCTION}:
                score += 0.5

        if "member_booking_flow" in query_intents:
            if any(marker in file_path_lower for marker in ("mitglied", "import", "buchung", "abrechnung")):
                score += 2.0
            if any(marker in symbol_name_lower for marker in ("mitglied", "import", "buchung", "abrechnung")):
                score += 2.0
            if any(marker in symbol_fqn_lower for marker in ("mitglied", "import", "buchung", "abrechnung")):
                score += 1.0
            if any(marker in chunk_content_lower for marker in ("imports a new member", "mitglied", "buchung", "abrechnung")):
                score += 2.0
            if "entrypoint" in token_set and any(marker in file_path_lower for marker in ("view", "action", "dialog", "import.java")):
                score += 1.5

        if "csv_member_import" in query_intents:
            if file_path_lower.endswith("/io/import.java") or file_path_lower.endswith("io/import.java"):
                score += 4.0
            if any(marker in file_path_lower for marker in ("mitglied", "member")):
                score += 3.0
            if any(marker in symbol_name_lower for marker in ("importmitglied", "mitglied", "member")):
                score += 3.0
            if "import" in symbol_name_lower:
                score += 3.5
            if "csv" in symbol_name_lower:
                score += 1.5
            if any(marker in chunk_content_lower for marker in ("imports a new member", "mitglied", "member")):
                score += 3.0
            if any(marker in chunk_content_lower for marker in ("csv", "separator", "fileextension")):
                score += 1.0
            if any(marker in symbol_name_lower for marker in ("export", "auswertung", "report")):
                score -= 3.0
            if any(marker in file_path_lower for marker in ("export", "auswertung", "report")):
                score -= 2.5
            if any(marker in symbol_name_lower for marker in ("formular", "buchung", "konto")):
                score -= 2.0
            if any(marker in file_path_lower for marker in ("formular", "buchung", "konto")):
                score -= 1.5

        return score

    def _build_match_reason(
        self,
        sources: List[str],
        snippet_selection: Optional[SnippetSelection] = None,
        snippet_match_type: Optional[str] = None,
    ) -> str:
        source_label = "+".join(sources)
        match_type = snippet_selection.match_type if snippet_selection else snippet_match_type
        if match_type is None:
            return source_label
        return f"{source_label} via {match_type}"

    def _build_follow_up_tools(self, symbol: Symbol) -> List[str]:
        tools = ["get_symbol_context", "find_usages"]
        if symbol.kind in {
            SymbolKindEnum.FUNCTION,
            SymbolKindEnum.METHOD,
            SymbolKindEnum.CLASS,
            SymbolKindEnum.INTERFACE,
        }:
            tools.append("get_call_hierarchy")
        return tools

    def _truncate_snippet(self, content: str, max_length: int) -> str:
        if len(content) > max_length:
            return content[:max_length] + "..."
        return content
    
    def _tokenize_query(self, query: str) -> List[str]:
        """
        Tokenize query into individual words, filtering out common stop words.
        
        Args:
            query: Search query string
            
        Returns:
            List of query tokens
        """
        # Common stop words to ignore (keep it minimal for code search)
        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
            'find', 'where', 'what', 'which', 'how', 'uses', 'using', 'used', 'define', 'defined',
            'is', 'are', 'do', 'we', 'the', 'this',
        }
        
        # Split on whitespace and special characters, keep alphanumeric
        tokens = re.findall(r'\w+', query.lower())
        
        # Filter out stop words and very short tokens
        tokens = [t for t in tokens if t not in stop_words and len(t) >= 2]

        normalized_tokens: List[str] = []
        seen: set[str] = set()
        for token in tokens:
            for variant in (token, self._singularize_token(token)):
                if variant and variant not in seen and len(variant) >= 2:
                    normalized_tokens.append(variant)
                    seen.add(variant)

        return normalized_tokens

    def _singularize_token(self, token: str) -> str:
        """Generate a light singular form for common plural query tokens."""
        if token.endswith("ies") and len(token) > 4:
            return token[:-3] + "y"
        if token.endswith("ses") and len(token) > 4:
            return token[:-2]
        if token.endswith("s") and len(token) > 4:
            return token[:-1]
        return token
    
    def _calculate_keyword_score(self, symbol: Symbol, query: str) -> float:
        """Calculate keyword relevance score (legacy single-phrase scoring)."""
        score = 0.0
        
        # Exact name match is highest priority
        if symbol.name.lower() == query:
            score += 1.0
        elif query in symbol.name.lower():
            score += 0.7
        
        # Signature match
        if symbol.signature and query in symbol.signature.lower():
            score += 0.3
        
        # Documentation match
        if symbol.documentation and query in symbol.documentation.lower():
            score += 0.2
        
        return score

    def _unpack_keyword_candidate_row(
        self,
        row: tuple,
    ) -> tuple[Symbol, File, Repository, Optional[str]]:
        """Normalize keyword candidate rows from either legacy or chunk-aware queries."""
        if len(row) == 4:
            return row
        if len(row) == 3:
            symbol, file, repo = row
            return symbol, file, repo, None
        raise ValueError(f"Unexpected keyword search row shape: {len(row)} columns")
    
    def _calculate_keyword_score_multiword(
        self,
        symbol: Symbol,
        query: str,
        tokens: List[str],
        file_path: Optional[str] = None,
        chunk_content: Optional[str] = None,
    ) -> float:
        """
        Calculate keyword relevance score with multi-word token matching.
        
        Args:
            symbol: Symbol to score
            query: Original full query
            tokens: Individual query tokens
            
        Returns:
            Relevance score (higher is better)
        """
        score = 0.0
        symbol_name_lower = symbol.name.lower()
        symbol_sig_lower = (symbol.signature or "").lower()
        symbol_doc_lower = (symbol.documentation or "").lower()
        symbol_fqn_lower = (symbol.fully_qualified_name or "").lower()
        file_path_lower = (file_path or "").lower()
        chunk_content_lower = (chunk_content or "").lower()
        
        # 1. Exact full phrase match (highest priority)
        if symbol_name_lower == query:
            score += 10.0
        elif query in symbol_name_lower:
            score += 5.0
        elif query in symbol_fqn_lower:
            score += 4.0
        elif query in symbol_sig_lower:
            score += 2.0
        elif query in symbol_doc_lower:
            score += 1.0
        elif query in file_path_lower:
            score += 3.0
        elif query in chunk_content_lower:
            score += 3.0
        
        # 2. Multi-word token matching (flexible matching)
        if tokens:
            matched_tokens = 0
            for token in tokens:
                token_found = False
                
                # Check name
                if token in symbol_name_lower:
                    score += 2.0
                    token_found = True
                
                # Check fully qualified name
                elif token in symbol_fqn_lower:
                    score += 1.5
                    token_found = True
                
                # Check signature
                elif token in symbol_sig_lower:
                    score += 1.0
                    token_found = True
                
                # Check documentation
                elif token in symbol_doc_lower:
                    score += 0.5
                    token_found = True
                elif token in file_path_lower:
                    score += 1.0
                    token_found = True
                elif token in chunk_content_lower:
                    score += 0.75
                    token_found = True
                
                if token_found:
                    matched_tokens += 1
            
            # Bonus for matching multiple tokens (indicates better relevance)
            if len(tokens) > 1:
                match_ratio = matched_tokens / len(tokens)
                score += match_ratio * 3.0  # Up to 3 bonus points
        
        return score
    
    def _reciprocal_rank_fusion(
        self,
        keyword_results: List[SearchResult],
        semantic_results: List[SearchResult],
        limit: int,
        k: int = 60,
        keyword_weight: float = 1.0,
        semantic_weight: float = 1.0,
    ) -> List[SearchResult]:
        """
        Combine results using reciprocal rank fusion.
        
        Args:
            keyword_results: Results from keyword search
            semantic_results: Results from semantic search
            limit: Number of results to return
            k: RRF constant (typically 60)
            
        Returns:
            Fused results
        """
        # Build fusion scores
        fusion_scores: Dict[int, float] = {}
        result_map: Dict[int, SearchResult] = {}
        native_scores: Dict[int, float] = {}
        source_map: Dict[int, set[str]] = {}
        
        # Add keyword results
        for rank, result in enumerate(keyword_results, 1):
            fusion_scores[result.symbol_id] = fusion_scores.get(result.symbol_id, 0) + (keyword_weight / (k + rank))
            result_map[result.symbol_id] = result
            native_scores[result.symbol_id] = max(native_scores.get(result.symbol_id, float("-inf")), result.score)
            source_map.setdefault(result.symbol_id, set()).add("keyword")
        
        # Add semantic results
        for rank, result in enumerate(semantic_results, 1):
            fusion_scores[result.symbol_id] = fusion_scores.get(result.symbol_id, 0) + (semantic_weight / (k + rank))
            result_map[result.symbol_id] = result
            native_scores[result.symbol_id] = max(native_scores.get(result.symbol_id, float("-inf")), result.score)
            source_map.setdefault(result.symbol_id, set()).add("semantic")

        # Sort by fusion score
        sorted_ids = sorted(
            fusion_scores.keys(),
            key=lambda x: (fusion_scores[x], native_scores.get(x, 0.0)),
            reverse=True,
        )
        
        # Return top results
        results = []
        for symbol_id in sorted_ids[:limit]:
            result = result_map[symbol_id]
            result.score = native_scores.get(symbol_id, fusion_scores[symbol_id])
            result.match_type = "hybrid"
            result.match_reason = self._build_match_reason(
                sources=sorted(source_map.get(symbol_id, {"keyword", "semantic"})),
                snippet_match_type=result.snippet_match_type,
            )
            results.append(result)
        
        return results

    def _get_hybrid_source_weights(self, query_intents: set[str]) -> tuple[float, float]:
        keyword_weight = 1.0
        semantic_weight = 1.0

        if "csv_member_import" in query_intents:
            return 10.0, 0.1

        if "api_surface" in query_intents or "ui_surface" in query_intents:
            keyword_weight = 1.5
            semantic_weight = 1.0

        if "scoring_logic" in query_intents:
            keyword_weight = 1.5
            semantic_weight = 0.9

        return keyword_weight, semantic_weight
