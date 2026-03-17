"""Build call graph relationships in database."""

import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Symbol, File, Relation, Repository
from src.database.session import AsyncSessionLocal
from src.config.enums import SymbolKindEnum, RelationTypeEnum, LanguageEnum
from src.extractors.call_analyzer import JavaScriptCallAnalyzer, JavaCallAnalyzer, Call
from src.extractors.call_resolver import CallResolver
from src.extractors.strategy_interfaces import CallExtractionStrategy, NullCallStrategy, language_strategy
from src.repository_sources import get_repository_source_registry
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class CallGraphBuilder:
    """Builds call graph relationships for repository."""
    
    def __init__(self, session: AsyncSession):
        """
        Initialize call graph builder.
        
        Args:
            session: Database session
        """
        self.session = session
        self.call_strategies: Dict[LanguageEnum, CallExtractionStrategy] = {
            LanguageEnum.JAVASCRIPT: JavaScriptCallAnalyzer(),
            LanguageEnum.TYPESCRIPT: JavaScriptCallAnalyzer(),
            LanguageEnum.JAVA: JavaCallAnalyzer(),
        }
        self._null_call_strategy = NullCallStrategy()
        self.resolver = CallResolver(session)
    
    async def build_call_relationships(
        self,
        repository_id: int,
        changed_file_ids: Optional[List[int]] = None
    ):
        """
        Build call relationships for entire repository.
        
        Args:
            repository_id: Repository ID
            changed_file_ids: Optional list of file IDs that changed. If provided,
                             only process symbols from these files. If None or empty,
                             process all files (full build).
        """
        logger.info(
            "building_call_relationships",
            repository_id=repository_id,
            changed_file_count=len(changed_file_ids) if changed_file_ids else 0,
            selective_mode=bool(changed_file_ids)
        )
        
        # Get repository
        result = await self.session.execute(
            select(Repository).where(Repository.id == repository_id)
        )
        repo = result.scalar_one_or_none()
        
        if not repo:
            logger.error("repository_not_found", repository_id=repository_id)
            return 0
        
        source = get_repository_source_registry().resolve(repo)
        repo_path = source.get_repository_path(repo)
        
        # Build query for symbols
        query = (
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                Symbol.kind.in_([
                    SymbolKindEnum.METHOD, 
                    SymbolKindEnum.FUNCTION,
                    SymbolKindEnum.PROPERTY,
                    SymbolKindEnum.VARIABLE
                ])
            )
        )
        
        # If changed_file_ids provided, filter to only those files
        if changed_file_ids:
            query = query.where(File.id.in_(changed_file_ids))
        
        result = await self.session.execute(query)
        
        rows = result.all()
        
        # Group symbols by file
        files_map: Dict[int, File] = {}
        file_symbols: Dict[int, List[Symbol]] = defaultdict(list)
        symbol_ids: List[int] = []
        
        for symbol, file in rows:
            files_map[file.id] = file
            file_symbols[file.id].append(symbol)
            symbol_ids.append(symbol.id)
        
        # If selective mode, delete existing relationships from changed files first
        if changed_file_ids and symbol_ids:
            from sqlalchemy import delete as sql_delete
            delete_stmt = sql_delete(Relation).where(
                Relation.from_symbol_id.in_(symbol_ids),
                Relation.relation_type.in_([RelationTypeEnum.CALLS, RelationTypeEnum.USES])
            )
            await self.session.execute(delete_stmt)
            await self.session.commit()
            logger.info(
                "call_graph_deleted_existing_relations",
                repository_id=repository_id,
                changed_file_count=len(changed_file_ids),
                symbol_count=len(symbol_ids)
            )
            
        total_files = len(files_map)
        total_methods = len(rows)
        
        logger.info(
            "call_graph_analysis_started",
            repository_id=repository_id,
            total_files=total_files,
            total_symbols=total_methods
        )
        
        # Process files in parallel with semaphore
        semaphore = asyncio.Semaphore(10)  # Limit concurrency to 10 files
        processed_files = 0
        relationships_created = 0
        
        # Create tasks for all files
        tasks = []
        for file_id, symbols in file_symbols.items():
            file = files_map[file_id]
            task = self._process_file_safe(
                semaphore,
                file,
                symbols,
                repo_path
            )
            tasks.append(task)
        
        # Execute tasks in chunks to avoid creating too many tasks at once
        # and to allow periodic committing
        chunk_size = 50
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            results = await asyncio.gather(*chunk)
            
            # Aggregate results
            batch_relations = []
            for file_relations in results:
                if file_relations:
                    batch_relations.extend(file_relations)
            
            # Add to session and commit
            if batch_relations:
                self.session.add_all(batch_relations)
                relationships_created += len(batch_relations)
                await self.session.commit()
            
            processed_files += len(chunk)
            
            logger.info(
                "call_graph_progress",
                repository_id=repository_id,
                processed_files=processed_files,
                total_files=total_files,
                relationships_created=relationships_created
            )
        
        logger.info(
            "call_graph_completed",
            repository_id=repository_id,
            files_processed=processed_files,
            relationships_created=relationships_created
        )
        
        return relationships_created

    async def _process_file_safe(
        self,
        semaphore: asyncio.Semaphore,
        file: File,
        symbols: List[Symbol],
        repo_path: Path
    ) -> List[Relation]:
        """Wrapper to process file with semaphore."""
        async with semaphore:
            return await self._process_file(file, symbols, repo_path)

    async def _process_file(
        self,
        file: File,
        symbols: List[Symbol],
        repo_path: Path
    ) -> List[Relation]:
        """
        Process a single file: parse once and analyze all symbols.
        
        Args:
            file: File record
            symbols: List of symbols in this file
            repo_path: Repository root path
        Returns:
            List of Relation objects to create
        """
        relations = []
        
        # Create a new session for this task to avoid concurrent usage of the shared session
        async with AsyncSessionLocal() as session:
            local_resolver = CallResolver(session)
            
            try:
                file_path = repo_path / file.path
                
                if not file_path.exists():
                    logger.warning("file_not_found", file_path=str(file_path))
                    return []
                
                # Read file content
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        code = f.read()
                except Exception as e:
                    logger.warning("file_read_error", file_path=str(file_path), error=str(e))
                    return []
                
                # Parse the file to get AST
                from src.parsers import ParserFactory
                parser = ParserFactory.get_parser(file.language)
                
                tree = None
                if hasattr(parser, 'parser'):
                    tree = parser.parser.parse(bytes(code, "utf8"))
                else:
                    logger.warning(
                        "parser_no_tree_sitter",
                        file_path=file.path,
                        parser_type=type(parser).__name__,
                        parser_attrs=dir(parser)[:10]  # Show first 10 attributes for debugging
                    )
                    return []
                
                if tree:
                    # Extract imports if parser supports it
                    imports = []
                    if hasattr(parser, 'extract_imports'):
                        imports = parser.extract_imports(tree.root_node, code)
                    
                    # Fetch field/property symbols for this file to build field_types map
                    field_types = {}
                    result = await session.execute(
                        select(Symbol)
                        .where(
                            Symbol.file_id == file.id,
                            Symbol.kind.in_([SymbolKindEnum.VARIABLE, SymbolKindEnum.PROPERTY])
                        )
                    )
                    field_symbols = result.scalars().all()
                    
                    # Build map of field name -> type
                    for field_symbol in field_symbols:
                        if field_symbol.return_type:
                            # Use simple name (e.g., _service -> IUserService)
                            field_types[field_symbol.name] = field_symbol.return_type
                    
                    # Process each symbol in the file
                    symbols_processed = 0
                    symbols_with_nodes = 0
                    total_calls_detected = 0
                    total_calls_resolved = 0
                    
                    for symbol in symbols:
                        symbols_processed += 1
                        
                        # Find the symbol's node in the tree
                        symbol_node = self._find_symbol_node(tree.root_node, symbol, code)
                        
                        if symbol_node:
                            symbols_with_nodes += 1
                            
                            # Extract calls based on language
                            calls = await self._extract_calls_for_symbol(
                                symbol_node,
                                code,
                                file.language
                            )
                            
                            total_calls_detected += len(calls)
                            
                            # Resolve and create relationships
                            for call in calls:
                                target_id = await local_resolver.resolve_call_target(
                                    call,
                                    symbol,
                                    file,
                                    imports,
                                    field_types,
                                    code
                                )
                                
                                if target_id:
                                    # Target is guaranteed to exist as it was resolved from DB
                                    total_calls_resolved += 1
                                    
                                    # Create CALLS relationship
                                    relation = Relation(
                                        from_symbol_id=symbol.id,
                                        to_symbol_id=target_id,
                                        relation_type=RelationTypeEnum.CALLS,
                                        relation_metadata={
                                            'is_async': call.is_async
                                        },
                                        start_line=call.line_number,
                                        end_line=call.end_line,
                                        start_column=call.start_column,
                                        end_column=call.end_column
                                    )
                                    relations.append(relation)
                                    
                            # Extract usages based on language
                            usages = await self._extract_usages_for_symbol(
                                symbol_node,
                                code,
                                file.language
                            )
                            
                            for usage in usages:
                                target_id = await local_resolver.resolve_usage_target(
                                    usage.method_name, # method_name holds the variable name
                                    symbol,
                                    file,
                                    imports,
                                    receiver=usage.receiver,
                                    field_types=field_types
                                )
                                
                                if target_id:
                                    # Target is guaranteed to exist
                                    # Create USES relationship
                                    relation = Relation(
                                        from_symbol_id=symbol.id,
                                        to_symbol_id=target_id,
                                        relation_type=RelationTypeEnum.USES,
                                        start_line=usage.line_number,
                                        end_line=usage.end_line,
                                        start_column=usage.start_column,
                                        end_column=usage.end_column
                                    )
                                    relations.append(relation)
                        else:
                            # Log when we can't find the node
                            logger.debug(
                                "symbol_node_not_found",
                                symbol_name=symbol.name,
                                start_line=symbol.start_line,
                                file_path=file.path
                            )
                    
                    # Log summary for this file (INFO level so it shows in logs)
                    if symbols_processed > 0:
                        logger.info(
                            "file_call_analysis_summary",
                            file_path=file.path,
                            symbols_processed=symbols_processed,
                            symbols_with_nodes=symbols_with_nodes,
                            calls_detected=total_calls_detected,
                            calls_resolved=total_calls_resolved,
                            relations_created=len([r for r in relations if r.from_symbol_id in [s.id for s in symbols]])
                        )
                                    
            except Exception as e:
                logger.error(
                    "file_processing_failed",
                    file_path=file.path,
                    error=str(e)
                )
            
        return relations
    
    def _find_symbol_node(
        self,
        root_node: "tree_sitter.Node",
        symbol: Symbol,
        code: str
    ) -> Optional["tree_sitter.Node"]:
        """
        Find the AST node for a symbol by line number.
        
        Args:
            root_node: Root of AST
            symbol: Symbol to find
            code: Source code
            
        Returns:
            Node for the symbol or None
        """
        # Simple heuristic: find node at symbol's start line
        # We use a non-recursive iterative approach or optimized traversal if possible
        # But for now, simple traversal is fine as we are already inside a per-file task
        
        target_line = symbol.start_line - 1 # 0-indexed
        
        # Optimization: Only search nodes that span the target line
        cursor = root_node.walk()
        
        visited_children = False
        while True:
            node = cursor.node
            
            # Check if this node contains the target line
            if node.start_point[0] <= target_line <= node.end_point[0]:
                # If it starts exactly on the line, check if it's a function
                if node.start_point[0] == target_line:
                    if node.type in [
                        'method_declaration', 'function_declaration',
                        'arrow_function', 'function', 'local_function_statement',
                        'lambda_expression', 'simple_lambda_expression', 'parenthesized_lambda_expression',
                        'constructor_declaration', 'field_declaration', 'property_declaration',
                        'variable_declarator'
                    ]:
                        return node
                
                # If we haven't visited children yet, go to first child
                if not visited_children and cursor.goto_first_child():
                    continue
            
            # Try next sibling
            if cursor.goto_next_sibling():
                visited_children = False
            elif cursor.goto_parent():
                visited_children = True
            else:
                break
                
        return None
    
    async def _extract_calls_for_symbol(
        self,
        symbol_node: "tree_sitter.Node",
        code: str,
        language: LanguageEnum
    ) -> List[Call]:
        """Extract calls from symbol based on language."""
        strategy = language_strategy(self.call_strategies, language, self._null_call_strategy)
        return strategy.extract_calls(symbol_node, code)

    async def _extract_usages_for_symbol(
        self,
        symbol_node: "tree_sitter.Node",
        code: str,
        language: LanguageEnum
    ) -> List[Call]:
        """Extract usages from symbol based on language."""
        strategy = language_strategy(self.call_strategies, language, self._null_call_strategy)
        return strategy.extract_usages(symbol_node, code)
    
