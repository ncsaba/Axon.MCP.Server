"""Call graph analyzers for supported languages."""

from dataclasses import dataclass
from typing import List, Optional, Protocol

import tree_sitter

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Call:
    """Represents a function/method call."""

    method_name: str
    receiver: Optional[str] = None
    arguments: List[str] = None
    line_number: int = 0
    end_line: int = 0
    start_column: int = 0
    end_column: int = 0
    is_async: bool = False
    is_static: bool = False

    def __post_init__(self):
        if self.arguments is None:
            self.arguments = []


class LanguageCallAnalyzer(Protocol):
    """Interface for language-specific call analyzers."""

    def extract_calls(self, symbol_node: tree_sitter.Node, code: str) -> List[Call]:
        ...

    def extract_usages(self, symbol_node: tree_sitter.Node, code: str) -> List[Call]:
        ...


class JavaScriptCallAnalyzer:
    """Analyzes JavaScript/TypeScript code to extract function calls."""

    def extract_calls(self, function_node: tree_sitter.Node, code: str) -> List[Call]:
        calls: List[Call] = []

        def traverse(node: tree_sitter.Node):
            if node.type == "call_expression":
                call = self._parse_call_expression(node, code)
                if call:
                    calls.append(call)
            elif node.type == "new_expression":
                call = self._parse_new_expression(node, code)
                if call:
                    calls.append(call)

            for child in node.children:
                traverse(child)

        body = self._find_function_body(function_node)
        if body:
            traverse(body)

        return calls

    def extract_usages(self, symbol_node: tree_sitter.Node, code: str) -> List[Call]:
        # JS/TS usage extraction is not implemented yet.
        return []

    def _find_function_body(self, function_node: tree_sitter.Node) -> Optional[tree_sitter.Node]:
        for child in function_node.children:
            if child.type in ["statement_block", "expression"]:
                return child
        return None

    def _parse_new_expression(self, node: tree_sitter.Node, code: str) -> Optional[Call]:
        try:
            constructor_node = None
            for child in node.children:
                if child.type == "identifier":
                    constructor_node = child
                    break

            if not constructor_node:
                return None

            class_name = self._get_node_text(constructor_node, code)
            return Call(
                method_name=class_name,
                receiver=None,
                line_number=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_column=node.start_point[1] + 1,
                end_column=node.end_point[1] + 1,
                is_async=False,
                is_static=True,
            )
        except Exception as e:
            logger.debug(f"Failed to parse new expression: {str(e)}")
            return None

    def _parse_call_expression(self, node: tree_sitter.Node, code: str) -> Optional[Call]:
        try:
            method_name = None
            receiver = None
            is_async = False

            parent = node.parent
            if parent and parent.type == "await_expression":
                is_async = True

            for child in node.children:
                if child.type == "member_expression":
                    parts = self._parse_member_expression(child, code)
                    if parts and len(parts) >= 2:
                        receiver = ".".join(parts[:-1])
                        method_name = parts[-1]
                elif child.type == "identifier":
                    method_name = self._get_node_text(child, code)

            if not method_name:
                return None

            return Call(
                method_name=method_name,
                receiver=receiver,
                line_number=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_column=node.start_point[1] + 1,
                end_column=node.end_point[1] + 1,
                is_async=is_async,
            )
        except Exception as e:
            logger.debug(f"Failed to parse call expression: {str(e)}")
            return None

    def _parse_member_expression(self, node: tree_sitter.Node, code: str) -> List[str]:
        parts: List[str] = []

        def extract_parts(n: tree_sitter.Node):
            if n.type == "member_expression":
                for child in n.children:
                    if child.type != ".":
                        extract_parts(child)
            elif n.type in ["identifier", "property_identifier"]:
                parts.append(self._get_node_text(n, code))
            elif n.type == "this":
                parts.append("this")

        extract_parts(node)
        return parts

    def _get_node_text(self, node: Optional[tree_sitter.Node], code: str) -> str:
        if not node:
            return ""
        code_bytes = code.encode("utf-8")
        return code_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


class JavaCallAnalyzer:
    """Analyzes Java code to extract method invocations."""

    def extract_calls(self, symbol_node: tree_sitter.Node, code: str) -> List[Call]:
        calls: List[Call] = []

        def traverse(node: tree_sitter.Node):
            if node.type == "method_invocation":
                call = self._parse_method_invocation(node, code)
                if call:
                    calls.append(call)

            for child in node.children:
                traverse(child)

        body = self._find_method_body(symbol_node)
        if body:
            traverse(body)

        return calls

    def extract_usages(self, symbol_node: tree_sitter.Node, code: str) -> List[Call]:
        # Java usage extraction is not implemented in this slice.
        return []

    def _find_method_body(self, symbol_node: tree_sitter.Node) -> Optional[tree_sitter.Node]:
        for child in symbol_node.children:
            if child.type == "block":
                return child
        return None

    def _parse_method_invocation(self, node: tree_sitter.Node, code: str) -> Optional[Call]:
        text = self._get_node_text(node, code).strip()
        if not text:
            return None

        call_expr = text.split("(", 1)[0].strip()
        if not call_expr:
            return None

        receiver: Optional[str] = None
        method_name = call_expr
        if "." in call_expr:
            receiver, method_name = call_expr.rsplit(".", 1)
            receiver = receiver.strip() or None
        method_name = method_name.strip()
        if not method_name:
            return None

        return Call(
            method_name=method_name,
            receiver=receiver,
            line_number=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_column=node.start_point[1] + 1,
            end_column=node.end_point[1] + 1,
            is_async=False,
            is_static=bool(receiver and receiver[:1].isupper()),
        )

    def _get_node_text(self, node: Optional[tree_sitter.Node], code: str) -> str:
        if not node:
            return ""
        code_bytes = code.encode("utf-8")
        return code_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
