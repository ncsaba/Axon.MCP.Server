"""Import resolution for building accurate import relationships."""

from typing import Optional, List, Dict, Any, Tuple
import asyncio
import re
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Symbol, FileInstance as File, Relation
from src.database.query_helpers import active_file_filter
from src.config.enums import SymbolKindEnum, RelationTypeEnum, LanguageEnum
from src.extractors.path_resolver import PathResolver
from src.extractors.strategy_interfaces import ImportExtractionStrategy, language_strategy
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class ImportResolver:
    """Resolves import statements to actual symbols."""
    
    def __init__(self, session: AsyncSession, repository_root: Path):
        """
        Initialize import resolver.
        
        Args:
            session: Database session
            repository_root: Root directory of repository
        """
        self.session = session
        self.path_resolver = PathResolver(repository_root)
    
    async def resolve_import(
        self,
        import_path: str,
        importing_file: File,
        importing_file_path: Path
    ) -> Optional[int]:
        """
        Resolve an import statement to a file ID.
        
        Args:
            import_path: Import string
            importing_file: File record making the import
            importing_file_path: Full path to importing file
            
        Returns:
            File ID of imported file, or None if not found
        """
        # Resolve import path to actual file path
        resolved_path = self.path_resolver.resolve_import_path(
            import_path,
            importing_file_path,
            importing_file.language.value
        )
        
        if not resolved_path:
            return None
        
        # Find file in database
        result = await self.session.execute(
            select(File).where(
                File.repository_id == importing_file.repository_id,
                File.path == str(resolved_path),
                active_file_filter(),
            )
        )
        
        target_file = result.scalar_one_or_none()
        return target_file.id if target_file else None
    
    async def resolve_symbol_import(
        self,
        import_path: str,
        symbol_name: str,
        importing_file: File,
        importing_file_path: Path
    ) -> Optional[int]:
        """
        Resolve a named import to a specific symbol.
        
        For imports like:
        - import { User } from './models/User'
        - using MyNamespace.MyClass
        
        Args:
            import_path: Import path/namespace
            symbol_name: Name of imported symbol
            importing_file: File making the import
            importing_file_path: Full path to importing file
            
        Returns:
            Symbol ID of imported symbol, or None if not found
        """
        # First resolve the file
        file_id = await self.resolve_import(import_path, importing_file, importing_file_path)
        
        if not file_id:
            return None
        
        # Find exported symbol with matching name in that file
        result = await self.session.execute(
            select(Symbol).where(
                Symbol.file_instance_id == file_id,
                Symbol.name == symbol_name
            ).limit(1)
        )
        
        symbol = result.scalar_one_or_none()
        return symbol.id if symbol else None


class JSImportExtractionStrategy:
    """JS/TS import extraction + resolution strategy."""

    def __init__(self, resolver: ImportResolver):
        self.resolver = resolver

    async def extract_imports(
        self,
        code: str,
        file: File,
        file_path: Path
    ) -> List[Dict[str, Any]]:
        imports = []

        named_imports = re.finditer(
            r'import\s+\{([^}]+)\}\s+from\s+["\']([^"\']+)["\']',
            code
        )

        for match in named_imports:
            symbols_str = match.group(1)
            import_path = match.group(2)
            symbol_names = [s.strip() for s in symbols_str.split(',')]

            target_file_id = await self.resolver.resolve_import(import_path, file, file_path)
            if not target_file_id:
                continue

            symbol_ids = []
            for symbol_name in symbol_names:
                symbol_id = await self.resolver.resolve_symbol_import(
                    import_path,
                    symbol_name,
                    file,
                    file_path
                )
                if symbol_id:
                    symbol_ids.append(symbol_id)

            imports.append({
                'import_path': import_path,
                'file_id': target_file_id,
                'symbols': symbol_ids
            })

        default_imports = re.finditer(
            r'import\s+(\w+)\s+from\s+["\']([^"\']+)["\']',
            code
        )

        for match in default_imports:
            symbol_name = match.group(1)
            import_path = match.group(2)
            target_file_id = await self.resolver.resolve_import(import_path, file, file_path)
            if not target_file_id:
                continue

            symbol_id = await self.resolver.resolve_symbol_import(
                import_path,
                symbol_name,
                file,
                file_path
            )
            if symbol_id:
                imports.append({
                    'import_path': import_path,
                    'file_id': target_file_id,
                    'symbols': [symbol_id]
                })

        return imports


