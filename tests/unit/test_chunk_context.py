from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.enums import LanguageEnum
from src.embeddings.chunk_context import ChunkContextBuilder


def _empty_relation_result() -> MagicMock:
    result = MagicMock()
    result.all.return_value = []
    return result


@pytest.mark.asyncio
async def test_build_context_populates_python_imports_and_module_namespace() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_empty_relation_result())

    builder = ChunkContextBuilder(session)
    symbol = SimpleNamespace(
        fully_qualified_name=None,
        parent_name=None,
        access_modifier=None,
        complexity_score=None,
        name="run",
        id=7,
    )
    file = SimpleNamespace(
        id=1,
        path="pkg/service/module.py",
        language=LanguageEnum.PYTHON,
        repository_id=10,
    )

    context = await builder.build_context(
        symbol,
        file,
        file_content="import os\nfrom typing import List\n\ndef run():\n    return 1\n",
    )

    assert context.namespace == "pkg.service.module"
    assert context.imports == ["os", "typing", "typing.List"]


@pytest.mark.asyncio
async def test_build_context_populates_java_imports_and_package_namespace() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_empty_relation_result())

    builder = ChunkContextBuilder(session)
    symbol = SimpleNamespace(
        fully_qualified_name="Foo",
        parent_name=None,
        access_modifier=None,
        complexity_score=None,
        name="Foo",
        id=9,
    )
    file = SimpleNamespace(
        id=2,
        path="src/main/java/com/example/Foo.java",
        language=LanguageEnum.JAVA,
        repository_id=11,
    )

    context = await builder.build_context(
        symbol,
        file,
        file_content=(
            "package com.example;\n"
            "import java.util.List;\n"
            "import java.util.Map;\n"
            "public class Foo {}\n"
        ),
    )

    assert context.namespace == "com.example"
    assert context.imports == ["java.util.List", "java.util.Map"]
