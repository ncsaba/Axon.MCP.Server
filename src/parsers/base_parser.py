from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Union
from pathlib import Path
from src.config.enums import LanguageEnum, SymbolKindEnum, AccessModifierEnum


@dataclass
class ImportedSymbol:
    """Represents a single symbol imported from a module.
    
    Examples:
        from os import path -> ImportedSymbol(name="path", alias=None)
        from os import path as p -> ImportedSymbol(name="path", alias="p")
    """
    name: str
    alias: Optional[str] = None


@dataclass
class ImportRecord:
    """Structured record of an import statement.
    
    Supports all Python import patterns:
        import os -> ImportRecord(module="os")
        import numpy as np -> ImportRecord(module="numpy", alias="np")
        from os import path -> ImportRecord(module="os", symbols=[ImportedSymbol("path")])
        from os import path as p -> ImportRecord(module="os", symbols=[ImportedSymbol("path", "p")])
        from os import * -> ImportRecord(module="os", is_wildcard=True)
        from . import module -> ImportRecord(module="", level=1)
        from ..pkg import X -> ImportRecord(module="pkg", level=2)
    """
    module: str
    level: int = 0  # Relative import level (0 = absolute)
    symbols: List[ImportedSymbol] = field(default_factory=list)
    alias: Optional[str] = None  # Module alias (import X as Y)
    is_wildcard: bool = False
    line_number: int = 0
    is_conditional: bool = False  # Inside try/except ImportError
    is_dynamic: bool = False  # importlib.import_module or __import__

    def to_legacy_string(self) -> str:
        """Convert to legacy string format for backward compatibility.
        
        Examples:
            ImportRecord(module="os") -> "os"
            ImportRecord(module="os", symbols=[ImportedSymbol("path")]) -> "os.path"
            ImportRecord(module="os", level=1) -> ".os"
        """
        if self.level > 0:
            prefix = "." * self.level
            if self.module:
                return f"{prefix}{self.module}"
            return prefix
        
        if self.symbols:
            # For from-imports, return module.symbol format
            first_symbol = self.symbols[0].name
            if self.module:
                return f"{self.module}.{first_symbol}"
            return first_symbol
        
        return self.module


@dataclass
class ParsedSymbol:
    """Represents a parsed code symbol."""
    kind: SymbolKindEnum
    name: str
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    signature: Optional[str] = None
    documentation: Optional[str] = None
    structured_docs: Optional[Dict[str, Any]] = None  # XML/JSDoc structured documentation
    parameters: List[Dict[str, Any]] = None
    return_type: Optional[str] = None
    access_modifier: Optional[AccessModifierEnum] = None
    parent_name: Optional[str] = None
    fully_qualified_name: Optional[str] = None
    references: List[Dict[str, Any]] = None  # List of extracted references (name, location, type)
    generic_parameters: List[Dict[str, Any]] = None  # Phase 2.3
    constraints: List[Dict[str, Any]] = None  # Phase 2.3
    complexity: int = 1  # Phase 3: Cyclomatic complexity score
    
    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.parameters is None:
            self.parameters = []
        if self.references is None:
            self.references = []
        if self.generic_parameters is None:
            self.generic_parameters = []
        if self.constraints is None:
            self.constraints = []

@dataclass
class ParseResult:
    """Result of parsing a file."""
    language: LanguageEnum
    file_path: str
    symbols: List[ParsedSymbol]
    imports: List[str]  # Legacy: flat strings for backward compatibility
    exports: List[str]
    parse_errors: List[str]
    parse_duration_ms: float
    api_calls: List[Dict[str, Any]] = None  # Phase 2: HTTP API calls detected
    events: List[Dict[str, Any]] = None  # Phase 2: Event publishing/subscription detected
    import_records: List[ImportRecord] = None  # Structured import records
    
    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.symbols is None:
            self.symbols = []
        if self.imports is None:
            self.imports = []
        if self.exports is None:
            self.exports = []
        if self.parse_errors is None:
            self.parse_errors = []
        if self.api_calls is None:
            self.api_calls = []
        if self.events is None:
            self.events = []
        if self.import_records is None:
            self.import_records = []
            
    @property
    def success(self) -> bool:
        """Return True if parsing was successful (no errors)."""
        return len(self.parse_errors) == 0
    
    def get_imports_structured(self) -> List[ImportRecord]:
        """Get structured import records, converting from legacy if needed."""
        if self.import_records:
            return self.import_records
        # Convert legacy strings to basic ImportRecords
        return [ImportRecord(module=imp) for imp in self.imports if imp]

class BaseParser(ABC):
    """Base parser interface for all language parsers."""
    
    @abstractmethod
    def parse(self, code: str, file_path: Optional[str] = None) -> ParseResult:
        """
        Parse source code and extract symbols.
        
        Args:
            code: Source code content
            file_path: Optional file path for context
            
        Returns:
            ParseResult with extracted symbols
        """
        pass
    
    @abstractmethod
    def get_language(self) -> LanguageEnum:
        """Return the language this parser handles."""
        pass
    
    @abstractmethod
    def is_supported(self, file_path: Path) -> bool:
        """Check if this parser supports the given file."""
        pass
    
    async def cleanup(self):
        """
        Clean up any resources (e.g., persistent processes).
        Override in subclasses if cleanup is needed.
        """
        pass