class JavaImportExtractionStrategy:
    """Java import extraction + resolution strategy."""

    def __init__(self, resolver: ImportResolver):
        self.resolver = resolver

    async def extract_imports(
        self,
        code: str,
        file: File,
        file_path: Path
    ) -> List[Dict[str, Any]]:
        from src.parsers import ParserFactory

        imports: List[Dict[str, Any]] = []
        parser = ParserFactory.get_parser(file.language)
        parse_result = parser.parse(code, file.path)
        wildcard_map = self._extract_java_wildcard_imports(code)

        for import_path in parse_result.imports:
            if not import_path:
                continue

            wildcard_info = wildcard_map.get(import_path)
            if wildcard_info and wildcard_info["is_static"]:
                target_file_id, symbol_ids = await self._resolve_java_static_wildcard_import(import_path, file, file_path)
                imports.append(
                    {
                        "import_path": import_path,
                        "file_id": target_file_id,
                        "symbols": symbol_ids,
                    }
                )
                continue

            if wildcard_info and not wildcard_info["is_static"]:
                symbol_ids = await self._resolve_java_package_wildcard_import(import_path, file.repository_id)
                imports.append(
                    {
                        "import_path": import_path,
                        "file_id": None,
                        "symbols": symbol_ids,
                    }
                )
                continue

            target_file_id = await self.resolver.resolve_import(import_path, file, file_path)
            if not target_file_id:
                continue

            imported_symbol_name = import_path.split('.')[-1]
            symbol_ids: List[int] = []
            if imported_symbol_name and imported_symbol_name != "*":
                symbol_id = await self.resolver.resolve_symbol_import(
                    import_path,
                    imported_symbol_name,
                    file,
                    file_path
                )
                if symbol_id:
                    symbol_ids.append(symbol_id)

            imports.append({
                'import_path': import_path,
                'file_id': target_file_id,
                'symbols': symbol_ids
            })

        return imports

    def _extract_java_wildcard_imports(self, code: str) -> Dict[str, Dict[str, bool]]:
        """Return import_path -> wildcard metadata for Java imports that end with .*."""
        wildcard_map: Dict[str, Dict[str, bool]] = {}
        for raw_line in code.splitlines():
            line = raw_line.strip()
            match = re.match(r"import\s+(static\s+)?([A-Za-z_][\w\.]*)\.\*\s*;", line)
            if not match:
                continue
            import_path = match.group(2)
            wildcard_map[import_path] = {"is_static": bool(match.group(1))}
        return wildcard_map

    async def _resolve_java_package_wildcard_import(
        self,
        package_path: str,
        repository_id: int,
    ) -> List[int]:
        """Resolve package wildcard imports (e.g. com.example.services.*) to top-level type symbols."""
        normalized = package_path.replace(".", "/").strip("/")
        if not normalized:
            return []

        java_prefixes = [
            f"src/main/java/{normalized}/",
            f"src/test/java/{normalized}/",
            f"{normalized}/",
        ]

        file_result = await self.resolver.session.execute(
            select(File).where(
                File.repository_id == repository_id,
                File.language == LanguageEnum.JAVA,
                active_file_filter(),
            )
        )
        java_files = file_result.scalars().all()

        package_file_ids: List[int] = []
        for db_file in java_files:
            for prefix in java_prefixes:
                if db_file.path.startswith(prefix):
                    remainder = db_file.path[len(prefix):]
                    # Java wildcard imports only cover direct package members, not subpackages.
                    if "/" in remainder:
                        continue
                    if db_file.path.endswith(".java"):
                        package_file_ids.append(db_file.id)
                    break

        if not package_file_ids:
            return []

        symbol_result = await self.resolver.session.execute(
            select(Symbol.id).where(
                Symbol.file_instance_id.in_(package_file_ids),
                Symbol.kind.in_([SymbolKindEnum.CLASS, SymbolKindEnum.INTERFACE, SymbolKindEnum.ENUM]),
            )
        )
        return [row[0] for row in symbol_result.all()]

    async def _resolve_java_static_wildcard_import(
        self,
        import_path: str,
        file: File,
        file_path: Path,
    ) -> Tuple[Optional[int], List[int]]:
        """Resolve static wildcard imports (e.g. import static a.b.Constants.*)."""
        target_file_id = await self.resolver.resolve_import(import_path, file, file_path)
        if not target_file_id:
            return None, []

        class_fqn = import_path
        symbol_result = await self.resolver.session.execute(
            select(Symbol.id).where(
                Symbol.file_instance_id == target_file_id,
                Symbol.parent_name == class_fqn,
                Symbol.kind.in_(
                    [
                        SymbolKindEnum.METHOD,
                        SymbolKindEnum.FUNCTION,
                        SymbolKindEnum.VARIABLE,
                        SymbolKindEnum.CONSTANT,
                        SymbolKindEnum.PROPERTY,
                    ]
                ),
            )
        )
        symbol_ids = [row[0] for row in symbol_result.all()]
        return target_file_id, symbol_ids


