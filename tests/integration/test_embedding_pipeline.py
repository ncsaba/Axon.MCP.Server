import pytest
from unittest.mock import patch, MagicMock
import numpy as np
from src.embeddings.generator import EmbeddingGenerator
from src.embeddings.cache import EmbeddingCache
from src.embeddings.chunking import TextChunker
from src.config.embedding_contract import FIXED_EMBEDDING_DIMENSION
from src.config.settings import get_settings

settings = get_settings()


def _mock_local_embedding_model():
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = FIXED_EMBEDDING_DIMENSION

    def encode(texts):
        return np.array([[0.1] * FIXED_EMBEDDING_DIMENSION for _ in texts])

    model.encode.side_effect = encode
    return model


@pytest.mark.integration
@pytest.mark.skipif(
    settings.embedding_provider == "openai" and not settings.openai_api_key,
    reason="OpenAI API key required"
)
@pytest.mark.asyncio
async def test_end_to_end_embedding_generation():
    """Test complete embedding generation pipeline."""
    with patch("src.embeddings.cache.redis") as mock_redis, patch(
        "src.embeddings.generator.get_settings"
    ) as mock_get_settings, patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_client = MagicMock()
        mock_redis.from_url.return_value = mock_client
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 100
        mock_st.return_value = _mock_local_embedding_model()
        
        # Mock storage
        storage = {}
        def mock_setex(name, time, value):
            storage[name] = value
            return True
        def mock_get(name):
            return storage.get(name)
            
        mock_client.setex.side_effect = mock_setex
        mock_client.get.side_effect = mock_get

        generator = EmbeddingGenerator()
        cache = EmbeddingCache()
    
    # Test text
    text = "This is a test sentence for embedding generation."
    chunks = [{'id': 1, 'content': text}]
    
    # Generate embeddings
    results = await generator.generate_embeddings(chunks)
    
    assert len(results) == 1
    assert len(results[0].vector) == generator.dimension
    
    # Cache result
    content_hash = EmbeddingCache.hash_content(text)
    cache.set(content_hash, generator.model_name, results[0].vector)
    
    # Retrieve from cache
    cached = cache.get(content_hash, generator.model_name)
    assert cached is not None
    assert cached == results[0].vector


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_embedding_generation():
    """Test local model embedding generation."""
    # Override to use local model
    with patch("src.embeddings.generator.get_settings") as mock_get_settings, patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 100
        mock_st.return_value = _mock_local_embedding_model()
        
        generator = EmbeddingGenerator()
        
        chunks = [
            {'id': 1, 'content': 'Function to calculate sum'},
            {'id': 2, 'content': 'Class for user authentication'},
            {'id': 3, 'content': 'Method to process data'}
        ]
        
        results = await generator.generate_embeddings(chunks)
        
        assert len(results) == 3
        assert all(len(r.vector) == generator.dimension for r in results)
        assert all(r.model_name == generator.model_name for r in results)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chunking_and_embedding():
    """Test text chunking followed by embedding generation."""
    chunker = TextChunker(max_tokens=50, overlap=10)
    with patch("src.embeddings.generator.get_settings") as mock_get_settings, patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 100
        mock_st.return_value = _mock_local_embedding_model()
        generator = EmbeddingGenerator()
    
        # Long text that needs chunking
        long_text = " ".join([f"This is sentence number {i}." for i in range(100)])
    
        # Chunk the text
        text_chunks = chunker.chunk_text(long_text)
        assert len(text_chunks) > 1  # Should be split into multiple chunks
    
        # Convert to format expected by generator
        chunks = [{'id': i, 'content': chunk} for i, chunk in enumerate(text_chunks)]
    
        # Generate embeddings
        results = await generator.generate_embeddings(chunks)
    
        assert len(results) == len(text_chunks)
        assert all(len(r.vector) == generator.dimension for r in results)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_hit_rate():
    """Test that caching reduces redundant embedding generation."""
    with patch("src.embeddings.cache.redis") as mock_redis, patch(
        "src.embeddings.generator.get_settings"
    ) as mock_get_settings, patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_client = MagicMock()
        mock_redis.from_url.return_value = mock_client
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 100
        mock_st.return_value = _mock_local_embedding_model()
        
        # Mock storage
        storage = {}
        def mock_setex(name, time, value):
            storage[name] = value
            return True
        def mock_get(name):
            return storage.get(name)
            
        mock_client.setex.side_effect = mock_setex
        mock_client.get.side_effect = mock_get

        generator = EmbeddingGenerator()
        cache = EmbeddingCache()
    
    text = "This is a test for cache hit rate."
    content_hash = EmbeddingCache.hash_content(text)
    
    # First call - should miss cache
    cached = cache.get(content_hash, generator.model_name)
    assert cached is None
    
    # Generate embedding
    chunks = [{'id': 1, 'content': text}]
    results = await generator.generate_embeddings(chunks)
    
    # Store in cache
    cache.set(content_hash, generator.model_name, results[0].vector)
    
    # Second call - should hit cache
    cached = cache.get(content_hash, generator.model_name)
    assert cached is not None
    assert len(cached) == generator.dimension


@pytest.mark.integration
@pytest.mark.asyncio
async def test_batch_processing_performance():
    """Test performance of batch processing."""
    import time
    
    with patch("src.embeddings.generator.get_settings") as mock_get_settings, patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 100
        mock_st.return_value = _mock_local_embedding_model()
        generator = EmbeddingGenerator()
    
        # Create a large batch
        chunks = [
            {'id': i, 'content': f'This is test content number {i}'}
            for i in range(100)
        ]
    
        start_time = time.time()
        results = await generator.generate_embeddings(chunks)
        duration = time.time() - start_time
    
        assert len(results) == 100
        assert duration < 60  # Should complete within 60 seconds for mocked local model
    
        print(f"Generated {len(results)} embeddings in {duration:.2f} seconds")
        print(f"Average: {duration/len(results):.3f} seconds per embedding")


@pytest.mark.integration
def test_text_chunking():
    """Test text chunking functionality."""
    chunker = TextChunker(max_tokens=10, overlap=2)
    
    # Short text - should not be chunked
    short_text = "This is a short text."
    chunks = chunker.chunk_text(short_text)
    assert len(chunks) == 1
    
    # Long text - should be chunked
    long_text = " ".join([f"word{i}" for i in range(50)])
    chunks = chunker.chunk_text(long_text)
    assert len(chunks) > 1
    
    # Check overlap
    if len(chunks) > 1:
        # First chunk should end with some words that start the second chunk
        first_words = chunks[0].split()
        second_words = chunks[1].split()
        # There should be overlap
        assert any(word in second_words for word in first_words[-5:])


@pytest.mark.integration
def test_code_chunking():
    """Test code chunking functionality."""
    chunker = TextChunker()
    
    # Sample code
    code = """
def function_one():
    return 1

def function_two():
    return 2

class MyClass:
    def __init__(self):
        self.value = 0
    
    def method_one(self):
        return self.value
"""
    
    chunks = chunker.chunk_code(code, max_lines=5)
    assert len(chunks) > 1
    
    # Verify chunks contain code
    assert all(chunk.strip() for chunk in chunks)
