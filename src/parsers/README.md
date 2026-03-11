# Code Parsers

Multi-language parser framework based on Tree-sitter.

## Supported Languages

- Python (`.py`)
- Java (`.java`)
- JavaScript (`.js`, `.jsx`, `.mjs`)
- TypeScript (`.ts`, `.tsx`)
- Vue (`.vue`)
- Markdown (`.md`)
- SQL (`.sql`)

## Core Objects

`ParseResult` contains file-level parse output:

- `language`
- `file_path`
- `symbols`
- `imports`
- `exports`
- `parse_errors`
- `parse_duration_ms`

`ParsedSymbol` contains symbol-level output:

- `kind`
- `name`
- `start_line`, `end_line`
- `start_column`, `end_column`
- `signature`
- `documentation`
- `parameters`
- `return_type`
- `access_modifier`
- `parent_name`
- `fully_qualified_name`

## Extending Parsers

1. Install the Tree-sitter language package.
2. Add a parser class extending `TreeSitterParser`.
3. Implement symbol/import/export extraction methods.
4. Wire it in `ParserFactory`.
5. Add integration coverage.
