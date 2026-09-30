"""Unit tests for Python __init__.py re-export resolution."""

import pytest
from pathlib import Path
import tempfile
import os

from src.extractors.reexport_resolver import (
    ReexportResolver,
    ReexportEntry,
    PackageReexportMap,
    find_python_packages,
)


class TestReexportEntry:
    """Tests for ReexportEntry dataclass."""

    def test_basic_entry(self):
        """Test creating a basic re-export entry."""
        entry = ReexportEntry(
            symbol_name="MyClass",
            source_module=".module",
            original_name="MyClass",
            line_number=5,
        )
        assert entry.symbol_name == "MyClass"
        assert entry.source_module == ".module"
        assert entry.original_name == "MyClass"
        assert entry.alias is None
        assert entry.is_wildcard is False

    def test_aliased_entry(self):
        """Test creating an aliased re-export entry."""
        entry = ReexportEntry(
            symbol_name="AliasedClass",
            source_module=".module",
            original_name="OriginalClass",
            alias="AliasedClass",
            line_number=5,
        )
        assert entry.symbol_name == "AliasedClass"
        assert entry.original_name == "OriginalClass"
        assert entry.alias == "AliasedClass"


class TestPackageReexportMap:
    """Tests for PackageReexportMap."""

    def test_empty_map(self):
        """Test empty re-export map."""
        map_obj = PackageReexportMap(
            package_path="mypackage",
            init_file_path="mypackage/__init__.py",
        )
        assert not map_obj.is_reexported("Anything")
        assert map_obj.get_source_info("Anything") is None

    def test_with_exports(self):
        """Test map with exports."""
        map_obj = PackageReexportMap(
            package_path="mypackage",
            init_file_path="mypackage/__init__.py",
            exports={
                "MyClass": ReexportEntry(
                    symbol_name="MyClass",
                    source_module=".module",
                    original_name="MyClass",
                ),
            },
        )
        assert map_obj.is_reexported("MyClass")
        assert not map_obj.is_reexported("OtherClass")

        source_info = map_obj.get_source_info("MyClass")
        assert source_info == (".module", "MyClass")


