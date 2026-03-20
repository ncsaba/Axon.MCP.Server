"""Path resolution utilities for imports."""

from pathlib import Path
from typing import Optional, Dict, List
import json
import re

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_PYTHON_SOURCE_ROOT_CANDIDATES = ("src", "lib", "app", "python")


class PathResolver:
    """Resolves import paths to actual file paths."""
    
    def __init__(self, repository_root: Path):
        """
        Initialize path resolver.
        
        Args:
            repository_root: Root directory of repository
        """
        self.repository_root = repository_root
        self.path_aliases = self._load_path_aliases()
    
    def _load_path_aliases(self) -> Dict[str, str]:
        """Load path aliases from tsconfig.json or similar."""
        aliases = {}
        
        # Try to load tsconfig.json for TypeScript path aliases
        tsconfig_path = self.repository_root / "tsconfig.json"
        if tsconfig_path.exists():
            try:
                with open(tsconfig_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                compiler_options = config.get('compilerOptions', {})
                paths = compiler_options.get('paths', {})
                base_url = compiler_options.get('baseUrl', '.')
                
                # Convert TypeScript paths to aliases
                for alias, targets in paths.items():
                    # Remove /* from alias if present
                    clean_alias = alias.replace('/*', '')
                    if targets:
                        # Take first target
                        target = targets[0].replace('/*', '')
                        # Resolve relative to baseUrl
                        resolved = Path(base_url) / target
                        aliases[clean_alias] = str(resolved)
                        
            except Exception as e:
                logger.debug(f"Failed to load tsconfig.json: {str(e)}")
        
        # Try to load package.json for module aliases
        package_json = self.repository_root / "package.json"
        if package_json.exists():
            try:
                with open(package_json, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                # Check for common alias configurations
                if '_moduleAliases' in config:
                    aliases.update(config['_moduleAliases'])
                    
            except Exception as e:
                logger.debug(f"Failed to load package.json: {str(e)}")
        
        return aliases
    
    def resolve_import_path(
        self,
        import_path: str,
        importing_file: Path,
        language: str
    ) -> Optional[Path]:
        """
        Resolve an import path to actual file path.
        
        Args:
            import_path: Import string (e.g., "./utils/helper", "@/components/Button")
            importing_file: File that contains the import
            language: Language (for resolution strategy)
            
        Returns:
            Resolved file path or None if not resolvable
        """
        if language.lower() == "python":
            return self._resolve_python_import_path(import_path, importing_file)

        # Handle relative imports
        if import_path.startswith('.'):
            return self._resolve_relative_path(import_path, importing_file, language)
        
        # Handle aliased imports (e.g., @/components)
        if import_path.startswith('@/') or import_path.startswith('@'):
            return self._resolve_aliased_path(import_path, language)
        
        # Handle absolute/package imports
        return self._resolve_package_path(import_path, language)

    def _resolve_python_import_path(
        self,
        import_path: str,
        importing_file: Path,
    ) -> Optional[Path]:
        """Resolve Python absolute and relative module imports."""
        try:
            importing_rel = importing_file.relative_to(self.repository_root)
        except ValueError:
            return None

        source_root = self._infer_python_source_root(importing_rel)
        package_parts = self._python_package_parts(importing_rel, source_root)

        if import_path.startswith("."):
            full_parts = self._resolve_python_relative_parts(import_path, package_parts)
            root_candidates = [source_root] if source_root else [Path()]
        else:
            cleaned = import_path.strip(".")
            if not cleaned:
                return None
            full_parts = [part for part in cleaned.split(".") if part]
            root_candidates = self._python_absolute_root_candidates(source_root)

        if not full_parts:
            return None

        module_path = Path(*full_parts)
        return self._resolve_python_module_candidates(root_candidates, module_path)
    
    def _resolve_relative_path(
        self,
        import_path: str,
        importing_file: Path,
        language: str
    ) -> Optional[Path]:
        """Resolve relative import path."""
        # Get directory of importing file
        import_dir = importing_file.parent
        
        # Resolve relative path
        resolved = (import_dir / import_path).resolve()
        
        # Try common extensions based on language
        extensions = self._get_extensions_for_language(language)
        
        for ext in extensions:
            candidate = resolved.with_suffix(ext)
            if candidate.exists() and candidate.is_relative_to(self.repository_root):
                # Return path relative to repository root
                return candidate.relative_to(self.repository_root)
            
            # Also try /index.ext pattern
            index_candidate = resolved / f"index{ext}"
            if index_candidate.exists() and index_candidate.is_relative_to(self.repository_root):
                return index_candidate.relative_to(self.repository_root)
        
        return None
    
    def _resolve_aliased_path(
        self,
        import_path: str,
        language: str
    ) -> Optional[Path]:
        """Resolve aliased import path (e.g., @/components)."""
        # Common alias patterns
        if import_path.startswith('@/'):
            # @/ usually maps to src/
            path_without_alias = import_path[2:]  # Remove @/
            
            # Check if we have a specific alias mapping
            if '@' in self.path_aliases:
                base = self.path_aliases['@']
            else:
                # Try common patterns
                for base_dir in ['src', '.', 'app']:
                    base = base_dir
                    resolved = self.repository_root / base / path_without_alias
                    
                    extensions = self._get_extensions_for_language(language)
                    for ext in extensions:
                        candidate = resolved.with_suffix(ext)
                        if candidate.exists():
                            return candidate.relative_to(self.repository_root)
                    
                    # Try index file
                    for ext in extensions:
                        index_candidate = resolved / f"index{ext}"
                        if index_candidate.exists():
                            return index_candidate.relative_to(self.repository_root)
        
        # Check other aliases
        for alias, target in self.path_aliases.items():
            if import_path.startswith(alias):
                path_without_alias = import_path[len(alias):].lstrip('/')
                resolved = self.repository_root / target / path_without_alias
                
                extensions = self._get_extensions_for_language(language)
                for ext in extensions:
                    candidate = resolved.with_suffix(ext)
                    if candidate.exists():
                        return candidate.relative_to(self.repository_root)
        
        return None
    
    def _resolve_package_path(
        self,
        import_path: str,
        language: str
    ) -> Optional[Path]:
        """
        Resolve package import path.
        
        For internal packages, tries to find in src/ or other common locations.
        For external packages (e.g., node_modules), returns None.
        """
        if language.lower() == "java":
            return self._resolve_java_package_path(import_path)

        # Check if it's an external package (contains no path separators)
        if '/' not in import_path:
            # Likely external package (react, lodash, etc.)
            return None
        
        # Try to resolve as internal package
        for base_dir in ['src', 'lib', 'app', 'packages']:
            resolved = self.repository_root / base_dir / import_path
            
            extensions = self._get_extensions_for_language(language)
            for ext in extensions:
                candidate = resolved.with_suffix(ext)
                if candidate.exists():
                    return candidate.relative_to(self.repository_root)

        return None

    def _infer_python_source_root(self, importing_rel: Path) -> Optional[Path]:
        if importing_rel.parts and importing_rel.parts[0] in _PYTHON_SOURCE_ROOT_CANDIDATES:
            return Path(importing_rel.parts[0])
        return None

    def _python_package_parts(
        self,
        importing_rel: Path,
        source_root: Optional[Path],
    ) -> List[str]:
        relative_to_root = importing_rel
        if source_root is not None:
            relative_to_root = importing_rel.relative_to(source_root)

        module_parts = list(relative_to_root.with_suffix("").parts)
        if module_parts and module_parts[-1] == "__init__":
            return module_parts[:-1]
        return module_parts[:-1]

    def _resolve_python_relative_parts(
        self,
        import_path: str,
        package_parts: List[str],
    ) -> List[str]:
        match = re.match(r"^(\.+)(.*)$", import_path)
        if not match:
            return []

        level = len(match.group(1))
        remainder = match.group(2).strip(".")
        ascend = max(level - 1, 0)

        if ascend > len(package_parts):
            return []

        base_parts = package_parts[:len(package_parts) - ascend] if ascend else list(package_parts)
        remainder_parts = [part for part in remainder.split(".") if part] if remainder else []
        return base_parts + remainder_parts

    def _python_absolute_root_candidates(self, source_root: Optional[Path]) -> List[Path]:
        candidates: List[Path] = []
        if source_root is not None:
            candidates.append(source_root)
        candidates.append(Path())
        for root_name in _PYTHON_SOURCE_ROOT_CANDIDATES:
            root = Path(root_name)
            if root not in candidates:
                candidates.append(root)
        return candidates

    def _resolve_python_module_candidates(
        self,
        root_candidates: List[Path],
        module_path: Path,
    ) -> Optional[Path]:
        for root in root_candidates:
            base = self.repository_root / root / module_path

            file_candidate = base.with_suffix(".py")
            if file_candidate.exists() and file_candidate.is_relative_to(self.repository_root):
                return file_candidate.relative_to(self.repository_root)

            package_candidate = base / "__init__.py"
            if package_candidate.exists() and package_candidate.is_relative_to(self.repository_root):
                return package_candidate.relative_to(self.repository_root)

        return None

    def _resolve_java_package_path(self, import_path: str) -> Optional[Path]:
        """
        Resolve Java package imports to a .java file path.

        Examples:
        - com.example.service.UserService -> src/main/java/com/example/service/UserService.java
        - com.example.service.* -> src/main/java/com/example/service (not resolved to a single file)
        """
        if not import_path:
            return None

        # Ignore wildcard imports for file-level resolution.
        if import_path.endswith(".*"):
            return None

        normalized = import_path.replace(".", "/").strip("/")
        if not normalized:
            return None

        # Java source roots in preferred lookup order.
        java_roots = [
            self.repository_root / "src" / "main" / "java",
            self.repository_root / "src" / "test" / "java",
            self.repository_root,
        ]

        for root in java_roots:
            candidate = root / f"{normalized}.java"
            if candidate.exists() and candidate.is_relative_to(self.repository_root):
                return candidate.relative_to(self.repository_root)

        # Support static imports by progressively stripping trailing members.
        # import static a.b.C.D -> try a/b/C.java
        parts = normalized.split("/")
        for end in range(len(parts) - 1, 0, -1):
            prefix = "/".join(parts[:end])
            for root in java_roots:
                candidate = root / f"{prefix}.java"
                if candidate.exists() and candidate.is_relative_to(self.repository_root):
                    return candidate.relative_to(self.repository_root)

        return None
    
    def _get_extensions_for_language(self, language: str) -> List[str]:
        """Get possible file extensions for language."""
        extensions_map = {
            'javascript': ['.js', '.jsx', '.mjs'],
            'typescript': ['.ts', '.tsx', '.d.ts'],
            'java': ['.java'],
            'vue': ['.vue'],
            'python': ['.py'],
            'markdown': ['.md', '.markdown']
        }
        
        return extensions_map.get(language.lower(), [''])
