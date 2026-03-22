import ast
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

from src.config.enums import LanguageEnum, SymbolKindEnum, AccessModifierEnum
from src.parsers.base_parser import (
    BaseParser,
    ParseResult,
    ParsedSymbol,
    ImportRecord,
    ImportedSymbol,
)


class PythonParser(BaseParser):
    """Python language parser using builtin AST module."""

    def __init__(self):
        self.language = LanguageEnum.PYTHON

    def get_language(self) -> LanguageEnum:
        return self.language

    def is_supported(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == '.py'

    def parse(self, code: str, file_path: Optional[str] = None) -> ParseResult:
        start_time = time.time()

        symbols: List[ParsedSymbol] = []
        imports: List[str] = []
        exports: List[str] = []
        errors: List[str] = []
        import_records: List[ImportRecord] = []

        try:
            tree = ast.parse(code)
            visitor = _PythonAstVisitor(code)
            visitor.visit(tree)
            symbols = visitor.symbols
            imports = visitor.imports
            exports = visitor.exports
            import_records = visitor.import_records
        except SyntaxError as e:
            errors.append(f"Python syntax error: {str(e)}")
        except Exception as e:
            errors.append(f"Python parsing error: {str(e)}")

        duration_ms = (time.time() - start_time) * 1000

        return ParseResult(
            language=self.language,
            file_path=file_path or "unknown",
            symbols=symbols,
            imports=imports,
            exports=exports,
            parse_errors=errors,
            parse_duration_ms=duration_ms,
            import_records=import_records,
        )


class _PythonAstVisitor(ast.NodeVisitor):
    def __init__(self, code: str):
        self.code = code
        self.lines = code.splitlines()
        self.symbols: List[ParsedSymbol] = []
        self.imports: List[str] = []  # Legacy flat strings
        self.import_records: List[ImportRecord] = []  # Structured records
        self.exports: List[str] = []
        self.class_stack: List[str] = []
        self.function_depth = 0
        self._in_try_import = False  # Track try/except ImportError context

    def visit_Try(self, node: ast.Try):
        """Track try/except ImportError blocks for conditional import marking."""
        # Check if any handler catches ImportError
        import_error_handlers = []
        for handler in node.handlers:
            if handler.type is None:
                # Bare except - could catch anything
                import_error_handlers.append(handler)
            elif isinstance(handler.type, ast.Name) and handler.type.id == 'ImportError':
                import_error_handlers.append(handler)
            elif isinstance(handler.type, ast.Tuple):
                # except (ImportError, ...)
                for elt in handler.type.elts:
                    if isinstance(elt, ast.Name) and elt.id == 'ImportError':
                        import_error_handlers.append(handler)
                        break

        if import_error_handlers:
            # Mark imports in try block as conditional
            old_in_try = self._in_try_import
            self._in_try_import = True
            for stmt in node.body:
                self.visit(stmt)
            self._in_try_import = old_in_try
            # Visit handlers and else/finally normally
            for handler in node.handlers:
                for stmt in handler.body:
                    self.visit(stmt)
            for stmt in node.orelse:
                self.visit(stmt)
            for stmt in node.finalbody:
                self.visit(stmt)
        else:
            # No ImportError handling, visit normally
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            # Legacy format
            self.imports.append(alias.name)
            
            # Structured record
            record = ImportRecord(
                module=alias.name,
                alias=alias.asname,
                line_number=node.lineno,
                is_conditional=self._in_try_import,
            )
            self.import_records.append(record)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ''
        level = node.level
        
        # Legacy format
        legacy_module = ('.' * level) + module
        if legacy_module:
            self.imports.append(legacy_module)
        
        # Check for wildcard import
        is_wildcard = any(alias.name == '*' for alias in node.names)
        
        if is_wildcard:
            # Wildcard import
            record = ImportRecord(
                module=module,
                level=level,
                is_wildcard=True,
                line_number=node.lineno,
                is_conditional=self._in_try_import,
            )
            self.import_records.append(record)
            
            # Legacy: add module.*
            if legacy_module:
                self.imports.append(f"{legacy_module}.*")
        else:
            # Named symbol imports
            imported_symbols = []
            for alias in node.names:
                # Legacy format
                if module:
                    self.imports.append(f"{legacy_module}.{alias.name}")
                else:
                    self.imports.append(alias.name)
                
                # Structured symbol
                imported_symbols.append(ImportedSymbol(
                    name=alias.name,
                    alias=alias.asname,
                ))
            
            # Create single ImportRecord for all symbols in this from-import
            record = ImportRecord(
                module=module,
                level=level,
                symbols=imported_symbols,
                line_number=node.lineno,
                is_conditional=self._in_try_import,
            )
            self.import_records.append(record)
        
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """Detect dynamic imports via importlib.import_module() or __import__()."""
        dynamic_module = None
        
        # Check for importlib.import_module("module")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == 'import_module':
                # Could be importlib.import_module or some_var.import_module
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'importlib':
                    if node.args and isinstance(node.args[0], ast.Constant):
                        dynamic_module = node.args[0].value
        
        # Check for __import__("module")
        elif isinstance(node.func, ast.Name) and node.func.id == '__import__':
            if node.args and isinstance(node.args[0], ast.Constant):
                dynamic_module = node.args[0].value
        
        if dynamic_module and isinstance(dynamic_module, str):
            # Create dynamic import record
            record = ImportRecord(
                module=dynamic_module,
                is_dynamic=True,
                line_number=node.lineno,
                is_conditional=self._in_try_import,
            )
            self.import_records.append(record)
            # Also add to legacy for backward compatibility
            self.imports.append(f"dynamic:{dynamic_module}")
        
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        parent_name = self.class_stack[-1] if self.class_stack else None
        class_name = node.name
        fully_qualified_name = '.'.join(self.class_stack + [class_name]) if self.class_stack else class_name

        bases = [self._safe_unparse(base) for base in node.bases]
        decorators = self._extract_decorators(node)
        signature = f"class {class_name}"
        if bases:
            signature += f"({', '.join(bases)})"

        self.symbols.append(
            ParsedSymbol(
                kind=SymbolKindEnum.CLASS,
                name=class_name,
                start_line=node.lineno,
                end_line=getattr(node, 'end_lineno', node.lineno),
                start_column=getattr(node, 'col_offset', 0),
                end_column=self._end_column(node),
                signature=signature,
                documentation=ast.get_docstring(node),
                structured_docs={
                    "bases": [base for base in bases if base],
                    "decorators": decorators,
                    "chunk_start_line": self._chunk_start_line(node),
                },
                access_modifier=self._access_modifier_for_name(class_name),
                parent_name=parent_name,
                fully_qualified_name=fully_qualified_name,
            )
        )

        self.class_stack.append(class_name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._record_function(node, is_async=False)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._record_function(node, is_async=True)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_Assign(self, node: ast.Assign):
        # Export extraction via __all__ = [...]
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == '__all__':
                extracted = self._extract_all_exports(node.value)
                if extracted:
                    self.exports.extend(extracted)

        # Module-level constants/variables
        if not self.class_stack and self._is_module_level(node):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._record_module_symbol(target.id, node)

        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        # Handle module-level annotated assignments (e.g., FOO: int = 1)
        if self._is_module_level(node) and isinstance(node.target, ast.Name):
            self._record_module_symbol(node.target.id, node)

        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        # Export extraction via __all__ += [...]
        if isinstance(node.target, ast.Name) and node.target.id == '__all__':
            extracted = self._extract_all_exports(node.value)
            if extracted:
                self.exports.extend(extracted)

        self.generic_visit(node)

    def _record_function(self, node: ast.AST, is_async: bool):
        parent_name = self.class_stack[-1] if self.class_stack else None
        func_name = node.name  # type: ignore[attr-defined]
        fully_qualified_name = '.'.join(self.class_stack + [func_name]) if self.class_stack else func_name

        args = self._extract_parameters(node)
        return_type = self._safe_unparse(getattr(node, 'returns', None))
        decorators = self._extract_decorators(node)

        prefix = 'async def' if is_async else 'def'
        signature = f"{prefix} {func_name}({', '.join(p['name'] for p in args)})"
        if return_type:
            signature += f" -> {return_type}"

        self.symbols.append(
            ParsedSymbol(
                kind=SymbolKindEnum.METHOD if self.class_stack else SymbolKindEnum.FUNCTION,
                name=func_name,
                start_line=node.lineno,  # type: ignore[attr-defined]
                end_line=getattr(node, 'end_lineno', node.lineno),  # type: ignore[attr-defined]
                start_column=getattr(node, 'col_offset', 0),
                end_column=self._end_column(node),
                signature=signature,
                documentation=ast.get_docstring(node),
                structured_docs={
                    "decorators": decorators,
                    "chunk_start_line": self._chunk_start_line(node),
                    "is_async": is_async,
                },
                parameters=args,
                return_type=return_type,
                access_modifier=self._access_modifier_for_name(func_name),
                parent_name=parent_name,
                fully_qualified_name=fully_qualified_name,
            )
        )

    def _record_module_symbol(self, var_name: str, node: ast.AST):
        self.symbols.append(
            ParsedSymbol(
                kind=SymbolKindEnum.CONSTANT if var_name.isupper() else SymbolKindEnum.VARIABLE,
                name=var_name,
                start_line=getattr(node, 'lineno', 1),
                end_line=getattr(node, 'end_lineno', getattr(node, 'lineno', 1)),
                start_column=getattr(node, 'col_offset', 0),
                end_column=self._end_column(node),
                signature=f"{var_name} = ...",
                documentation=None,
                access_modifier=self._access_modifier_for_name(var_name),
                fully_qualified_name=var_name,
            )
        )

    def _extract_parameters(self, node: ast.AST) -> List[Dict[str, Any]]:
        params: List[Dict[str, Any]] = []
        args_obj = getattr(node, 'args', None)
        if not args_obj:
            return params

        arg_nodes = list(args_obj.posonlyargs) + list(args_obj.args) + list(args_obj.kwonlyargs)
        for arg in arg_nodes:
            params.append(
                {
                    'name': arg.arg,
                    'type': self._safe_unparse(arg.annotation) if arg.annotation else None,
                }
            )

        if args_obj.vararg:
            params.append({'name': f"*{args_obj.vararg.arg}", 'type': self._safe_unparse(args_obj.vararg.annotation) if args_obj.vararg.annotation else None})
        if args_obj.kwarg:
            params.append({'name': f"**{args_obj.kwarg.arg}", 'type': self._safe_unparse(args_obj.kwarg.annotation) if args_obj.kwarg.annotation else None})

        return params

    def _extract_all_exports(self, value: ast.AST) -> List[str]:
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            exports = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    exports.append(elt.value)
            return exports
        return []

    def _safe_unparse(self, node: Optional[ast.AST]) -> Optional[str]:
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:
            return None

    def _access_modifier_for_name(self, name: str) -> AccessModifierEnum:
        return AccessModifierEnum.PRIVATE if name.startswith('_') else AccessModifierEnum.PUBLIC

    def _end_column(self, node: ast.AST) -> int:
        end_col = getattr(node, 'end_col_offset', None)
        if isinstance(end_col, int):
            return end_col

        lineno = getattr(node, 'lineno', 1)
        if 1 <= lineno <= len(self.lines):
            return len(self.lines[lineno - 1])
        return 0

    def _is_module_level(self, node: ast.AST) -> bool:
        # Module-level symbols are represented by top-level body nodes.
        # Class stack is enough for our use case because we only track class nesting.
        return not self.class_stack and self.function_depth == 0

    def _extract_decorators(self, node: ast.AST) -> List[str]:
        decorators: List[str] = []
        for decorator in getattr(node, "decorator_list", []):
            value = self._safe_unparse(decorator)
            if value:
                decorators.append(value)
        return decorators

    def _chunk_start_line(self, node: ast.AST) -> int:
        decorator_list = getattr(node, "decorator_list", [])
        if decorator_list:
            decorator_lines = [getattr(decorator, "lineno", getattr(node, "lineno", 1)) for decorator in decorator_list]
            return min(decorator_lines)
        return getattr(node, "lineno", 1)
