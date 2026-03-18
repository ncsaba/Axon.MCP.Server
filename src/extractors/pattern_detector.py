"""Pattern detection for design patterns and architectural layers."""

from typing import List, Dict, Any, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Symbol, File, Relation
from src.database.query_helpers import active_file_filter
from src.config.enums import SymbolKindEnum, RelationTypeEnum, AccessModifierEnum
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class Pattern:
    """Represents a detected pattern."""
    
    def __init__(
        self,
        pattern_type: str,
        pattern_name: str,
        confidence: float,
        symbols: List[int],
        description: str,
        evidence: List[str]
    ):
        self.pattern_type = pattern_type
        self.pattern_name = pattern_name
        self.confidence = confidence
        self.symbols = symbols
        self.description = description
        self.evidence = evidence


class PatternDetector:
    """Detects design patterns and architectural patterns in code."""
    
    # Role detection keywords mapped to role names
    ROLE_KEYWORDS = {
        'Controller': ['controller', 'api', 'endpoint'],
        'Service': ['service', 'manager', 'handler', 'provider'],
        'Repository': ['repository', 'repo', 'dao', 'dataaccess'],
        'Model': ['model', 'entity', 'dto', 'viewmodel'],
        'Utility': ['helper', 'util', 'utils', 'utility', 'extension'],
    }
    
    # Factory pattern keywords
    FACTORY_KEYWORDS = ['factory', 'builder', 'creator', 'provider']
    
    @staticmethod
    def detect_roles(symbol) -> List[str]:
        """
        Detect roles based on symbol naming conventions.
        
        Args:
            symbol: Symbol object to analyze
            
        Returns:
            List of detected role names
        """
        roles = []
        
        # Get name to check (use fully qualified name if available)
        name_to_check = (symbol.fully_qualified_name or symbol.name or '').lower()
        
        for role, keywords in PatternDetector.ROLE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in name_to_check:
                    if role not in roles:
                        roles.append(role)
                    break
        
        return roles
    
    @staticmethod
    def detect_factory_pattern(symbols: List) -> List:
        """
        Detect factory pattern based on naming conventions.
        
        Args:
            symbols: List of Symbol objects to analyze
            
        Returns:
            List of symbols that are factories/builders
        """
        factories = []
        
        for symbol in symbols:
            # Only check classes with valid attributes
            if not hasattr(symbol, 'kind') or not hasattr(symbol, 'name'):
                continue
            
            if symbol.kind != SymbolKindEnum.CLASS:
                continue
            
            name_lower = (symbol.name or '').lower()
            
            for keyword in PatternDetector.FACTORY_KEYWORDS:
                if keyword in name_lower:
                    factories.append(symbol)
                    break
        
        return factories
    
    def __init__(self, session: AsyncSession):
        """
        Initialize pattern detector.
        
        Args:
            session: Database session
        """
        self.session = session
    
    async def detect_patterns(self, repository_id: int) -> List[Pattern]:
        """
        Detect all patterns in repository.
        
        Args:
            repository_id: Repository ID
            
        Returns:
            List of detected patterns
        """
        patterns = []
        
        # Detect design patterns
        patterns.extend(await self._detect_singleton_pattern(repository_id))
        patterns.extend(await self._detect_factory_pattern(repository_id))
        patterns.extend(await self._detect_repository_pattern(repository_id))
        patterns.extend(await self._detect_builder_pattern(repository_id))
        
        # Detect architectural layers
        patterns.extend(await self._detect_architectural_layers(repository_id))
        
        # Detect anti-patterns
        patterns.extend(await self._detect_god_class(repository_id))
        patterns.extend(await self._detect_circular_dependencies(repository_id))
        
        return patterns
    
    async def _detect_singleton_pattern(self, repository_id: int) -> List[Pattern]:
        """Detect Singleton pattern."""
        patterns = []
        
        # Look for classes with:
        # 1. Private static instance field
        # 2. Private constructor
        # 3. Public static GetInstance() method
        
        result = await self.session.execute(
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind == SymbolKindEnum.CLASS
            )
        )
        
        for class_symbol, file in result.all():
            evidence = []
            
            # Get all members of this class
            members_result = await self.session.execute(
                select(Symbol).where(
                    Symbol.parent_name == class_symbol.fully_qualified_name
                )
            )
            members = members_result.scalars().all()
            
            has_private_static_instance = False
            has_private_constructor = False
            has_get_instance = False
            
            for member in members:
                # Check for private static instance field
                if member.kind == SymbolKindEnum.VARIABLE:
                    if member.access_modifier == AccessModifierEnum.PRIVATE:
                        if 'instance' in member.name.lower():
                            has_private_static_instance = True
                            evidence.append(f"Private static instance field: {member.name}")
                
                # Check for private constructor
                if member.kind == SymbolKindEnum.METHOD:
                    if member.name == class_symbol.name:  # Constructor
                        if member.access_modifier == AccessModifierEnum.PRIVATE:
                            has_private_constructor = True
                            evidence.append(f"Private constructor")
                    
                    # Check for GetInstance method
                    if 'getinstance' in member.name.lower() or 'instance' in member.name.lower():
                        if member.access_modifier == AccessModifierEnum.PUBLIC:
                            has_get_instance = True
                            evidence.append(f"Public instance accessor: {member.name}")
            
            # If at least 2 of 3 criteria met, likely singleton
            criteria_met = sum([
                has_private_static_instance,
                has_private_constructor,
                has_get_instance
            ])
            
            if criteria_met >= 2:
                confidence = criteria_met / 3.0
                patterns.append(Pattern(
                    pattern_type='design_pattern',
                    pattern_name='Singleton',
                    confidence=confidence,
                    symbols=[class_symbol.id],
                    description=f'{class_symbol.name} implements Singleton pattern',
                    evidence=evidence
                ))
        
        return patterns
    
    async def _detect_factory_pattern(self, repository_id: int) -> List[Pattern]:
        """Detect Factory pattern."""
        patterns = []
        
        # Look for classes with:
        # 1. Name contains "Factory"
        # 2. Has Create/Make/Build methods
        
        result = await self.session.execute(
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind == SymbolKindEnum.CLASS,
                Symbol.name.ilike('%Factory%')
            )
        )
        
        for class_symbol, file in result.all():
            evidence = [f"Class name contains 'Factory': {class_symbol.name}"]
            
            # Get methods
            methods_result = await self.session.execute(
                select(Symbol).where(
                    Symbol.parent_name == class_symbol.fully_qualified_name,
                    Symbol.kind == SymbolKindEnum.METHOD
                )
            )
            
            has_factory_method = False
            for method in methods_result.scalars():
                if any(keyword in method.name.lower() for keyword in ['create', 'make', 'build', 'get']):
                    has_factory_method = True
                    evidence.append(f"Factory method: {method.name}")
            
            if has_factory_method:
                patterns.append(Pattern(
                    pattern_type='design_pattern',
                    pattern_name='Factory',
                    confidence=0.8,
                    symbols=[class_symbol.id],
                    description=f'{class_symbol.name} implements Factory pattern',
                    evidence=evidence
                ))
        
        return patterns
    
    async def _detect_repository_pattern(self, repository_id: int) -> List[Pattern]:
        """Detect Repository pattern."""
        patterns = []
        
        # Look for classes/interfaces with:
        # 1. Name contains "Repository"
        # 2. Has CRUD methods (Add, Get, Update, Delete, Find)
        
        result = await self.session.execute(
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind.in_([SymbolKindEnum.CLASS, SymbolKindEnum.INTERFACE]),
                Symbol.name.ilike('%Repository%')
            )
        )
        
        for symbol, file in result.all():
            evidence = [f"Name contains 'Repository': {symbol.name}"]
            
            # Get methods
            methods_result = await self.session.execute(
                select(Symbol).where(
                    Symbol.parent_name == symbol.fully_qualified_name,
                    Symbol.kind == SymbolKindEnum.METHOD
                )
            )
            
            crud_methods = {'add': False, 'get': False, 'update': False, 'delete': False, 'find': False}
            
            for method in methods_result.scalars():
                method_lower = method.name.lower()
                for crud_op in crud_methods.keys():
                    if crud_op in method_lower:
                        crud_methods[crud_op] = True
                        evidence.append(f"CRUD method: {method.name}")
            
            # If has at least 3 CRUD operations
            if sum(crud_methods.values()) >= 3:
                confidence = sum(crud_methods.values()) / 5.0
                patterns.append(Pattern(
                    pattern_type='design_pattern',
                    pattern_name='Repository',
                    confidence=confidence,
                    symbols=[symbol.id],
                    description=f'{symbol.name} implements Repository pattern',
                    evidence=evidence
                ))
        
        return patterns
    
    async def _detect_builder_pattern(self, repository_id: int) -> List[Pattern]:
        """Detect Builder pattern."""
        patterns = []
        
        # Look for classes with:
        # 1. Name contains "Builder"
        # 2. Has fluent methods (return 'this' or builder type)
        # 3. Has Build() method
        
        result = await self.session.execute(
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind == SymbolKindEnum.CLASS,
                Symbol.name.ilike('%Builder%')
            )
        )
        
        for class_symbol, file in result.all():
            evidence = [f"Class name contains 'Builder': {class_symbol.name}"]
            
            # Get methods
            methods_result = await self.session.execute(
                select(Symbol).where(
                    Symbol.parent_name == class_symbol.fully_qualified_name,
                    Symbol.kind == SymbolKindEnum.METHOD
                )
            )
            
            has_build_method = False
            fluent_methods = 0
            
            for method in methods_result.scalars():
                if method.name.lower() == 'build':
                    has_build_method = True
                    evidence.append("Has Build() method")
                
                # Check if method returns same type (fluent interface)
                if method.return_type and class_symbol.name in method.return_type:
                    fluent_methods += 1
            
            if has_build_method and fluent_methods > 0:
                confidence = min(0.9, 0.5 + (fluent_methods * 0.1))
                evidence.append(f"Has {fluent_methods} fluent methods")
                
                patterns.append(Pattern(
                    pattern_type='design_pattern',
                    pattern_name='Builder',
                    confidence=confidence,
                    symbols=[class_symbol.id],
                    description=f'{class_symbol.name} implements Builder pattern',
                    evidence=evidence
                ))
        
        return patterns
    
    async def _detect_architectural_layers(self, repository_id: int) -> List[Pattern]:
        """Detect architectural layers (Controller, Service, Repository)."""
        patterns = []
        
        # Detect controllers
        controllers_result = await self.session.execute(
            select(func.count(Symbol.id))
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind == SymbolKindEnum.CLASS,
                Symbol.name.ilike('%Controller%')
            )
        )
        controller_count = controllers_result.scalar()
        
        if controller_count > 0:
            patterns.append(Pattern(
                pattern_type='architectural_layer',
                pattern_name='Controller Layer',
                confidence=1.0,
                symbols=[],
                description=f'Detected {controller_count} controller classes',
                evidence=[f'{controller_count} controllers found']
            ))
        
        # Detect services
        services_result = await self.session.execute(
            select(func.count(Symbol.id))
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind == SymbolKindEnum.CLASS,
                Symbol.name.ilike('%Service%')
            )
        )
        service_count = services_result.scalar()
        
        if service_count > 0:
            patterns.append(Pattern(
                pattern_type='architectural_layer',
                pattern_name='Service Layer',
                confidence=1.0,
                symbols=[],
                description=f'Detected {service_count} service classes',
                evidence=[f'{service_count} services found']
            ))
        
        # Detect repositories
        repos_result = await self.session.execute(
            select(func.count(Symbol.id))
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind.in_([SymbolKindEnum.CLASS, SymbolKindEnum.INTERFACE]),
                Symbol.name.ilike('%Repository%')
            )
        )
        repo_count = repos_result.scalar()
        
        if repo_count > 0:
            patterns.append(Pattern(
                pattern_type='architectural_layer',
                pattern_name='Repository Layer',
                confidence=1.0,
                symbols=[],
                description=f'Detected {repo_count} repository classes/interfaces',
                evidence=[f'{repo_count} repositories found']
            ))
        
        return patterns
    
    async def _detect_god_class(self, repository_id: int) -> List[Pattern]:
        """Detect God Class anti-pattern (classes with too many methods)."""
        patterns = []
        
        # Import aliased for proper table aliasing
        from sqlalchemy.orm import aliased
        
        # Create alias for methods (child symbols)
        methods_alias = aliased(Symbol, name='methods')
        
        # Find classes with > 30 methods
        result = await self.session.execute(
            select(
                Symbol.id,
                Symbol.name,
                Symbol.fully_qualified_name,
                File.id,
                func.count(methods_alias.id).label('method_count')
            )
            .join(File, Symbol.file_id == File.id)
            .outerjoin(
                methods_alias,
                Symbol.fully_qualified_name == methods_alias.parent_name
            )
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Symbol.kind == SymbolKindEnum.CLASS
            )
            .group_by(Symbol.id, Symbol.name, Symbol.fully_qualified_name, File.id)
            .having(func.count(methods_alias.id) > 30)
        )
        
        for symbol_id, symbol_name, symbol_fqn, file_id, method_count in result.all():
            if method_count and method_count > 30:
                confidence = min(1.0, method_count / 50.0)
                
                patterns.append(Pattern(
                    pattern_type='anti_pattern',
                    pattern_name='God Class',
                    confidence=confidence,
                    symbols=[symbol_id],
                    description=f'{symbol_name} has {method_count} methods (God Class)',
                    evidence=[
                        f'Class has {method_count} methods',
                        'Consider splitting into smaller classes'
                    ]
                ))
        
        return patterns
    
    async def _detect_circular_dependencies(self, repository_id: int) -> List[Pattern]:
        """Detect circular dependencies between files/symbols."""
        patterns = []
        
        # This requires graph traversal of relationships
        # Enhanced version: Use DFS to detect cycles of any length (not just A↔B)
        
        result = await self.session.execute(
            select(Relation)
            .join(Symbol, Relation.from_symbol_id == Symbol.id)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                active_file_filter(),
                Relation.relation_type.in_([RelationTypeEnum.IMPORTS, RelationTypeEnum.USES])
            )
        )
        
        relations = result.scalars().all()
        
        # Build adjacency map
        adjacency = {}
        for rel in relations:
            if rel.from_symbol_id not in adjacency:
                adjacency[rel.from_symbol_id] = []
            adjacency[rel.from_symbol_id].append(rel.to_symbol_id)
        
        # DFS-based cycle detection
        def find_cycles_dfs():
            """Find all cycles using DFS with backtracking."""
            visited = set()
            rec_stack = set()
            cycles = []
            
            def dfs(node, path):
                if node in rec_stack:
                    # Found a cycle - extract it from the path
                    cycle_start = path.index(node)
                    cycle = path[cycle_start:-1]  # Exclude the last node (it's a duplicate of the first)
                    # Normalize cycle representation (sort to avoid duplicates)
                    cycle_key = tuple(sorted(cycle))
                    if cycle_key not in [tuple(sorted(c)) for c in cycles]:
                        cycles.append(cycle)
                    return
                
                if node in visited:
                    return
                
                visited.add(node)
                rec_stack.add(node)
                
                if node in adjacency:
                    for neighbor in adjacency[node]:
                        dfs(neighbor, path + [neighbor])
                
                rec_stack.remove(node)
            
            # Start DFS from each node
            for start_node in adjacency:
                dfs(start_node, [start_node])
            
            return cycles
        
        cycles_found = find_cycles_dfs()
        
        # Create patterns for detected cycles
        for cycle in cycles_found:
            if len(cycle) >= 2:  # Only report meaningful cycles
                patterns.append(Pattern(
                    pattern_type='anti_pattern',
                    pattern_name='Circular Dependency',
                    confidence=1.0,
                    symbols=list(cycle),
                    description=f'Circular dependency detected: {len(cycle)}-node cycle',
                    evidence=[
                        f'Cycle involves {len(cycle)} symbols',
                        'Consider refactoring to break the cycle'
                    ]
                ))
        
        return patterns
