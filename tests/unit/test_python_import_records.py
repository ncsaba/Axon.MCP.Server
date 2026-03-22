"""Unit tests for Python import record extraction."""

import pytest
from src.parsers.python_parser import PythonParser
from src.parsers.base_parser import ImportRecord, ImportedSymbol


class TestPythonImportRecords:
    """Test structured import record extraction from Python code."""

    @pytest.fixture
    def parser(self):
        return PythonParser()

    def test_basic_import(self, parser):
        """Test: import os"""
        code = "import os"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "os"
        assert record.alias is None
        assert record.level == 0
        assert record.symbols == []
        assert record.is_wildcard is False
        assert record.is_dynamic is False
        assert record.is_conditional is False
        
        # Legacy compatibility
        assert "os" in result.imports

    def test_aliased_import(self, parser):
        """Test: import numpy as np"""
        code = "import numpy as np"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "numpy"
        assert record.alias == "np"
        assert record.level == 0
        assert record.symbols == []
        
        # Legacy compatibility
        assert "numpy" in result.imports

    def test_multiple_imports(self, parser):
        """Test: import os, sys"""
        code = "import os, sys"
        result = parser.parse(code)
        
        assert len(result.import_records) == 2
        
        record1 = result.import_records[0]
        assert record1.module == "os"
        assert record1.alias is None
        
        record2 = result.import_records[1]
        assert record2.module == "sys"
        assert record2.alias is None

    def test_from_import_single(self, parser):
        """Test: from os import path"""
        code = "from os import path"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "os"
        assert record.level == 0
        assert len(record.symbols) == 1
        assert record.symbols[0].name == "path"
        assert record.symbols[0].alias is None
        assert record.is_wildcard is False

    def test_from_import_aliased(self, parser):
        """Test: from os import path as p"""
        code = "from os import path as p"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "os"
        assert len(record.symbols) == 1
        assert record.symbols[0].name == "path"
        assert record.symbols[0].alias == "p"

    def test_from_import_multiple(self, parser):
        """Test: from os import path, environ"""
        code = "from os import path, environ"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "os"
        assert len(record.symbols) == 2
        assert record.symbols[0].name == "path"
        assert record.symbols[1].name == "environ"

    def test_from_import_multiple_aliased(self, parser):
        """Test: from os import path as p, environ as e"""
        code = "from os import path as p, environ as e"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "os"
        assert len(record.symbols) == 2
        assert record.symbols[0].name == "path"
        assert record.symbols[0].alias == "p"
        assert record.symbols[1].name == "environ"
        assert record.symbols[1].alias == "e"

    def test_wildcard_import(self, parser):
        """Test: from os import *"""
        code = "from os import *"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "os"
        assert record.is_wildcard is True
        assert record.symbols == []

    def test_relative_import_level1(self, parser):
        """Test: from . import module"""
        code = "from . import module"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == ""
        assert record.level == 1
        assert len(record.symbols) == 1
        assert record.symbols[0].name == "module"

    def test_relative_import_level2(self, parser):
        """Test: from ..pkg import X"""
        code = "from ..pkg import X"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "pkg"
        assert record.level == 2
        assert len(record.symbols) == 1
        assert record.symbols[0].name == "X"

    def test_relative_import_with_module(self, parser):
        """Test: from .module import func"""
        code = "from .module import func"
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "module"
        assert record.level == 1
        assert len(record.symbols) == 1
        assert record.symbols[0].name == "func"

    def test_conditional_import_try_except(self, parser):
        """Test: try: import fastlib except ImportError: import slowlib"""
        code = """
try:
    import fastlib
except ImportError:
    import slowlib
"""
        result = parser.parse(code)
        
        assert len(result.import_records) == 2
        
        fastlib_record = next(r for r in result.import_records if r.module == "fastlib")
        slowlib_record = next(r for r in result.import_records if r.module == "slowlib")
        
        assert fastlib_record.is_conditional is True
        assert slowlib_record.is_conditional is True

    def test_conditional_import_bare_except(self, parser):
        """Test: try: import x except: pass"""
        code = """
try:
    import x
except:
    pass
"""
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "x"
        assert record.is_conditional is True

    def test_non_conditional_try_except(self, parser):
        """Test: try: import x except ValueError: pass (not ImportError)"""
        code = """
try:
    import x
except ValueError:
    pass
"""
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "x"
        assert record.is_conditional is False

    def test_dynamic_import_importlib(self, parser):
        """Test: importlib.import_module("plugins.plugin")"""
        code = 'module = importlib.import_module("plugins.plugin")'
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "plugins.plugin"
        assert record.is_dynamic is True
        
        # Legacy compatibility
        assert "dynamic:plugins.plugin" in result.imports

    def test_dynamic_import_dunder(self, parser):
        """Test: __import__("module")"""
        code = 'module = __import__("module")'
        result = parser.parse(code)
        
        assert len(result.import_records) == 1
        record = result.import_records[0]
        
        assert record.module == "module"
        assert record.is_dynamic is True

    def test_mixed_imports(self, parser):
        """Test multiple import types in one file."""
        code = """
import os
import numpy as np
from collections import defaultdict as dd
from . import local_module
from ..pkg import ClassA, ClassB
try:
    import fastlib
except ImportError:
    import slowlib as slib
module = importlib.import_module("dynamic.module")
"""
        result = parser.parse(code)
        
        # Should have 8 import records
        assert len(result.import_records) == 8
        
        # Check specific records
        os_record = next(r for r in result.import_records if r.module == "os" and not r.symbols)
        assert os_record.alias is None
        
        np_record = next(r for r in result.import_records if r.module == "numpy")
        assert np_record.alias == "np"
        
        dd_record = next(r for r in result.import_records if r.module == "collections")
        assert len(dd_record.symbols) == 1
        assert dd_record.symbols[0].name == "defaultdict"
        assert dd_record.symbols[0].alias == "dd"
        
        local_record = next(r for r in result.import_records if r.level == 1 and not r.module)
        assert local_record.symbols[0].name == "local_module"
        
        pkg_record = next(r for r in result.import_records if r.module == "pkg" and r.level == 2)
        assert len(pkg_record.symbols) == 2
        
        fastlib_record = next(r for r in result.import_records if r.module == "fastlib")
        assert fastlib_record.is_conditional is True
        
        slib_record = next(r for r in result.import_records if r.module == "slowlib")
        assert slib_record.alias == "slib"
        assert slib_record.is_conditional is True
        
        dynamic_record = next(r for r in result.import_records if r.is_dynamic)
        assert dynamic_record.module == "dynamic.module"

    def test_legacy_string_compatibility(self, parser):
        """Test that legacy imports list is still populated."""
        code = """
import os
import numpy as np
from collections import defaultdict
from . import local
"""
        result = parser.parse(code)
        
        # Legacy imports should still be populated
        assert "os" in result.imports
        assert "numpy" in result.imports
        assert "collections" in result.imports
        assert "collections.defaultdict" in result.imports
        assert ".local" in result.imports

    def test_import_record_to_legacy_string(self):
        """Test ImportRecord.to_legacy_string() method."""
        # Simple module
        record = ImportRecord(module="os")
        assert record.to_legacy_string() == "os"
        
        # With alias (returns module name)
        record = ImportRecord(module="numpy", alias="np")
        assert record.to_legacy_string() == "numpy"
        
        # From import with symbol
        record = ImportRecord(module="os", symbols=[ImportedSymbol(name="path")])
        assert record.to_legacy_string() == "os.path"
        
        # Relative import
        record = ImportRecord(module="pkg", level=2)
        assert record.to_legacy_string() == "..pkg"
        
        # Relative import no module
        record = ImportRecord(module="", level=1)
        assert record.to_legacy_string() == "."

    def test_line_numbers(self, parser):
        """Test that line numbers are captured correctly."""
        code = """
# Line 2
import os  # Line 3
# Line 4
from sys import path  # Line 5
"""
        result = parser.parse(code)
        
        assert len(result.import_records) == 2
        
        os_record = result.import_records[0]
        assert os_record.line_number == 3
        
        sys_record = result.import_records[1]
        assert sys_record.line_number == 5


