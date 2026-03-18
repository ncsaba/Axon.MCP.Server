from typing import List, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from src.database.models import Embedding, Chunk, Symbol, File, Repository
from src.database.query_helpers import active_file_filter
from src.config.embedding_contract import FIXED_EMBEDDING_DIMENSION
from src.embeddings.generator import EmbeddingResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class PgVectorStore:
    """pgvector-based vector store for semantic search."""

    DEFAULT_VECTOR_INDEX_NAME = "embeddings_vector_idx"
    
    def __init__(self, session: AsyncSession):
        """
        Initialize vector store.
        
        Args:
            session: Database session
        """
        self.session = session
    
    async def store_embeddings(
        self,
        results: List[EmbeddingResult]
    ) -> int:
        """
        Store embeddings in database with proper transaction management.
        
        Args:
            results: List of embedding results
            
        Returns:
            Number of embeddings stored
            
        Raises:
            Exception: If critical error occurs during storage
        """
        if not results:
            return 0
        
        stored_count = 0
        
        try:
            # FIXED: Batch fetch all chunks to avoid N+1 query
            chunk_ids = [result.chunk_id for result in results]
            chunks_result = await self.session.execute(
                select(Chunk).where(Chunk.id.in_(chunk_ids))
            )
            chunks_by_id = {chunk.id: chunk for chunk in chunks_result.scalars()}
            
            # Create all embedding records
            embeddings_to_add = []
            for result in results:
                if result.dimension != FIXED_EMBEDDING_DIMENSION:
                    raise ValueError(
                        f"Embedding dimension {result.dimension} does not match fixed contract "
                        f"{FIXED_EMBEDDING_DIMENSION}"
                    )
                if len(result.vector) != FIXED_EMBEDDING_DIMENSION:
                    raise ValueError(
                        f"Embedding vector length {len(result.vector)} does not match fixed contract "
                        f"{FIXED_EMBEDDING_DIMENSION}"
                    )
                chunk = chunks_by_id.get(result.chunk_id)
                
                if not chunk:
                    logger.warning("chunk_not_found", chunk_id=result.chunk_id)
                    continue
                
                # Create embedding record
                embedding = Embedding(
                    chunk_id=result.chunk_id,
                    symbol_id=chunk.symbol_id,
                    model_name=result.model_name,
                    model_version=result.model_version,
                    dimension=result.dimension,
                    vector=result.vector
                )
                
                embeddings_to_add.append(embedding)
                stored_count += 1
            
            # FIXED: Add all embeddings in batch
            if embeddings_to_add:
                self.session.add_all(embeddings_to_add)
                await self.session.flush()
                logger.info("embeddings_stored", count=stored_count)
            else:
                logger.warning("no_valid_embeddings_to_store")
            
            return stored_count
            
        except Exception as e:
            error_msg = f"Failed to store embeddings: {str(e)}"
            logger.error(
                "embedding_storage_failed",
                total_results=len(results),
                error=error_msg
            )
            raise
    
    async def search_similar(
        self,
        query_vector: List[float],
        query_text: Optional[str] = None,
        limit: int = 10,
        threshold: float = 0.7,
        filters: Optional[Dict] = None,
        include_file_repo: bool = False
    ) -> List[Tuple[Symbol, float, Optional[File], Optional[Repository]]]:
        """
        Search for similar symbols using hybrid search (vector + keyword).
        
        Args:
            query_vector: Query embedding vector
            query_text: Optional text query for keyword matching and boosting
            limit: Maximum number of results
            threshold: Similarity threshold
            filters: Optional filters (language, repository_id, etc.)
            include_file_repo: If True, join and return File and Repository objects
            
        Returns:
            List of (Symbol, similarity_score, File?, Repository?) tuples
            
        Raises:
            ValueError: If parameters are invalid
        """
        # SECURITY: Validate inputs
        if not query_vector or not isinstance(query_vector, list):
            raise ValueError("query_vector must be a non-empty list")
        
        if limit < 1 or limit > 1000:
            raise ValueError(f"limit must be between 1 and 1000, got {limit}")
        
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError(f"threshold must be between 0.0 and 1.0, got {threshold}")
        
        from sqlalchemy import func, or_

        filters = dict(filters or {})
        query_dimension = int(filters.pop("embedding_dimension", FIXED_EMBEDDING_DIMENSION))
        if query_dimension != FIXED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding_dimension {query_dimension} does not match fixed contract "
                f"{FIXED_EMBEDDING_DIMENSION}"
            )
        if len(query_vector) != FIXED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"query_vector length {len(query_vector)} does not match fixed contract "
                f"{FIXED_EMBEDDING_DIMENSION}"
            )

        query_model_name = filters.pop("embedding_model_name", None)
        query_model_version = filters.pop("embedding_model_version", None)

        vector_distance_expr = Embedding.vector.cosine_distance(query_vector)
        vector_score_expr = (1 - vector_distance_expr)
        
        # 1. Vector Search
        # Fetch more candidate chunks than the final symbol limit, then deduplicate by symbol.
        # Using ORDER BY distance + LIMIT keeps the query in a shape that pgvector ANN indexes can use.
        vector_limit = max(limit * 10, 50)
        
        candidate_stmt = select(
            Embedding.symbol_id,
            vector_score_expr.label("vector_score"),
        ).where(Embedding.dimension == FIXED_EMBEDDING_DIMENSION)

        if query_model_name:
            candidate_stmt = candidate_stmt.where(Embedding.model_name == query_model_name)
        if query_model_version:
            candidate_stmt = candidate_stmt.where(Embedding.model_version == query_model_version)

        candidate_subq = candidate_stmt.order_by(vector_distance_expr).limit(vector_limit).subquery()

        # Subquery to get best similarity per symbol
        vector_subq = select(
            candidate_subq.c.symbol_id,
            func.max(candidate_subq.c.vector_score).label("vector_score"),
        ).where(
            candidate_subq.c.vector_score >= threshold
        ).group_by(candidate_subq.c.symbol_id).subquery()
        
        vector_query = select(
            Symbol,
            vector_subq.c.vector_score,
            File,
            Repository
        ).join(
            vector_subq,
            Symbol.id == vector_subq.c.symbol_id
        ).join(
            File, Symbol.file_id == File.id
        ).join(
            Repository, File.repository_id == Repository.id
        ).where(
            active_file_filter()
        )
        
        # Apply filters to vector query
        if filters:
            if 'language' in filters:
                vector_query = vector_query.where(Symbol.language == filters['language'])
            if 'repository_id' in filters:
                vector_query = vector_query.where(File.repository_id == filters['repository_id'])
            if 'symbol_kind' in filters:
                vector_query = vector_query.where(Symbol.kind == filters['symbol_kind'])
                
        vector_query = vector_query.order_by(text('vector_score DESC')).limit(vector_limit)
        
        vector_results = await self.session.execute(vector_query)
        vector_candidates = vector_results.all()
        
        # 2. Keyword Search (if query_text provided)
        keyword_candidates = []
        if query_text:
            keyword_limit = limit
            keyword_query = select(
                Symbol,
                File,
                Repository
            ).join(
                File, Symbol.file_id == File.id
            ).join(
                Repository, File.repository_id == Repository.id
            ).where(
                or_(
                    Symbol.name.ilike(f"%{query_text}%"),
                    Symbol.fully_qualified_name.ilike(f"%{query_text}%")
                ),
                active_file_filter(),
            )
            
            # Apply filters to keyword query
            if filters:
                if 'language' in filters:
                    keyword_query = keyword_query.where(Symbol.language == filters['language'])
                if 'repository_id' in filters:
                    keyword_query = keyword_query.where(File.repository_id == filters['repository_id'])
                if 'symbol_kind' in filters:
                    keyword_query = keyword_query.where(Symbol.kind == filters['symbol_kind'])
            
            keyword_query = keyword_query.limit(keyword_limit)
            
            keyword_results = await self.session.execute(keyword_query)
            keyword_candidates = keyword_results.all()
            
        # 3. Merge and Score
        # Map symbol_id -> (Symbol, vector_score, File, Repository)
        merged_results = {}
        
        # Process vector candidates
        for row in vector_candidates:
            merged_results[row.Symbol.id] = {
                'symbol': row.Symbol,
                'vector_score': row.vector_score,
                'file': row.File,
                'repo': row.Repository,
                'keyword_match': False
            }
            
        # Process keyword candidates
        for row in keyword_candidates:
            if row.Symbol.id in merged_results:
                merged_results[row.Symbol.id]['keyword_match'] = True
            else:
                merged_results[row.Symbol.id] = {
                    'symbol': row.Symbol,
                    'vector_score': 0.0, # No vector match
                    'file': row.File,
                    'repo': row.Repository,
                    'keyword_match': True
                }
        
        # Calculate final scores
        scored_results = []
        
        for data in merged_results.values():
            symbol = data['symbol']
            file = data['file']
            base_score = float(data['vector_score'])
            
            # Boosting Logic
            boost = 0.0
            
            if query_text:
                # Exact match boost
                if symbol.name.lower() == query_text.lower():
                    boost += 0.5
                # Partial match boost
                elif query_text.lower() in symbol.name.lower():
                    boost += 0.2
                
                # Path boost
                if file and query_text.lower() in file.path.lower():
                    boost += 0.1
            
            # Symbol type boost
            # Prefer high-level symbols
            if symbol.kind in ('CLASS', 'INTERFACE', 'STRUCT', 'MODULE'):
                boost += 0.1
            elif symbol.kind in ('FUNCTION', 'METHOD'):
                boost += 0.05
                
            final_score = base_score + boost
            
            scored_results.append((
                symbol,
                final_score,
                file if include_file_repo else None,
                data['repo'] if include_file_repo else None
            ))
            
        # 4. Sort and Return
        scored_results.sort(key=lambda x: x[1], reverse=True)
        
        return scored_results[:limit]

    @staticmethod
    def _extract_index_type(index_definition: Optional[str]) -> Optional[str]:
        """Extract the pgvector index method from a CREATE INDEX definition."""
        if not index_definition:
            return None

        lowered = index_definition.lower()
        if " using hnsw " in lowered:
            return "hnsw"
        if " using ivfflat " in lowered:
            return "ivfflat"
        return None

    async def get_vector_index_type(
        self,
        index_name: str = DEFAULT_VECTOR_INDEX_NAME,
    ) -> Optional[str]:
        """Return the configured pgvector ANN index type for embeddings, if present."""
        result = await self.session.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE tablename = 'embeddings' AND indexname = :index_name
                ORDER BY schemaname = 'public' DESC, schemaname ASC
                LIMIT 1
                """
            ),
            {"index_name": index_name},
        )
        row = result.first()
        if not row:
            return None
        return self._extract_index_type(row[0])

    async def ensure_vector_index(
        self,
        index_type: str = "hnsw",
        lists: int = 100,
        index_name: str = DEFAULT_VECTOR_INDEX_NAME,
    ) -> str:
        """
        Ensure the fixed-dimension pgvector ANN index exists for semantic search.

        Returns:
            One of: "created", "existing", "mismatched"
        """
        if index_type not in ("ivfflat", "hnsw"):
            raise ValueError(f"Invalid index_type: {index_type}. Must be 'ivfflat' or 'hnsw'")

        if not isinstance(lists, int) or lists < 1 or lists > 10000:
            raise ValueError(f"Invalid lists value: {lists}. Must be integer between 1 and 10000")

        existing_type = await self.get_vector_index_type(index_name=index_name)
        if existing_type == index_type:
            logger.info(
                "vector_index_already_present",
                index_name=index_name,
                index_type=index_type,
            )
            return "existing"

        if existing_type is not None and existing_type != index_type:
            logger.warning(
                "vector_index_type_mismatch",
                index_name=index_name,
                expected=index_type,
                existing=existing_type,
            )
            return "mismatched"

        if index_type == "ivfflat":
            await self.session.execute(
                text(
                    f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON embeddings USING ivfflat (vector vector_cosine_ops)
                    WITH (lists = :lists)
                    """
                ),
                {"lists": lists},
            )
            await self.session.execute(text("ANALYZE embeddings"))
        else:
            await self.session.execute(
                text(
                    f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON embeddings USING hnsw (vector vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                    """
                )
            )

        await self.session.flush()
        logger.info(
            "vector_index_ensured",
            index_name=index_name,
            index_type=index_type,
        )
        return "created"
    
    async def create_vector_index(
        self,
        index_type: str = "ivfflat",
        lists: int = 100,
    ):
        """
        Create vector index for faster similarity search.
        
        Args:
            index_type: 'ivfflat' or 'hnsw'
            lists: Number of lists for IVFFlat (ignored for HNSW)
        
        Raises:
            ValueError: If index_type is invalid or lists is out of range
        """
        if index_type not in ("ivfflat", "hnsw"):
            raise ValueError(f"Invalid index_type: {index_type}. Must be 'ivfflat' or 'hnsw'")

        if not isinstance(lists, int) or lists < 1 or lists > 10000:
            raise ValueError(f"Invalid lists value: {lists}. Must be integer between 1 and 10000")

        try:
            index_name = self.DEFAULT_VECTOR_INDEX_NAME

            # Drop existing index if it exists
            await self.session.execute(text(
                f"DROP INDEX IF EXISTS {index_name}"
            ))
            
            if index_type == "ivfflat":
                # FIXED: Use parameterized query to prevent SQL injection
                await self.session.execute(
                    text("""
                        CREATE INDEX %s 
                        ON embeddings USING ivfflat (vector vector_cosine_ops) 
                        WITH (lists = :lists)
                    """ % index_name),
                    {"lists": lists}
                )
                # FIXED: Analyze table so Postgres can use the IVFFlat index
                # Without ANALYZE, Postgres won't consider the index and will do table scans
                await self.session.execute(text("ANALYZE embeddings"))
                logger.info("ivfflat_index_analyzed")
                
            elif index_type == "hnsw":
                await self.session.execute(text(
                    f"""
                    CREATE INDEX {index_name} 
                    ON embeddings USING hnsw (vector vector_cosine_ops) 
                    WITH (m = 16, ef_construction = 64)
                    """
                ))
                # HNSW doesn't require ANALYZE to be used by the planner
            
            await self.session.flush()
            logger.info(
                "vector_index_created",
                index_type=index_type,
                lists=lists if index_type == "ivfflat" else None,
            )
            
        except Exception as e:
            error_msg = f"Failed to create vector index: {str(e)}"
            logger.error("vector_index_creation_failed", error=error_msg)
            raise