class ImportRelationshipBuilder:
    """Builds IMPORTS relationships between files."""
    
    def __init__(self, session: AsyncSession, repository_root: Path):
        """
        Initialize import relationship builder.
        
        Args:
            session: Database session
            repository_root: Root directory of repository
        """
        self.session = session
        self.resolver = ImportResolver(session, repository_root)
        self.import_strategies: Dict[LanguageEnum, ImportExtractionStrategy] = {
            LanguageEnum.JAVASCRIPT: JSImportExtractionStrategy(self.resolver),
            LanguageEnum.TYPESCRIPT: JSImportExtractionStrategy(self.resolver),
            LanguageEnum.JAVA: JavaImportExtractionStrategy(self.resolver),
        }
    
    async def build_import_relationships(
        self,
        repository_id: int
    ):
        """
        Build import relationships for entire repository.
        
        Args:
            repository_id: Repository ID
        """
        logger.info("building_import_relationships", repository_id=repository_id)
        
        # Get all files in repository
        result = await self.session.execute(
            select(File).where(File.repository_id == repository_id, active_file_filter())
        )
        files = result.scalars().all()
        
        relationships_created = 0
        
        for file in files:
            try:
                # Get file content to parse imports
                file_path = self.resolver.path_resolver.repository_root / file.path
                
                if not file_path.exists():
                    continue
                
                # Read file content
                code = await asyncio.to_thread(file_path.read_text, encoding='utf-8', errors='ignore')
                
                # Parse imports based on language
                imports = await self._extract_imports_from_code(
                    code,
                    file,
                    file_path
                )
                
                # Get all symbols in the importing file to create import relationships
                source_symbols_result = await self.session.execute(
                    select(Symbol).where(Symbol.file_instance_id == file.id).limit(1)
                )
                source_symbol = source_symbols_result.scalar_one_or_none()
                
                # Create import relationships
                for import_info in imports:
                    target_file_id = import_info.get('file_id')
                    imported_symbols = import_info.get('symbols', [])

                    # Some Java wildcard imports resolve to symbols without a single target file.
                    if source_symbol and imported_symbols:
                        # Create import relationships from file's first symbol to imported symbols
                        # This represents that the file imports these symbols
                        for symbol_id in imported_symbols:
                            if symbol_id:  # Only if we found the imported symbol
                                relation = Relation(
                                    from_symbol_id=source_symbol.id,
                                    to_symbol_id=symbol_id,
                                    relation_type=RelationTypeEnum.IMPORTS,
                                    relation_metadata={
                                        'import_path': import_info.get('import_path'),
                                        'file_id': file.id
                                    }
                                )
                                self.session.add(relation)
                                relationships_created += 1
                
                # Commit in batches
                if relationships_created % 100 == 0:
                    await self.session.commit()
                
            except Exception as e:
                logger.error(
                    "import_resolution_failed",
                    file_id=file.id,
                    file_path=file.path,
                    error=str(e)
                )
                continue
        
        # Final commit
        await self.session.commit()
        
        logger.info(
            "import_relationships_built",
            repository_id=repository_id,
            relationships_created=relationships_created
        )
        
        return relationships_created
    
    async def _extract_imports_from_code(
        self,
        code: str,
        file: File,
        file_path: Path
    ) -> List[Dict[str, Any]]:
        """Extract and resolve imports from code."""
        strategy = language_strategy(self.import_strategies, file.language)
        if not strategy:
            return []
        return await strategy.extract_imports(code, file, file_path)

    async def _extract_java_imports(
        self,
        code: str,
        file: File,
        file_path: Path
    ) -> List[Dict[str, Any]]:
        """Extract Java imports using the Java parser import extraction."""
        from src.parsers import ParserFactory

        imports: List[Dict[str, Any]] = []
        parser = ParserFactory.get_parser(file.language)

        # Parse once and reuse extracted imports.
        parse_result = parser.parse(code, file.path)
        for import_path in parse_result.imports:
            if not import_path:
                continue

            target_file_id = await self.resolver.resolve_import(
                import_path,
                file,
                file_path
            )

            if not target_file_id:
                continue

            # For standard imports, last segment is typically the class symbol.
            # Example: com.example.UserService -> UserService
            imported_symbol_name = import_path.split('.')[-1]
            symbol_ids: List[int] = []

            if imported_symbol_name and imported_symbol_name != "*":
                symbol_id = await self.resolver.resolve_symbol_import(
                    import_path,
                    imported_symbol_name,
                    file,
                    file_path
                )
                if symbol_id:
                    symbol_ids.append(symbol_id)

            imports.append({
                'import_path': import_path,
                'file_id': target_file_id,
                'symbols': symbol_ids
            })

        return imports
    
    async def _extract_js_imports(
        self,
        code: str,
        file: File,
        file_path: Path
    ) -> List[Dict[str, Any]]:
        """Extract JavaScript/TypeScript imports."""
        import re
        imports = []
        
        # Match: import { Symbol1, Symbol2 } from 'path'
        named_imports = re.finditer(
            r'import\s+\{([^}]+)\}\s+from\s+["\']([^"\']+)["\']',
            code
        )
        
        for match in named_imports:
            symbols_str = match.group(1)
            import_path = match.group(2)
            
            # Parse symbol names
            symbol_names = [s.strip() for s in symbols_str.split(',')]
            
            # Resolve file
            target_file_id = await self.resolver.resolve_import(
                import_path,
                file,
                file_path
            )
            
            if target_file_id:
                # Resolve each symbol
                symbol_ids = []
                for symbol_name in symbol_names:
                    symbol_id = await self.resolver.resolve_symbol_import(
                        import_path,
                        symbol_name,
                        file,
                        file_path
                    )
                    if symbol_id:
                        symbol_ids.append(symbol_id)
                
                imports.append({
                    'import_path': import_path,
                    'file_id': target_file_id,
                    'symbols': symbol_ids
                })
        
        # Match: import DefaultExport from 'path'
        default_imports = re.finditer(
            r'import\s+(\w+)\s+from\s+["\']([^"\']+)["\']',
            code
        )
        
        for match in default_imports:
            symbol_name = match.group(1)
            import_path = match.group(2)
            
            target_file_id = await self.resolver.resolve_import(
                import_path,
                file,
                file_path
            )
            
            if target_file_id:
                symbol_id = await self.resolver.resolve_symbol_import(
                    import_path,
                    symbol_name,
                    file,
                    file_path
                )
                
                if symbol_id:
                    imports.append({
                        'import_path': import_path,
                        'file_id': target_file_id,
                        'symbols': [symbol_id]
                    })
        
        return imports
    
