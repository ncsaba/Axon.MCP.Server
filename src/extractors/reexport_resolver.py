"""Re-export resolution for Python __init__.py files.

This module handles Python package re-export semantics where __init__.py files
re-export symbols from submodules, making them available at the package level.

Example:
    # mypackage/__init__.py
    from .module import SomeClass, some_function
    from .submodule import AnotherClass as AliasedClass

    # User code can then do:
    from mypackage import SomeClass  # Actually from mypackage.module
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ReexportEntry:
    """A single re-export entry from an __init__.py file.

    Attributes:
        symbol_name: The name exposed at the package level
        source_module: The relative module path (e.g., ".module", "..submodule")
        original_name: The original symbol name in the source module
        alias: The alias used (if any), same as symbol_name when aliased
        line_number: Line number in __init__.py
        is_wildcard: Whether this is a wildcard import (from .module import *)
    """
    symbol_name: str
    source_module: str
    original_name: str
    alias: Optional[str] = None
    line_number: int = 0
    is_wildcard: bool = False


@dataclass
class PackageReexportMap:
    """Re-export map for a single package.

    Attributes:
        package_path: Path to the package directory (relative to repo root)
        init_file_path: Path to the __init__.py file
        exports: Mapping of symbol name to re-export entry
        wildcard_modules: Set of modules that were wildcard-imported
        all_declaration: Symbols listed in __all__ (if present)
    """
    package_path: str
    init_file_path: str
    exports: Dict[str, ReexportEntry] = field(default_factory=dict)
    wildcard_modules: Set[str] = field(default_factory=set)
    all_declaration: Optional[List[str]] = None

    def get_source_info(self, symbol_name: str) -> Optional[Tuple[str, str]]:
        """Get the source module and original name for a symbol.

        Args:
            symbol_name: The symbol name as imported at package level

        Returns:
            Tuple of (source_module, original_name) or None if not a re-export
        """
        entry = self.exports.get(symbol_name)
        if entry:
            return (entry.source_module, entry.original_name)
        return None

    def is_reexported(self, symbol_name: str) -> bool:
        """Check if a symbol is re-exported from this package."""
        return symbol_name in self.exports


class ReexportResolver:
    """Resolves Python re-exports from __init__.py files.

    This class parses __init__.py files to build a map of re-exported symbols,
    then uses that map to resolve package-level imports to their actual source modules.
    """

    def __init__(self, repository_root: Path):
        """Initialize the re-export resolver.

        Args:
            repository_root: Root directory of the repository
        """
        self.repository_root = repository_root
        self._cache: Dict[str, PackageReexportMap] = {}

    def parse_init_file(self, init_path: Path) -> PackageReexportMap:
        """Parse an __init__.py file and build its re-export map.

        Args:
            init_path: Path to the __init__.py file

        Returns:
            PackageReexportMap with all re-export information
        """
        try:
            relative_path = init_path.relative_to(self.repository_root)
        except ValueError:
            relative_path = init_path

        package_path = str(relative_path.parent)
        init_file_path = str(relative_path)

        result = PackageReexportMap(
            package_path=package_path,
            init_file_path=init_file_path
        )

        if not init_path.exists():
            return result

        try:
            code = init_path.read_text(encoding='utf-8', errors='ignore')
            tree = ast.parse(code)
        except (SyntaxError, Exception) as e:
            logger.debug(f"Failed to parse {init_path}: {e}")
            return result

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self._process_import_from(node, result)
            elif isinstance(node, ast.Assign):
                self._process_all_assignment(node, result)
            elif isinstance(node, ast.AugAssign):
                self._process_all_aug_assignment(node, result)

        # If __all__ is defined, filter exports to only include those symbols
        if result.all_declaration is not None:
            filtered_exports = {
                name: entry
                for name, entry in result.exports.items()
                if name in result.all_declaration
            }
            result.exports = filtered_exports

        return result

    def _process_import_from(self, node: ast.ImportFrom, result: PackageReexportMap):
        """Process a 'from .module import ...' statement."""
        level = node.level
        module = node.module or ''

        # Build relative module path
        if level > 0:
            source_module = '.' * level + module
        else:
            source_module = module

        for alias in node.names:
            if alias.name == '*':
                # Wildcard import
                result.wildcard_modules.add(source_module)
                logger.debug(
                    "wildcard_import_found",
                    module=source_module,
                    package=result.package_path
                )
                continue

            # Named import
            symbol_name = alias.asname or alias.name
            entry = ReexportEntry(
                symbol_name=symbol_name,
                source_module=source_module,
                original_name=alias.name,
                alias=alias.asname,
                line_number=node.lineno,
                is_wildcard=False
            )
            result.exports[symbol_name] = entry

    def _process_all_assignment(self, node: ast.Assign, result: PackageReexportMap):
        """Process __all__ = ['Symbol1', 'Symbol2'] assignment."""
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == '__all__':
                exports = self._extract_all_list(node.value)
                if exports:
                    result.all_declaration = exports

    def _process_all_aug_assignment(self, node: ast.AugAssign, result: PackageReexportMap):
        """Process __all__ += ['Symbol3'] augmented assignment."""
        if isinstance(node.target, ast.Name) and node.target.id == '__all__':
            exports = self._extract_all_list(node.value)
            if exports:
                if result.all_declaration is None:
                    result.all_declaration = []
                result.all_declaration.extend(exports)

    def _extract_all_list(self, value: ast.AST) -> Optional[List[str]]:
        """Extract string values from __all__ list/tuple/set."""
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            exports = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    exports.append(elt.value)
            return exports
        return None

    def get_package_map(self, package_path: str) -> Optional[PackageReexportMap]:
        """Get the re-export map for a package, using cache if available.

        Args:
            package_path: Path to the package directory (relative to repo root)

        Returns:
            PackageReexportMap or None if package doesn't exist
        """
        if package_path in self._cache:
            return self._cache[package_path]

        # Try to find and parse __init__.py
        init_path = self.repository_root / package_path / "__init__.py"
        if not init_path.exists():
            return None

        result = self.parse_init_file(init_path)
        self._cache[package_path] = result
        return result

    def resolve_reexport(
        self,
        package_path: str,
        symbol_name: str
    ) -> Optional[Tuple[str, str]]:
        """Resolve a package-level symbol to its source module and original name.

        Args:
            package_path: Path to the package directory
            symbol_name: The symbol name as imported at package level

        Returns:
            Tuple of (source_module_relative_path, original_symbol_name) or None
        """
        package_map = self.get_package_map(package_path)
        if not package_map:
            return None

        return package_map.get_source_info(symbol_name)

    def resolve_to_file_path(
        self,
        package_path: str,
        symbol_name: str,
        source_root: Optional[Path] = None
    ) -> Optional[Tuple[Path, str]]:
        """Resolve a re-exported symbol to its source file path.

        Args:
            package_path: Path to the package directory
            symbol_name: The symbol name as imported at package level
            source_root: Optional source root (e.g., 'src') for resolution

        Returns:
            Tuple of (source_file_path, original_symbol_name) or None
        """
        source_info = self.resolve_reexport(package_path, symbol_name)
        if not source_info:
            return None

        source_module, original_name = source_info

        # Convert relative module to file path
        # e.g., ".submodule" -> package_path/submodule.py
        # e.g., "..sibling" -> parent/sibling.py

        package_dir = self.repository_root / package_path

        # Count relative levels
        level = 0
        module_part = source_module
        while module_part.startswith('.'):
            level += 1
            module_part = module_part[1:]

        # Navigate up from package directory
        target_dir = package_dir
        for _ in range(level - 1):  # -1 because first dot is the package itself
            target_dir = target_dir.parent

        # Add module path
        if module_part:
            module_path = module_part.replace('.', '/')
            target_file = target_dir / f"{module_path}.py"
        else:
            # from . import Symbol case - look in package's __init__.py
            target_file = package_dir / "__init__.py"

        if target_file.exists():
            return (target_file, original_name)

        # Try as package
        if module_part:
            module_path = module_part.replace('.', '/')
            package_init = target_dir / module_path / "__init__.py"
            if package_init.exists():
                return (package_init, original_name)

        return None

    def clear_cache(self):
        """Clear the re-export map cache."""
        self._cache.clear()

    def preload_packages(self, package_paths: List[str]):
        """Pre-load re-export maps for multiple packages.

        Args:
            package_paths: List of package paths to preload
        """
        for package_path in package_paths:
            self.get_package_map(package_path)


def find_python_packages(repository_root: Path) -> List[str]:
    """Find all Python packages in a repository (directories with __init__.py).

    Args:
        repository_root: Root directory of the repository

    Returns:
        List of package paths (relative to repository root)
    """
    packages = []

    for init_file in repository_root.rglob("__init__.py"):
        try:
            relative = init_file.relative_to(repository_root)
            package_path = str(relative.parent)
            packages.append(package_path)
        except ValueError:
            continue

    return packages
