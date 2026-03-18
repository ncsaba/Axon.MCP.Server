import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.embeddings.generator import EmbeddingGenerator, EmbeddingResult, OPENAI_AVAILABLE
from src.config.embedding_contract import FIXED_EMBEDDING_DIMENSION


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(embedding=[0.1] * FIXED_EMBEDDING_DIMENSION)
    ]
    mock_client.embeddings.create.return_value = mock_response
    return mock_client


@pytest.mark.skipif(not OPENAI_AVAILABLE, reason="OpenAI not installed")
def test_generate_openai_embeddings_rejects_wrong_dimension(mock_openai_client):
    """Test OpenAI embedding generator rejects dimensions outside the fixed contract."""
    with patch('src.embeddings.generator.AsyncOpenAI', return_value=mock_openai_client):
        with patch('src.embeddings.generator.get_settings') as mock_get_settings:
            mock_settings = mock_get_settings.return_value
            with patch('src.embeddings.generator.OPENAI_AVAILABLE', True):
                mock_settings.embedding_provider = "openai"
                mock_settings.openai_api_key = "test-key"
                mock_settings.openai_embedding_model = "text-embedding-3-small"
                mock_settings.openai_embedding_dimension = 1536
                mock_settings.embedding_batch_size = 100

                with pytest.raises(ValueError, match="does not match fixed contract"):
                    EmbeddingGenerator()


@pytest.mark.skipif(not OPENAI_AVAILABLE, reason="OpenAI not installed")
@pytest.mark.asyncio
async def test_generate_ollama_embeddings(mock_openai_client):
    """Test Ollama embedding generation with the fixed 1024-dimensional contract."""
    with patch('src.embeddings.generator.AsyncOpenAI', return_value=mock_openai_client):
        with patch('src.embeddings.generator.get_settings') as mock_get_settings:
            mock_settings = mock_get_settings.return_value
            mock_settings.embedding_provider = "ollama"
            mock_settings.ollama_base_url = "http://localhost:11434/v1"
            mock_settings.ollama_embedding_model = "mxbai-embed-large"
            mock_settings.embedding_batch_size = 100

            generator = EmbeddingGenerator()

            chunks = [
                {'id': 1, 'content': 'Test content'}
            ]

            results = await generator.generate_embeddings(chunks)

            assert len(results) == 1
            assert results[0].chunk_id == 1
            assert len(results[0].vector) == FIXED_EMBEDDING_DIMENSION
            assert generator.model_name == "mxbai-embed-large"


@pytest.mark.skipif(not OPENAI_AVAILABLE, reason="OpenAI not installed")
@pytest.mark.asyncio
async def test_generate_ollama_embeddings_retries_context_limited_batch():
    """Batch context-limit failures should split into smaller requests instead of dropping the batch."""
    mock_client = AsyncMock()

    def _response_for(input_texts):
        response = MagicMock()
        response.data = [
            MagicMock(embedding=[0.1] * FIXED_EMBEDDING_DIMENSION)
            for _ in input_texts
        ]
        return response

    async def _create_embeddings(*, model, input, encoding_format):
        if len(input) > 1:
            raise Exception("the input length exceeds the context length")
        return _response_for(input)

    mock_client.embeddings.create.side_effect = _create_embeddings

    with patch('src.embeddings.generator.AsyncOpenAI', return_value=mock_client):
        with patch('src.embeddings.generator.get_settings') as mock_get_settings:
            mock_settings = mock_get_settings.return_value
            mock_settings.embedding_provider = "ollama"
            mock_settings.ollama_base_url = "http://localhost:11434/v1"
            mock_settings.ollama_embedding_model = "mxbai-embed-large"
            mock_settings.embedding_batch_size = 100

            generator = EmbeddingGenerator()
            results = await generator.generate_embeddings(
                [
                    {'id': 1, 'content': 'first chunk'},
                    {'id': 2, 'content': 'second chunk'},
                ]
            )

            assert [result.chunk_id for result in results] == [1, 2]
            assert mock_client.embeddings.create.await_count == 3


@pytest.mark.skipif(not OPENAI_AVAILABLE, reason="OpenAI not installed")
@pytest.mark.asyncio
async def test_generate_ollama_embeddings_truncates_single_chunk_after_context_limit():
    """A single over-limit chunk should be retried with shorter content before being skipped."""
    mock_client = AsyncMock()
    attempted_lengths = []
    content = "abcdefghijklmnopqrstuvwxyz" * 10

    async def _create_embeddings(*, model, input, encoding_format):
        attempted_lengths.append(len(input[0]))
        if len(input[0]) > 80:
            raise Exception("the input length exceeds the context length")
        response = MagicMock()
        response.data = [MagicMock(embedding=[0.1] * FIXED_EMBEDDING_DIMENSION)]
        return response

    mock_client.embeddings.create.side_effect = _create_embeddings

    with patch('src.embeddings.generator.AsyncOpenAI', return_value=mock_client):
        with patch('src.embeddings.generator.get_settings') as mock_get_settings:
            mock_settings = mock_get_settings.return_value
            mock_settings.embedding_provider = "ollama"
            mock_settings.ollama_base_url = "http://localhost:11434/v1"
            mock_settings.ollama_embedding_model = "mxbai-embed-large"
            mock_settings.embedding_batch_size = 100

            generator = EmbeddingGenerator()
            results = await generator.generate_embeddings(
                [{'id': 1, 'content': content}]
            )

            assert [result.chunk_id for result in results] == [1]
            assert attempted_lengths[0] == len(content)
            assert attempted_lengths[-1] <= 80


