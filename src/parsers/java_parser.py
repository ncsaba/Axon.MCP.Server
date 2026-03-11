import tree_sitter
import tree_sitter_java
from pathlib import Path
from typing import List, Optional

from src.config.enums import LanguageEnum, SymbolKindEnum
from src.parsers.base_parser import ParsedSymbol
from src.parsers.tree_sitter_parser import TreeSitterParser


class JavaParser(TreeSitterParser):
    """Java language parser using Tree-sitter."""

    def __init__(self):
        super().__init__(LanguageEnum.JAVA, tree_sitter_java)

    def is_supported(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".java"

    def _extract_symbols(self, node: tree_sitter.Node, code: str) -> List[ParsedSymbol]:
        symbols: List[ParsedSymbol] = []

        def walk(n: tree_sitter.Node, parent_name: Optional[str] = None) -> None:
            if n.type in {"class_declaration", "interface_declaration", "enum_declaration"}:
                symbol = self._parse_type_symbol(n, code, parent_name)
                if symbol:
                    symbols.append(symbol)
                    parent_name = symbol.fully_qualified_name
            elif n.type in {"method_declaration", "constructor_declaration"}:
                symbol = self._parse_method_symbol(n, code, parent_name)
                if symbol:
                    symbols.append(symbol)
            elif n.type == "field_declaration":
                symbols.extend(self._parse_field_symbols(n, code, parent_name))

            for child in n.children:
                walk(child, parent_name)

        walk(node)
        return symbols

    def _extract_imports(self, node: tree_sitter.Node, code: str) -> List[str]:
        imports: List[str] = []

        def walk(n: tree_sitter.Node) -> None:
            if n.type == "import_declaration":
                scoped = self._find_child_by_type(n, "scoped_identifier")
                ident = self._find_child_by_type(n, "identifier")
                import_name = self._get_node_text(scoped or ident, code)
                if import_name:
                    imports.append(import_name)
            for child in n.children:
                walk(child)

        walk(node)
        return imports

    def _parse_type_symbol(
        self, node: tree_sitter.Node, code: str, parent_name: Optional[str]
    ) -> Optional[ParsedSymbol]:
        name_node = self._find_child_by_type(node, "identifier")
        if not name_node:
            return None
        name = self._get_node_text(name_node, code)

        kind = SymbolKindEnum.CLASS
        if node.type == "interface_declaration":
            kind = SymbolKindEnum.INTERFACE
        elif node.type == "enum_declaration":
            kind = SymbolKindEnum.ENUM

        return ParsedSymbol(
            kind=kind,
            name=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_column=node.start_point[1] + 1,
            end_column=node.end_point[1] + 1,
            signature=self._signature_for_node(node, code),
            documentation=self._find_documentation(node, code),
            parent_name=parent_name,
            fully_qualified_name=f"{parent_name}.{name}" if parent_name else name,
        )

    def _parse_method_symbol(
        self, node: tree_sitter.Node, code: str, parent_name: Optional[str]
    ) -> Optional[ParsedSymbol]:
        name_node = self._find_child_by_type(node, "identifier")
        if not name_node:
            return None
        name = self._get_node_text(name_node, code)

        kind = SymbolKindEnum.METHOD if node.type == "method_declaration" else SymbolKindEnum.FUNCTION
        return ParsedSymbol(
            kind=kind,
            name=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_column=node.start_point[1] + 1,
            end_column=node.end_point[1] + 1,
            signature=self._signature_for_node(node, code),
            documentation=self._find_documentation(node, code),
            parent_name=parent_name,
            fully_qualified_name=f"{parent_name}.{name}" if parent_name else name,
        )

    def _parse_field_symbols(
        self, node: tree_sitter.Node, code: str, parent_name: Optional[str]
    ) -> List[ParsedSymbol]:
        symbols: List[ParsedSymbol] = []
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = self._find_child_by_type(child, "identifier")
            if not name_node:
                continue
            name = self._get_node_text(name_node, code)
            symbols.append(
                ParsedSymbol(
                    kind=SymbolKindEnum.VARIABLE,
                    name=name,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    start_column=child.start_point[1] + 1,
                    end_column=child.end_point[1] + 1,
                    signature=self._signature_for_node(child, code),
                    documentation=self._find_documentation(node, code),
                    parent_name=parent_name,
                    fully_qualified_name=f"{parent_name}.{name}" if parent_name else name,
                )
            )
        return symbols

    def _signature_for_node(self, node: tree_sitter.Node, code: str) -> str:
        text = self._get_node_text(node, code).strip()
        return text.split("{", 1)[0].strip()[:300]
