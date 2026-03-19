from src.mcp_server.formatters.search import format_search_results


def test_format_search_results_surfaces_cross_repo_follow_up_for_multi_repo_results():
    text = format_search_results(
        [
            {
                "repository": "frontend",
                "file": "src/App.tsx",
                "name": "App",
                "fully_qualified_name": "frontend.App",
                "kind": "CLASS",
                "lines": "1-20",
                "relevance_score": 0.91,
                "match_type": "semantic",
                "match_reason": "semantic via semantic",
                "documentation": "frontend entrypoint",
                "code_snippet": "export class App {}",
                "symbol_id": 1,
                "follow_up_tools": ["get_symbol_context"],
                "language": "typescript",
            },
            {
                "repository": "backend",
                "file": "src/api/users.py",
                "name": "UsersController",
                "fully_qualified_name": "backend.UsersController",
                "kind": "CLASS",
                "lines": "10-40",
                "relevance_score": 0.88,
                "match_type": "keyword",
                "match_reason": "keyword via text",
                "documentation": "handles users",
                "code_snippet": "class UsersController: ...",
                "symbol_id": 2,
                "follow_up_tools": ["get_symbol_context", "find_usages"],
                "language": "python",
            },
        ],
        query="user flow",
    )

    assert "Repositories represented: backend, frontend" in text
    assert "find_repository_connections(repo_a, repo_b)" in text
    assert "explain_repository_dependency(repo_a, repo_b)" in text


def test_format_search_results_surfaces_expanded_repository_scope():
    text = format_search_results(
        [
            {
                "repository": "dasc-prediction-management",
                "file": "src/main/java/.../PredictiveServiceClient.java",
                "name": "PredictiveServiceClient",
                "fully_qualified_name": "de.webtrekk.prediction.management.service.PredictiveServiceClient",
                "kind": "CLASS",
                "lines": "1-20",
                "relevance_score": 0.93,
                "match_type": "hybrid",
                "match_reason": "keyword via text",
                "documentation": "Calls predictive service.",
                "code_snippet": "class PredictiveServiceClient {}",
                "symbol_id": 5,
                "follow_up_tools": ["get_symbol_context"],
                "language": "java",
                "query_scope_group": "Inferred prediction stack",
                "query_scope_repositories": [
                    "dasc-prediction-management",
                    "dasc-prediction-domain",
                    "infrastructure-automation",
                ],
            }
        ],
        query="predictive service",
    )

    assert "Query scope expanded via Inferred prediction stack" in text
    assert "dasc-prediction-domain" in text
