"""Fallback chunking for parser-empty and parser-weak files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class FallbackChunkPolicy:
    """Configuration for a parser-empty file fallback category."""

    category: str
    chunk_subtype: str
    content_type: str
    max_lines: int = 80
    max_chunks: int = 4


class FileFallbackChunker:
    """Create bounded file-level chunks for config/build/dependency artifacts."""

    _DEFAULT_POLICY = FallbackChunkPolicy(
        category="file",
        chunk_subtype="fallback",
        content_type="text",
    )

    def get_policy(self, file_path: str) -> Optional[FallbackChunkPolicy]:
        path = Path(file_path)
        suffix = path.suffix.lower()
        name = path.name.lower()
        path_lower = file_path.lower()

        if name in {"pom.xml", "package.json", "build.gradle", "build.gradle.kts", "requirements.txt"}:
            return FallbackChunkPolicy(
                category="dependency",
                chunk_subtype="dependency",
                content_type="dependency",
                max_lines=120,
            )

        if name in {"build.xml", "docker-compose.yml", "docker-compose.yaml", ".env"}:
            return FallbackChunkPolicy(
                category="build",
                chunk_subtype="build",
                content_type="configuration",
            )

        if "liquibase" in path_lower or suffix in {".sql", ".ddl"}:
            return FallbackChunkPolicy(
                category="database",
                chunk_subtype="database",
                content_type="configuration",
                max_lines=100,
            )

        if (
            name.startswith("application.")
            or name.startswith("appsettings")
            or suffix in {".yml", ".yaml", ".json", ".xml", ".properties", ".conf", ".ini", ".toml"}
        ):
            return FallbackChunkPolicy(
                category="configuration",
                chunk_subtype="config",
                content_type="configuration",
            )

        return self._DEFAULT_POLICY

    def create_chunks_for_file(
        self,
        file_path: str,
        file_content: str,
    ) -> list[dict]:
        """Create bounded file-level fallback chunks when no code symbols exist."""
        if not file_content.strip():
            return []

        policy = self.get_policy(file_path)
        if policy is None:
            return []

        lines = file_content.splitlines()
        if not lines:
            return []

        chunks: list[dict] = []
        for idx, start in enumerate(range(0, len(lines), policy.max_lines), start=1):
            if idx > policy.max_chunks:
                break

            window = lines[start:start + policy.max_lines]
            if not any(line.strip() for line in window):
                continue

            chunk_body = "\n".join(window).strip()
            start_line = start + 1
            end_line = start + len(window)

            header = [
                f"File: {file_path}",
                f"Category: {policy.category}",
                "",
                chunk_body,
            ]

            chunks.append(
                {
                    "content": "\n".join(header),
                    "content_type": policy.content_type,
                    "chunk_subtype": policy.chunk_subtype,
                    "context_metadata": {
                        "fallback_policy": "file_level_parser_empty_v1",
                        "fallback_category": policy.category,
                        "file_path": file_path,
                        "chunk_index": idx,
                    },
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )

        return chunks