@pytest.mark.asyncio
async def test_generate_local_embeddings():
    """Test local embedding generation."""
    with patch('src.embeddings.generator.get_settings') as mock_get_settings:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 100
        
        with patch('sentence_transformers.SentenceTransformer') as mock_st:
            import numpy as np
            mock_model = MagicMock()
            mock_model.encode.return_value = np.array([[0.1] * FIXED_EMBEDDING_DIMENSION])
            mock_model.get_sentence_embedding_dimension.return_value = FIXED_EMBEDDING_DIMENSION
            mock_st.return_value = mock_model
            
            generator = EmbeddingGenerator()
            
            chunks = [
                {'id': 1, 'content': 'Test content'}
            ]
            
            results = await generator.generate_embeddings(chunks)
            
            assert len(results) == 1
            assert results[0].chunk_id == 1


@pytest.mark.asyncio
async def test_generate_single_embedding():
    """Test single embedding generation."""
    with patch('src.embeddings.generator.get_settings') as mock_get_settings:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 100
        
        with patch('sentence_transformers.SentenceTransformer') as mock_st:
            import numpy as np
            mock_model = MagicMock()
            mock_model.encode.return_value = np.array([[0.1] * FIXED_EMBEDDING_DIMENSION])
            mock_model.get_sentence_embedding_dimension.return_value = FIXED_EMBEDDING_DIMENSION
            mock_st.return_value = mock_model
            
            generator = EmbeddingGenerator()
            
            vector = await generator.generate_single_embedding("Test text")
            
            assert len(vector) == FIXED_EMBEDDING_DIMENSION


@pytest.mark.asyncio
async def test_batch_processing():
    """Test batch processing of multiple chunks."""
    with patch('src.embeddings.generator.get_settings') as mock_get_settings:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 2  # Small batch size for testing
        
        with patch('sentence_transformers.SentenceTransformer') as mock_st:
            import numpy as np
            mock_model = MagicMock()
            # Return different batches
            mock_model.encode.side_effect = [
                np.array([[0.1] * FIXED_EMBEDDING_DIMENSION, [0.2] * FIXED_EMBEDDING_DIMENSION]),
                np.array([[0.3] * FIXED_EMBEDDING_DIMENSION])
            ]
            mock_model.get_sentence_embedding_dimension.return_value = FIXED_EMBEDDING_DIMENSION
            mock_st.return_value = mock_model
            
            generator = EmbeddingGenerator()
            
            chunks = [
                {'id': 1, 'content': 'Test content 1'},
                {'id': 2, 'content': 'Test content 2'},
                {'id': 3, 'content': 'Test content 3'}
            ]
            
            results = await generator.generate_embeddings(chunks)
            
            assert len(results) == 3
            assert results[0].chunk_id == 1
            assert results[1].chunk_id == 2
            assert results[2].chunk_id == 3


@pytest.mark.asyncio
async def test_embedding_error_handling():
    """Test error handling during embedding generation."""
    with patch('src.embeddings.generator.get_settings') as mock_get_settings:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "custom-local-1024"
        mock_settings.embedding_batch_size = 2
        
        with patch('sentence_transformers.SentenceTransformer') as mock_st:
            import numpy as np
            mock_model = MagicMock()
            # First batch fails, second succeeds
            mock_model.encode.side_effect = [
                Exception("Encoding failed"),
                np.array([[0.3] * FIXED_EMBEDDING_DIMENSION])
            ]
            mock_model.get_sentence_embedding_dimension.return_value = FIXED_EMBEDDING_DIMENSION
            mock_st.return_value = mock_model
            
            generator = EmbeddingGenerator()
            
            chunks = [
                {'id': 1, 'content': 'Test content 1'},
                {'id': 2, 'content': 'Test content 2'},
                {'id': 3, 'content': 'Test content 3'}
            ]
            
            results = await generator.generate_embeddings(chunks)
            
            # Should only get results from successful batch
            assert len(results) == 1
            assert results[0].chunk_id == 3


def test_generate_local_embeddings_rejects_wrong_dimension():
    """Test local embedding generator rejects models outside the fixed contract."""
    with patch('src.embeddings.generator.get_settings') as mock_get_settings:
        mock_settings = mock_get_settings.return_value
        mock_settings.embedding_provider = "local"
        mock_settings.local_embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
        mock_settings.embedding_batch_size = 100

        with patch('sentence_transformers.SentenceTransformer') as mock_st:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value = mock_model

            with pytest.raises(ValueError, match="does not match fixed contract"):
                EmbeddingGenerator()
