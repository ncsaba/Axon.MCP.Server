from typing import Dict, List

def format_search_results(results: List[Dict], query: str) -> str:
    """Format search results for ChatGPT display."""
    if not results:
        return f"No results found for query: '{query}'"

    # Group results by repository and file
    grouped_results: Dict[str, Dict[str, List[Dict]]] = {}
    
    for result in results:
        repo = result.get('repository', 'unknown')
        file_path = result.get('file', 'unknown')
        
        if repo not in grouped_results:
            grouped_results[repo] = {}
        if file_path not in grouped_results[repo]:
            grouped_results[repo][file_path] = []
            
        grouped_results[repo][file_path].append(result)

    formatted = [f"Found {len(results)} results for '{query}':\n"]
    query_scope_group = next((result.get("query_scope_group") for result in results if result.get("query_scope_group")), None)
    query_scope_repositories = next(
        (result.get("query_scope_repositories") for result in results if result.get("query_scope_repositories")),
        None,
    )
    if query_scope_group and query_scope_repositories:
        formatted.append(
            f"Query scope expanded via {query_scope_group}: {', '.join(query_scope_repositories)}\n"
        )
    if len(grouped_results) > 1:
        repo_names = ", ".join(sorted(grouped_results.keys()))
        formatted.append(f"Repositories represented: {repo_names}\n")
        formatted.append(
            "Cross-repo follow-up: use `find_repository_connections(repo_a, repo_b)` or "
            "`explain_repository_dependency(repo_a, repo_b)` if you are comparing systems.\n"
        )

    for repo, files in grouped_results.items():
        formatted.append(f"\n📁 **Repository: {repo}**\n")
        
        for file_path, file_results in files.items():
            formatted.append(f"\n  📄 **{file_path}**\n")
            
            for result in file_results:
                # Highlight query terms in name
                name = result.get('fully_qualified_name') or result['name']
                if query.lower() in name.lower():
                    # Simple case-insensitive highlight
                    start_idx = name.lower().find(query.lower())
                    if start_idx != -1:
                        end_idx = start_idx + len(query)
                        name = f"{name[:start_idx]}**{name[start_idx:end_idx]}**{name[end_idx:]}"
                
                # Format code preview
                code_preview = ""
                if result.get('code_snippet'):
                    # Use a larger limit or line-based limit
                    snippet = result['code_snippet']
                    lines = snippet.split('\n')
                    if len(lines) > 10:
                        snippet = '\n'.join(lines[:10]) + "\n..."
                    elif len(snippet) > 1000:
                        snippet = snippet[:1000] + "..."
                        
                    code_preview = f"\n   ```{result.get('language', '')}\n   {snippet}\n   ```\n"

                formatted.append(
                    f"   • **{name}** ({result['kind']})\n"
                    f"     Lines: {result['lines']} | Score: {result['relevance_score']} | Match: {result.get('match_reason') or result.get('match_type')}\n"
                    f"     {code_preview}"
                    f"     _Documentation_: {result.get('documentation', 'N/A')}\n"
                    f"     🔗 ID: {result['symbol_id']}\n"
                    f"     Next: {', '.join(result.get('follow_up_tools') or ['get_symbol_context'])}\n"
                )

    return "".join(formatted)
