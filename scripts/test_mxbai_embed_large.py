#!/usr/bin/env python3
"""
Smoke test for the Ollama `mxbai-embed-large` embedding pipeline.

This script verifies:
- generator initialization
- fixed 1024-dimensional contract
- single embedding generation
- batch embedding generation
- basic semantic similarity sanity
"""
import asyncio
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("DEBUG", "false")
os.environ["EMBEDDING_PROVIDER"] = "ollama"
os.environ["OLLAMA_EMBEDDING_MODEL"] = "mxbai-embed-large"

from src.config.embedding_contract import FIXED_EMBEDDING_DIMENSION
from src.embeddings.generator import EmbeddingGenerator


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = math.sqrt(sum(a * a for a in vec1))
    magnitude2 = math.sqrt(sum(b * b for b in vec2))
    return dot_product / (magnitude1 * magnitude2)


async def main() -> int:
    print("\n" + "=" * 72)
    print("MXBAI EMBED LARGE TEST")
    print("=" * 72)

    try:
        generator = EmbeddingGenerator()
    except Exception as exc:
        print(f"[ERROR] Failed to initialize generator: {exc}")
        print("Make sure Ollama is running and `mxbai-embed-large` is available.")
        print("Example: `ollama pull mxbai-embed-large`")
        return 1

    print(f"Provider: {generator.provider}")
    print(f"Model: {generator.model_name}")
    print(f"Dimension: {generator.dimension}")
    print(f"Ollama Base URL: {os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1')}")

    if generator.dimension != FIXED_EMBEDDING_DIMENSION:
        print(
            f"[ERROR] Generator dimension {generator.dimension} does not match fixed contract "
            f"{FIXED_EMBEDDING_DIMENSION}"
        )
        return 1

    query = "How do we authenticate a user with a password?"
    print(f"\nSingle embedding test:\n  Query: {query}")
    vector = await generator.generate_single_embedding(query)
    print(f"  Vector length: {len(vector)}")
    print(f"  First 5 values: {[round(v, 4) for v in vector[:5]]}")

    if len(vector) != FIXED_EMBEDDING_DIMENSION:
        print("[ERROR] Single embedding length mismatch")
        return 1

    chunks = [
        {"id": 1, "content": "Function to authenticate user with password"},
        {"id": 2, "content": "Method for login and session validation"},
        {"id": 3, "content": "Calculate the sum of array elements"},
    ]
    print(f"\nBatch embedding test:\n  Chunks: {len(chunks)}")
    results = await generator.generate_embeddings(chunks, batch_size=3)

    if len(results) != len(chunks):
        print(f"[ERROR] Expected {len(chunks)} embeddings, got {len(results)}")
        return 1

    for result in results:
        if len(result.vector) != FIXED_EMBEDDING_DIMENSION:
            print(f"[ERROR] Chunk {result.chunk_id} returned wrong dimension {len(result.vector)}")
            return 1

    sim_auth = cosine_similarity(results[0].vector, results[1].vector)
    sim_math = cosine_similarity(results[0].vector, results[2].vector)

    print("\nSimilarity sanity check:")
    print(f"  auth vs auth-like: {sim_auth:.4f}")
    print(f"  auth vs math-like: {sim_math:.4f}")

    print("\n[SUCCESS] mxbai-embed-large is working with the fixed 1024-dimensional contract.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(1)