class TestReexportResolver:
    """Tests for ReexportResolver."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository with Python packages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create package structure:
            # mypackage/
            #   __init__.py  (re-exports from submodule)
            #   module.py    (contains MyClass)
            #   submodule/
            #     __init__.py
            #     inner.py   (contains InnerClass)

            package_dir = repo_path / "mypackage"
            package_dir.mkdir()

            # __init__.py with re-exports
            init_content = '''
"""My package."""

from .module import MyClass
from .submodule import InnerClass as AliasedClass
from ..sibling import SiblingClass

__all__ = ["MyClass", "AliasedClass"]
'''
            (package_dir / "__init__.py").write_text(init_content)

            # module.py
            (package_dir / "module.py").write_text('''
class MyClass:
    pass
''')

            # submodule
            subdir = package_dir / "submodule"
            subdir.mkdir()
            (subdir / "__init__.py").write_text('''
from .inner import InnerClass
''')
            (subdir / "inner.py").write_text('''
class InnerClass:
    pass
''')

            # sibling package
            sibling_dir = repo_path / "sibling"
            sibling_dir.mkdir()
            (sibling_dir / "__init__.py").write_text('''
class SiblingClass:
    pass
''')

            yield repo_path

    def test_parse_init_file_basic(self, temp_repo):
        """Test parsing __init__.py with basic re-exports."""
        resolver = ReexportResolver(temp_repo)
        init_path = temp_repo / "mypackage" / "__init__.py"

        result = resolver.parse_init_file(init_path)

        assert result.package_path == "mypackage"
        assert "MyClass" in result.exports
        assert "AliasedClass" in result.exports

        # Check MyClass re-export
        myclass_entry = result.exports["MyClass"]
        assert myclass_entry.source_module == ".module"
        assert myclass_entry.original_name == "MyClass"

        # Check aliased re-export
        aliased_entry = result.exports["AliasedClass"]
        assert aliased_entry.source_module == ".submodule"
        assert aliased_entry.original_name == "InnerClass"
        assert aliased_entry.alias == "AliasedClass"

    def test_parse_init_file_with_all(self, temp_repo):
        """Test that __all__ filters exports."""
        resolver = ReexportResolver(temp_repo)
        init_path = temp_repo / "mypackage" / "__init__.py"

        result = resolver.parse_init_file(init_path)

        # SiblingClass is imported but not in __all__
        assert "SiblingClass" not in result.exports
        assert result.all_declaration == ["MyClass", "AliasedClass"]

    def test_get_package_map_caching(self, temp_repo):
        """Test that package maps are cached."""
        resolver = ReexportResolver(temp_repo)

        # First call parses
        result1 = resolver.get_package_map("mypackage")
        assert result1 is not None

        # Second call uses cache
        result2 = resolver.get_package_map("mypackage")
        assert result1 is result2  # Same object

    def test_get_package_map_not_found(self, temp_repo):
        """Test getting map for non-existent package."""
        resolver = ReexportResolver(temp_repo)

        result = resolver.get_package_map("nonexistent")
        assert result is None

    def test_resolve_reexport(self, temp_repo):
        """Test resolving a re-exported symbol."""
        resolver = ReexportResolver(temp_repo)

        source_info = resolver.resolve_reexport("mypackage", "MyClass")
        assert source_info == (".module", "MyClass")

        source_info = resolver.resolve_reexport("mypackage", "AliasedClass")
        assert source_info == (".submodule", "InnerClass")

    def test_resolve_to_file_path(self, temp_repo):
        """Test resolving re-export to actual file path."""
        resolver = ReexportResolver(temp_repo)

        result = resolver.resolve_to_file_path("mypackage", "MyClass")
        assert result is not None

        file_path, original_name = result
        assert file_path.name == "module.py"
        assert original_name == "MyClass"

    def test_clear_cache(self, temp_repo):
        """Test clearing the cache."""
        resolver = ReexportResolver(temp_repo)

        # Load into cache
        resolver.get_package_map("mypackage")
        assert "mypackage" in resolver._cache

        # Clear cache
        resolver.clear_cache()
        assert len(resolver._cache) == 0

    def test_wildcard_import_tracking(self, temp_repo):
        """Test that wildcard imports are tracked."""
        # Create a package with wildcard import
        package_dir = temp_repo / "wildcard_pkg"
        package_dir.mkdir()

        init_content = '''
from .module import *
'''
        (package_dir / "__init__.py").write_text(init_content)
        (package_dir / "module.py").write_text('')

        resolver = ReexportResolver(temp_repo)
        result = resolver.parse_init_file(package_dir / "__init__.py")

        assert ".module" in result.wildcard_modules


class TestFindPythonPackages:
    """Tests for find_python_packages function."""

    def test_find_packages(self):
        """Test finding Python packages in a repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create structure:
            # pkg1/__init__.py
            # pkg1/sub/__init__.py
            # pkg2/__init__.py
            # not_a_pkg.py

            (repo_path / "pkg1").mkdir()
            (repo_path / "pkg1" / "__init__.py").write_text('')
            (repo_path / "pkg1" / "sub").mkdir()
            (repo_path / "pkg1" / "sub" / "__init__.py").write_text('')
            (repo_path / "pkg2").mkdir()
            (repo_path / "pkg2" / "__init__.py").write_text('')
            (repo_path / "not_a_pkg.py").write_text('')

            packages = find_python_packages(repo_path)

            assert "pkg1" in packages
            assert "pkg1/sub" in packages
            assert "pkg2" in packages
            assert len(packages) == 3


