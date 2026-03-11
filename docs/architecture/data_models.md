# Data Models

## Core Entities

- `repositories`: repository metadata and sync lifecycle state
- `files`: indexed files with language and content metadata
- `symbols`: extracted symbols (class/function/method/etc.)
- `relations`: graph edges between symbols (`CALLS`, `INHERITS`, `IMPLEMENTS`, `USES`, ...)
- `chunks`: code/document chunks for retrieval and enrichment
- `embeddings`: vector embeddings stored in pgvector
- `dependencies`: package dependencies (e.g. npm, pip)
- `services`: detected service/module boundaries for architecture context

## Typical Query Shapes

- Find symbol by fully qualified name
- Get call hierarchy around a symbol
- List repository/file symbol inventories
- Search documentation/config entries
- List dependency records by repository
