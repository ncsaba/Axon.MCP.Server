from typing import List, Dict, Optional, Any
from dataclasses import dataclass
import asyncio
import time

# Optional import for OpenAI
try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    AsyncOpenAI = None
from src.config.settings import get_settings
from src.config.embedding_contract import FIXED_EMBEDDING_DIMENSION
from src.utils.logging_config import get_logger
from src.utils.metrics import embedding_generation_duration, embeddings_generated_total

logger = get_logger(__name__)

_EMBEDDING_CONTEXT_ERROR_MARKERS = (
    "context length",
    "input length exceeds",
    "maximum context length",
    "too many tokens",
    "prompt is too long",
)
_EMBEDDING_MIN_RETRY_CHARS = 64


@dataclass
class EmbeddingResult:
    """Result of embedding generation."""
    chunk_id: int
    vector: List[float]
    model_name: str
    model_version: str
    dimension: int


class EmbeddingGenerator:
    """Generates vector embeddings using OpenAI or local models."""
    
    def __init__(self):
        """Initialize embedding generator."""
        self.provider = get_settings().embedding_provider
        
        if self.provider == "openai":
            if not OPENAI_AVAILABLE:
                raise ImportError(
                    "Failed to initialize embedding generator: OpenAI package not installed. Install with: pip install openai"
                )
            if not get_settings().openai_api_key:
                raise ValueError("Failed to initialize embedding generator: OpenAI API key required when provider is 'openai'")
            self.client = AsyncOpenAI(api_key=get_settings().openai_api_key)
            self.model_name = get_settings().openai_embedding_model
            self.dimension = get_settings().openai_embedding_dimension
            self.model_version = "1.0"
        elif self.provider == "ollama":
            if not OPENAI_AVAILABLE:
                raise ImportError(
                    "Failed to initialize embedding generator: OpenAI package not installed. Install with: pip install openai"
                )
            self.client = AsyncOpenAI(
                api_key="ollama",
                base_url=get_settings().ollama_base_url,
            )
            self.model_name = get_settings().ollama_embedding_model
            self.dimension = FIXED_EMBEDDING_DIMENSION
            self.model_version = "1.0"
        else:
            # Local model using sentence-transformers
            from sentence_transformers import SentenceTransformer
            logger.info(
                "loading_local_embedding_model",
                model=get_settings().local_embedding_model
            )
            self.model = SentenceTransformer(get_settings().local_embedding_model)
            self.model_name = get_settings().local_embedding_model
            self.dimension = self.model.get_sentence_embedding_dimension()
            self.model_version = "1.0"

        if self.dimension != FIXED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"Embedding model dimension {self.dimension} does not match fixed contract "
                f"{FIXED_EMBEDDING_DIMENSION}"
            )
        
        logger.info(
            "embedding_generator_initialized",
            provider=self.provider,
            model=self.model_name,
            dimension=self.dimension
        )
    
    async def generate_embeddings(
        self,
        chunks: List[Dict[str, Any]],
        batch_size: Optional[int] = None
    ) -> List[EmbeddingResult]:
        """
        Generate embeddings for chunks.
        
        Args:
            chunks: List of dicts with 'id' and 'content' keys
            batch_size: Batch size for processing
            
        Returns:
            List of EmbeddingResults
        """
        batch_size = batch_size or get_settings().embedding_batch_size
        results = []
        
        # Process in batches
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            
            try:
                batch_results = await self._generate_batch_embeddings(batch)
                
                results.extend(batch_results)
                
                embeddings_generated_total.labels(
                    model=self.model_name,
                    status="success"
                ).inc(len(batch_results))
                skipped_count = len(batch) - len(batch_results)
                if skipped_count:
                    embeddings_generated_total.labels(
                        model=self.model_name,
                        status="error"
                    ).inc(skipped_count)
                    logger.warning(
                        "embedding_batch_partial_completion",
                        batch_num=i // batch_size + 1,
                        requested=len(batch),
                        generated=len(batch_results),
                        skipped=skipped_count,
                    )
                
                logger.info(
                    "embedding_batch_completed",
                    batch_num=i // batch_size + 1,
                    batch_size=len(batch_results),
                    total_processed=len(results)
                )
                
            except Exception as e:
                error_msg = f"Failed to generate embeddings: {str(e)}"
                logger.error(
                    "embedding_batch_failed",
                    batch_num=i // batch_size + 1,
                    error=error_msg
                )
                embeddings_generated_total.labels(
                    model=self.model_name,
                    status="error"
                ).inc(len(batch))
                # Continue with next batch
                continue
        
        return results

    async def _generate_batch_embeddings(
        self,
        chunks: List[Dict[str, Any]]
    ) -> List[EmbeddingResult]:
        if self.provider in ("openai", "ollama"):
            return await self._generate_openai_embeddings_with_retry(chunks)
        return await self._generate_local_embeddings(chunks)

    async def _generate_openai_embeddings_with_retry(
        self,
        chunks: List[Dict[str, Any]]
    ) -> List[EmbeddingResult]:
        try:
            return await self._generate_openai_embeddings(chunks)
        except Exception as exc:
            if not self._is_context_limit_error(exc):
                raise

            if len(chunks) > 1:
                midpoint = max(1, len(chunks) // 2)
                logger.warning(
                    "embedding_batch_context_limit_retry",
                    chunk_count=len(chunks),
                    split_left=midpoint,
                    split_right=len(chunks) - midpoint,
                )
                left_results = await self._generate_openai_embeddings_with_retry(chunks[:midpoint])
                right_results = await self._generate_openai_embeddings_with_retry(chunks[midpoint:])
                return left_results + right_results

            return await self._generate_single_openai_embedding_with_truncation(chunks[0], exc)

    async def _generate_single_openai_embedding_with_truncation(
        self,
        chunk: Dict[str, Any],
        original_error: Exception,
    ) -> List[EmbeddingResult]:
        content = str(chunk.get("content") or "")
        current_limit = self._next_retry_char_limit(len(content))
        last_error: Exception = original_error

        while current_limit >= _EMBEDDING_MIN_RETRY_CHARS:
            truncated_content = self._truncate_embedding_content(content, current_limit)
            if truncated_content == content:
                break

            logger.warning(
                "embedding_input_truncated_for_retry",
                chunk_id=chunk.get("id"),
                original_length=len(content),
                truncated_length=len(truncated_content),
            )

            try:
                return await self._generate_openai_embeddings(
                    [{"id": chunk["id"], "content": truncated_content}]
                )
            except Exception as exc:
                if not self._is_context_limit_error(exc):
                    raise
                last_error = exc
                current_limit = self._next_retry_char_limit(len(truncated_content))

        logger.error(
            "embedding_chunk_skipped_context_limit",
            chunk_id=chunk.get("id"),
            original_length=len(content),
            error=str(last_error),
        )
        return []

    def _is_context_limit_error(self, error: Exception) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in _EMBEDDING_CONTEXT_ERROR_MARKERS)

    def _next_retry_char_limit(self, current_length: int) -> int:
        if current_length <= _EMBEDDING_MIN_RETRY_CHARS:
            return current_length // 2
        return max(_EMBEDDING_MIN_RETRY_CHARS, current_length // 2)

    def _truncate_embedding_content(self, content: str, max_chars: int) -> str:
        if len(content) <= max_chars:
            return content

        truncated = content[:max_chars]
        newline_boundary = truncated.rfind("\n")
        if newline_boundary >= max_chars // 2:
            truncated = truncated[:newline_boundary]
        return truncated.rstrip()
    
    async def _generate_openai_embeddings(
        self,
        chunks: List[Dict[str, Any]]
    ) -> List[EmbeddingResult]:
        """Generate embeddings using OpenAI API."""
        start_time = time.time()
        
        texts = [chunk['content'] for chunk in chunks]
        
        try:
            response = await self.client.embeddings.create(
                model=self.model_name,
                input=texts,
                encoding_format="float"
            )
            
            results = []
            for i, chunk in enumerate(chunks):
                results.append(EmbeddingResult(
                    chunk_id=chunk['id'],
                    vector=response.data[i].embedding,
                    model_name=self.model_name,
                    model_version=self.model_version,  # Could extract from response
                    dimension=self.dimension
                ))
            
            duration = time.time() - start_time
            embedding_generation_duration.labels(model=self.model_name).observe(duration)
            
            return results
            
        except Exception as e:
            error_msg = f"Failed to generate OpenAI embeddings: {str(e)}"
            logger.error(
                "openai_embedding_failed",
                error=error_msg,
                chunk_count=len(chunks)
            )
            raise
    
    async def _generate_local_embeddings(
        self,
        chunks: List[Dict[str, Any]]
    ) -> List[EmbeddingResult]:
        """Generate embeddings using local model."""
        start_time = time.time()
        
        texts = [chunk['content'] for chunk in chunks]
        
        try:
            # Run in thread pool since sentence-transformers is synchronous
            loop = asyncio.get_event_loop()
            embeddings = await loop.run_in_executor(
                None,
                self.model.encode,
                texts
            )
            
            results = []
            for i, chunk in enumerate(chunks):
                results.append(EmbeddingResult(
                    chunk_id=chunk['id'],
                    vector=embeddings[i].tolist(),
                    model_name=self.model_name,
                    model_version=self.model_version,
                    dimension=self.dimension
                ))
            
            duration = time.time() - start_time
            embedding_generation_duration.labels(model=self.model_name).observe(duration)
            
            logger.info(
                "local_embeddings_generated",
                chunk_count=len(chunks),
                duration_seconds=round(duration, 2)
            )
            
            return results
            
        except Exception as e:
            error_msg = f"Failed to generate local embeddings: {str(e)}"
            logger.error(
                "local_embedding_failed",
                error=error_msg,
                chunk_count=len(chunks)
            )
            raise
    
    async def generate_single_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        chunks = [{'id': 0, 'content': text}]
        results = await self.generate_embeddings(chunks, batch_size=1)
        
        if results:
            return results[0].vector
        return []