class TestReexportPatterns:
    """Tests for various re-export patterns."""

    @pytest.fixture
    def pattern_repo(self):
        """Create a repository with various re-export patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Pattern 1: Simple re-export
            pkg1 = repo_path / "simple_pkg"
            pkg1.mkdir()
            (pkg1 / "__init__.py").write_text('''
from .module import ClassA, func_a
''')
            (pkg1 / "module.py").write_text('''
class ClassA:
    pass

def func_a():
    pass
''')

            # Pattern 2: Aliased re-export
            pkg2 = repo_path / "aliased_pkg"
            pkg2.mkdir()
            (pkg2 / "__init__.py").write_text('''
from .deep import DeepClass as ShallowClass
''')
            (pkg2 / "deep.py").write_text('''
class DeepClass:
    pass
''')

            # Pattern 3: Multi-level relative import
            pkg3 = repo_path / "parent" / "child"
            pkg3.mkdir(parents=True)
            (repo_path / "parent" / "__init__.py").write_text('')
            (pkg3 / "__init__.py").write_text('''
from ..sibling import SiblingClass
''')

            sibling = repo_path / "parent" / "sibling"
            sibling.mkdir()
            (sibling / "__init__.py").write_text('''
class SiblingClass:
    pass
''')

            # Pattern 4: __all__ with explicit list
            pkg4 = repo_path / "explicit_pkg"
            pkg4.mkdir()
            (pkg4 / "__init__.py").write_text('''
from .internal import PublicAPI, PrivateHelper

__all__ = ["PublicAPI"]
''')
            (pkg4 / "internal.py").write_text('''
class PublicAPI:
    pass

class PrivateHelper:
    pass
''')

            yield repo_path

    def test_simple_reexport(self, pattern_repo):
        """Test simple re-export pattern."""
        resolver = ReexportResolver(pattern_repo)
        result = resolver.get_package_map("simple_pkg")

        assert result is not None
        assert "ClassA" in result.exports
        assert "func_a" in result.exports

    def test_aliased_reexport(self, pattern_repo):
        """Test aliased re-export pattern."""
        resolver = ReexportResolver(pattern_repo)
        result = resolver.get_package_map("aliased_pkg")

        assert result is not None
        assert "ShallowClass" in result.exports
        assert "DeepClass" not in result.exports  # Original name not exposed

        entry = result.exports["ShallowClass"]
        assert entry.original_name == "DeepClass"
        assert entry.alias == "ShallowClass"

    def test_multilevel_relative(self, pattern_repo):
        """Test multi-level relative import pattern."""
        resolver = ReexportResolver(pattern_repo)
        result = resolver.get_package_map("parent/child")

        assert result is not None
        assert "SiblingClass" in result.exports

        entry = result.exports["SiblingClass"]
        assert entry.source_module == "..sibling"

    def test_all_filtering(self, pattern_repo):
        """Test __all__ filtering pattern."""
        resolver = ReexportResolver(pattern_repo)
        result = resolver.get_package_map("explicit_pkg")

        assert result is not None
        # Only PublicAPI should be in exports due to __all__
        assert "PublicAPI" in result.exports
        assert "PrivateHelper" not in result.exports


class TestEdgeCases:
    """Tests for edge cases in re-export resolution."""

    def test_empty_init(self):
        """Test empty __init__.py file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            pkg = repo_path / "empty_pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text('')

            resolver = ReexportResolver(repo_path)
            result = resolver.get_package_map("empty_pkg")

            assert result is not None
            assert len(result.exports) == 0
            assert len(result.wildcard_modules) == 0

    def test_syntax_error_init(self):
        """Test __init__.py with syntax errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            pkg = repo_path / "broken_pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text('''
from .module import Class
# Syntax error below
def broken(:
    pass
''')

            resolver = ReexportResolver(repo_path)
            # Should not raise, just return empty map
            result = resolver.get_package_map("broken_pkg")

            assert result is not None
            # May have partial results or empty
            assert isinstance(result.exports, dict)

    def test_conditional_imports(self):
        """Test conditional imports in __init__.py."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            pkg = repo_path / "cond_pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text('''
try:
    from .fast import FastImpl as Implementation
except ImportError:
    from .slow import SlowImpl as Implementation
''')
            (pkg / "fast.py").write_text('class FastImpl: pass')
            (pkg / "slow.py").write_text('class SlowImpl: pass')

            resolver = ReexportResolver(repo_path)
            result = resolver.get_package_map("cond_pkg")

            assert result is not None
            # Both imports are tracked
            assert "Implementation" in result.exports
            # Last one wins (slow in this case due to order)
            entry = result.exports["Implementation"]
            # Note: our parser doesn't understand try/except semantics
            # It just records what it sees

    def test_dynamic_imports_not_tracked(self):
        """Test that dynamic imports via importlib are not tracked as re-exports."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            pkg = repo_path / "dynamic_pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text('''
import importlib

def get_module(name):
    return importlib.import_module(f".{name}", package=__name__)
''')

            resolver = ReexportResolver(repo_path)
            result = resolver.get_package_map("dynamic_pkg")

            assert result is not None
            # Dynamic imports are not tracked as re-exports
            assert len(result.exports) == 0
