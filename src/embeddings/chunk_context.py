"""Context builder for creating rich chunks."""

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Repository, Symbol, FileInstance as File, Relation
from src.config.enums import LanguageEnum, RelationTypeEnum
from src.parsers import ParserFactory
from src.parsers.base_parser import ParseResult
from src.repository_sources import get_repository_source_registry

_MAX_CONTEXT_IMPORTS = 20
_JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", re.MULTILINE)


@dataclass
class ChunkContext:
    """Rich context for creating symbol chunks."""
    
    # File-level context
    file_path: str
    namespace: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    
    # Symbol hierarchy
    parent_class: Optional[Dict[str, Any]] = None  # Parent class info if this is a method
    parent_namespace: Optional[str] = None
    
    # Relationships
    calls: List[str] = field(default_factory=list)  # Functions this symbol calls
    called_by: List[str] = field(default_factory=list)  # Functions that call this
    implements: List[str] = field(default_factory=list)  # Interfaces this implements
    inherits_from: List[str] = field(default_factory=list)  # Classes this inherits from
    
    # Additional metadata
    complexity: Optional[int] = None
    is_test: bool = False
    is_public: bool = True


class ChunkContextBuilder:
    """Builder for extracting context for symbol chunks."""
    
    def __init__(self, session: AsyncSession):
        """
        Initialize context builder.
        
        Args:
            session: Database session
        """
        self.session = session
        self._parse_result_cache: dict[int, Optional[ParseResult]] = {}
        self._repo_path_cache: dict[int, Optional[Path]] = {}
    
    async def build_context(
        self,
        symbol: Symbol,
        file: File,
        file_content: Optional[str] = None,
    ) -> ChunkContext:
        """
        Build rich context for a symbol.
        
        Args:
            symbol: Symbol to build context for
            file: File containing the symbol
            
        Returns:
            ChunkContext with extracted information
        """
        context = ChunkContext(file_path=file.path)
        
        # Extract namespace from symbol's fully qualified name
        if symbol.fully_qualified_name and '.' in symbol.fully_qualified_name:
            parts = symbol.fully_qualified_name.rsplit('.', 1)
            context.namespace = parts[0]
        
        # Get parent class info if this is a method
        if symbol.parent_name:
            parent = await self._get_parent_symbol(symbol)
            if parent:
                context.parent_class = {
                    'name': parent.name,
                    'kind': parent.kind.value,
                    'signature': parent.signature
                }
        
        # Get imports from file
        # Note: This could be enhanced by parsing file.content if available
        # For now, we'll extract from related symbols
        context.imports = await self._extract_imports(file, file_content=file_content)
        if not context.namespace:
            context.namespace = self._derive_namespace(file, file_content=file_content)
        
        # Get relationships
        await self._extract_relationships(symbol, context)
        
        # Determine if public (from access modifier)
        if symbol.access_modifier:
            context.is_public = symbol.access_modifier.value in ['public', 'protected']
        
        # Check if it's a test (heuristic based on name/path)
        context.is_test = self._is_test_symbol(symbol, file)
        
        # Get complexity if available
        if symbol.complexity_score:
            context.complexity = symbol.complexity_score
        
        return context
    
    async def _get_parent_symbol(self, symbol: Symbol) -> Optional[Symbol]:
        """Get the parent symbol (e.g., class for a method)."""
        if not symbol.parent_name:
            return None
        
        result = await self.session.execute(
            select(Symbol).where(
                Symbol.file_instance_id == symbol.file_instance_id,
                Symbol.fully_qualified_name == symbol.parent_name
            )
        )
        return result.scalars().first()
    
    async def _extract_imports(self, file: File, file_content: Optional[str] = None) -> List[str]:
        """Extract bounded imports from parser output when source text is available."""
        parse_result = await self._get_parse_result(file, file_content=file_content)
        if not parse_result or not parse_result.imports:
            return []
        return self._normalize_imports(parse_result.imports)

    async def _get_parse_result(
        self,
        file: File,
        file_content: Optional[str] = None,
    ) -> Optional[ParseResult]:
        if file.id in self._parse_result_cache:
            return self._parse_result_cache[file.id]

        source_text = file_content
        if source_text is None:
            file_path = await self._resolve_file_path(file)
            if file_path is None or not file_path.exists() or not file_path.is_file():
                self._parse_result_cache[file.id] = None
                return None
            source_text = await asyncio.to_thread(
                file_path.read_text,
                encoding="utf-8",
                errors="ignore",
            )

        try:
            parser = ParserFactory.get_parser_for_file(Path(file.path))
        except ValueError:
            self._parse_result_cache[file.id] = None
            return None

        if hasattr(parser, "parse_async"):
            parse_result = await parser.parse_async(source_text, str(file.path))
        else:
            parse_result = await asyncio.to_thread(parser.parse, source_text, str(file.path))

        self._parse_result_cache[file.id] = parse_result
        return parse_result

    async def _resolve_file_path(self, file: File) -> Optional[Path]:
        if file.repository_id in self._repo_path_cache:
            repo_root = self._repo_path_cache[file.repository_id]
            return None if repo_root is None else repo_root / file.path

        repository = await self.session.get(Repository, file.repository_id)
        if repository is None:
            self._repo_path_cache[file.repository_id] = None
            return None

        try:
            source = get_repository_source_registry().resolve(repository)
            repo_root = source.get_repository_path(repository)
        except Exception:
            repo_root = None

        self._repo_path_cache[file.repository_id] = repo_root
        return None if repo_root is None else repo_root / file.path

    def _normalize_imports(self, imports: List[str]) -> List[str]:
        normalized: List[str] = []
        seen: set[str] = set()

        for value in imports:
            item = str(value or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
            if len(normalized) >= _MAX_CONTEXT_IMPORTS:
                break

        return normalized

    def _derive_namespace(self, file: File, file_content: Optional[str] = None) -> Optional[str]:
        if file.language == LanguageEnum.JAVA and file_content:
            match = _JAVA_PACKAGE_RE.search(file_content)
            if match:
                return match.group(1)

        if file.language == LanguageEnum.PYTHON:
            module_path = Path(file.path)
            without_suffix = module_path.with_suffix("")
            parts = list(without_suffix.parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                return ".".join(parts)

        return None
    
    async def _extract_relationships(self, symbol: Symbol, context: ChunkContext):
        """Extract symbol relationships."""
        # Get outgoing relationships (what this symbol uses/calls/implements)
        result_from = await self.session.execute(
            select(Relation, Symbol)
            .join(Symbol, Relation.to_symbol_id == Symbol.id)
            .where(Relation.from_symbol_id == symbol.id)
        )
        
        for relation, target in result_from.all():
            if relation.relation_type == RelationTypeEnum.CALLS:
                context.calls.append(target.name)
            elif relation.relation_type == RelationTypeEnum.IMPLEMENTS:
                context.implements.append(target.name)
            elif relation.relation_type == RelationTypeEnum.INHERITS:
                context.inherits_from.append(target.name)
        
        # Get incoming relationships (what calls/uses this symbol)
        result_to = await self.session.execute(
            select(Relation, Symbol)
            .join(Symbol, Relation.from_symbol_id == Symbol.id)
            .where(Relation.to_symbol_id == symbol.id)
        )
        
        for relation, source in result_to.all():
            if relation.relation_type == RelationTypeEnum.CALLS:
                context.called_by.append(source.name)
    
    def _is_test_symbol(self, symbol: Symbol, file: File) -> bool:
        """Determine if symbol is a test (heuristic)."""
        name_lower = symbol.name.lower()
        file_lower = file.path.lower()
        
        # Check for test keywords in name
        test_keywords = ['test', 'spec', 'should', 'when', 'given']
        if any(keyword in name_lower for keyword in test_keywords):
            return True
        
        # Check for test path patterns
        test_paths = ['test/', 'tests/', '__tests__/', 'spec/', 'specs/']
        if any(path in file_lower for path in test_paths):
            return True
        
        return False