class TestImportRecordEdgeCases:
    """Test edge cases in import record extraction."""

    @pytest.fixture
    def parser(self):
        return PythonParser()

    def test_nested_try_except(self, parser):
        """Test nested try/except with ImportError."""
        code = """
try:
    try:
        import inner
    except ImportError:
        import inner_fallback
except ImportError:
    import outer_fallback
"""
        result = parser.parse(code)
        
        # All imports should be marked conditional
        for record in result.import_records:
            assert record.is_conditional is True

    def test_try_except_with_tuple(self, parser):
        """Test try/except with tuple of exceptions."""
        code = """
try:
    import x
except (ImportError, ModuleNotFoundError):
    import y
"""
        result = parser.parse(code)
        
        assert len(result.import_records) == 2
        for record in result.import_records:
            assert record.is_conditional is True

    def test_dynamic_import_with_variable(self, parser):
        """Test dynamic import with variable (should not be captured)."""
        code = """
module_name = "some_module"
module = importlib.import_module(module_name)
"""
        result = parser.parse(code)
        
        # Should not capture dynamic import with non-literal argument
        # (Our implementation only captures string literals)
        dynamic_records = [r for r in result.import_records if r.is_dynamic]
        assert len(dynamic_records) == 0

    def test_syntax_error_handling(self, parser):
        """Test that syntax errors are handled gracefully."""
        code = "import os\nthis is not valid python\nimport sys"
        result = parser.parse(code)
        
        # Should have parse errors
        assert len(result.parse_errors) > 0
        # Should not have import records due to syntax error
        assert len(result.import_records) == 0

    def test_empty_file(self, parser):
        """Test parsing empty file."""
        code = ""
        result = parser.parse(code)
        
        assert len(result.import_records) == 0
        assert len(result.imports) == 0
        assert result.success is True

    def test_no_imports(self, parser):
        """Test file with no imports."""
        code = """
def hello():
    print("Hello, world!")

class MyClass:
    pass
"""
        result = parser.parse(code)
        
        assert len(result.import_records) == 0
        assert len(result.imports) == 0
