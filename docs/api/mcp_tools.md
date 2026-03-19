# MCP Tools

## Discovery and Search

- `search_code`: semantic and symbol search
- `search_by_path`: path-aware symbol/file lookup
- `search_documentation`: markdown/docs search
- `search_configuration`: configuration key/value search

## Symbol and Graph Navigation

- `get_symbol_context`
- `get_function_details`
- `get_class_details`
- `find_usages`
- `find_references`
- `find_implementations`
- `get_call_hierarchy`
- `find_callers`
- `find_callees`

## Repository and File Tools

- `list_repositories`
- `get_file_tree`
- `get_file_content`
- `list_symbols_in_file`
- `list_dependencies`
- `find_repository_connections`
- `explain_repository_dependency`
- `get_repository_connection_subgraph`

## Architecture and Context

- `find_api_endpoints`
- `analyze_architecture`
  - when `infrastructure-automation` is indexed, Axon also queries it for supporting deployment/runtime context
- `trace_request_flow`
- `get_project_map`
  - appends matching infrastructure-automation artifacts when they help explain topology
- `get_module_summary`
- `query_codebase_structure`
  - includes infrastructure-automation matches as supporting architecture context
- `list_services`
- `get_service_details`
- `get_service_documentation`
- `get_system_map`
