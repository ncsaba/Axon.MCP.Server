"""Cross-repository dependency and connection tools."""

from __future__ import annotations

from typing import List, Optional, Sequence, Union

from mcp.types import TextContent

from src.api.services.repository_connection_service import (
    RepositoryConnectionAnalysis,
    RepositoryConnectionEdge,
    RepositoryConnectionSubgraph,
    RepositoryConnectionService,
)
from src.database.session import get_async_session
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

RepositoryRef = Union[int, str]


def _format_edge(edge: RepositoryConnectionEdge) -> str:
    direction = f"{edge.source_repository_name} -> {edge.target_repository_name}"
    confidence = f"{edge.confidence:.2f}"
    evidence_lines = "\n".join(f"  - {sample}" for sample in edge.evidence_samples)
    note_line = f"\n  - Note: {edge.notes}" if edge.notes else ""
    return (
        f"- {direction} via `{edge.connection_type}` (confidence {confidence}, evidence {edge.evidence_count})\n"
        f"{evidence_lines}{note_line}"
    )


def _format_pairwise_analysis(analysis: RepositoryConnectionAnalysis) -> str:
    if not analysis.connected:
        return (
            f"# Repository Connections: {analysis.repo_a.name} <-> {analysis.repo_b.name}\n\n"
            "No grounded connection found between these repositories.\n\n"
            "Try broadening the evidence base with:\n"
            "- indexing/link refreshes for API and event links\n"
            "- `search_code` for shared service names or framework markers\n"
        )

    lines = [
        f"# Repository Connections: {analysis.repo_a.name} <-> {analysis.repo_b.name}\n\n",
        "Connected: yes\n\n",
    ]

    if analysis.direct_connections:
        lines.append("## Direct Connections\n\n")
        for edge in analysis.direct_connections:
            lines.append(f"{_format_edge(edge)}\n")
        lines.append("\n")

    if analysis.indirect_paths:
        lines.append("## Indirect Paths\n\n")
        for path in analysis.indirect_paths[:5]:
            lines.append(
                f"- {path.summary} (combined confidence {path.combined_confidence:.2f})\n"
            )
        lines.append("\n")

    lines.append("## Suggested Follow-up\n\n")
    lines.append("- `explain_repository_dependency` for a narrative summary\n")
    lines.append("- `get_repository_connection_subgraph` to widen to multiple repositories\n")
    lines.append("- `search_code` or `get_symbol_context` to inspect the linked symbols/files\n")
    return "".join(lines)


def _summarize_dependency_direction(analysis: RepositoryConnectionAnalysis) -> str:
    forward = [
        edge
        for edge in analysis.direct_connections
        if edge.source_repository_id == analysis.repo_a.id
        and edge.target_repository_id == analysis.repo_b.id
    ]
    reverse = [
        edge
        for edge in analysis.direct_connections
        if edge.source_repository_id == analysis.repo_b.id
        and edge.target_repository_id == analysis.repo_a.id
    ]

    if forward and reverse:
        return (
            f"{analysis.repo_a.name} and {analysis.repo_b.name} are bidirectionally coupled. "
            f"Each repository has grounded evidence pointing at the other."
        )
    if forward:
        return (
            f"{analysis.repo_a.name} depends on {analysis.repo_b.name}. "
            f"The strongest grounded evidence is outbound from {analysis.repo_a.name} into {analysis.repo_b.name}."
        )
    if reverse:
        return (
            f"{analysis.repo_b.name} depends on {analysis.repo_a.name}. "
            f"The strongest grounded evidence is outbound from {analysis.repo_b.name} into {analysis.repo_a.name}."
        )
    if analysis.indirect_paths:
        return (
            f"{analysis.repo_a.name} and {analysis.repo_b.name} are only indirectly connected in the current graph. "
            "The evidence goes through at least one intermediary repository."
        )
    return f"No grounded dependency could be explained for {analysis.repo_a.name} and {analysis.repo_b.name}."


def _format_dependency_explanation(analysis: RepositoryConnectionAnalysis) -> str:
    if not analysis.connected:
        return (
            f"# Dependency Explanation: {analysis.repo_a.name} vs {analysis.repo_b.name}\n\n"
            "No grounded dependency was found.\n\n"
            "This means the current indexed evidence does not show a direct or short indirect relationship.\n"
            "It does not prove the repositories are unrelated.\n"
        )

    lines = [
        f"# Dependency Explanation: {analysis.repo_a.name} vs {analysis.repo_b.name}\n\n",
        "## Directionality Summary\n\n",
        f"{_summarize_dependency_direction(analysis)}\n\n",
    ]

    grounded = [
        edge for edge in analysis.direct_connections if edge.status == "direct"
    ]
    heuristic = [
        edge for edge in analysis.direct_connections if edge.status != "direct"
    ]

    if grounded:
        lines.append("## Grounded Evidence\n\n")
        for edge in grounded:
            lines.append(f"{_format_edge(edge)}\n")
        lines.append("\n")

    if heuristic:
        lines.append("## Heuristic Evidence\n\n")
        for edge in heuristic:
            lines.append(f"{_format_edge(edge)}\n")
        lines.append("\n")

    if analysis.indirect_paths:
        lines.append("## Indirect Paths\n\n")
        for path in analysis.indirect_paths[:3]:
            lines.append(
                f"- {path.summary} (combined confidence {path.combined_confidence:.2f})\n"
            )
        lines.append("\n")

    lines.append("## Caveats\n\n")
    if heuristic and not grounded:
        lines.append(
            "- The current explanation is driven mostly by heuristic evidence, so it should be treated as directional guidance rather than proof.\n"
        )
    else:
        lines.append(
            "- The explanation is grounded in persisted links, but graph completeness still depends on indexing/link freshness.\n"
        )
    lines.append(
        "- If you need file-level proof, follow up with `find_repository_connections` and then inspect the referenced files or symbols.\n"
    )
    return "".join(lines)


def _format_connection_subgraph(subgraph: RepositoryConnectionSubgraph) -> str:
    repo_names = ", ".join(repo.name for repo in subgraph.repositories)
    lines = [
        "# Repository Connection Subgraph\n\n",
        f"Repositories in view: {repo_names}\n",
        f"Direct edges: {len(subgraph.direct_edges)}\n\n",
    ]

    if subgraph.hub_repositories:
        lines.append("## Hub Repositories\n\n")
        for repo_name in subgraph.hub_repositories:
            lines.append(f"- {repo_name}\n")
        lines.append("\n")

    if subgraph.direct_edges:
        lines.append("## Strongest Direct Connections\n\n")
        for edge in subgraph.direct_edges[:8]:
            lines.append(f"{_format_edge(edge)}\n")
        lines.append("\n")

    if subgraph.pair_summaries:
        lines.append("## Pairwise Summaries\n\n")
        for pair in subgraph.pair_summaries:
            state = "connected" if pair.connected else "not connected"
            lines.append(
                f"- {pair.repo_a.name} <-> {pair.repo_b.name}: {state}, "
                f"{len(pair.direct_connections)} direct edges, {len(pair.indirect_paths)} indirect paths\n"
            )
        lines.append("\n")

    if not subgraph.direct_edges and not subgraph.pair_summaries:
        lines.append("No grounded repository connections were found in this subgraph.\n")

    return "".join(lines)


async def find_repository_connections(
    repo_a: RepositoryRef,
    repo_b: RepositoryRef,
    max_depth: int = 2,
) -> List[TextContent]:
    """Return grounded connection evidence between two repositories."""
    try:
        async with get_async_session() as session:
            service = RepositoryConnectionService(session)
            analysis = await service.find_repository_connections(
                repo_a,
                repo_b,
                max_depth=max_depth,
            )
            return [TextContent(type="text", text=_format_pairwise_analysis(analysis))]
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp_find_repository_connections_failed", error=str(exc), exc_info=True)
        return [TextContent(type="text", text=f"Failed to find repository connections: {exc}")]


async def explain_repository_dependency(
    repo_a: RepositoryRef,
    repo_b: RepositoryRef,
    max_depth: int = 2,
) -> List[TextContent]:
    """Explain how one repository depends on another."""
    try:
        async with get_async_session() as session:
            service = RepositoryConnectionService(session)
            analysis = await service.find_repository_connections(
                repo_a,
                repo_b,
                max_depth=max_depth,
            )
            return [TextContent(type="text", text=_format_dependency_explanation(analysis))]
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp_explain_repository_dependency_failed", error=str(exc), exc_info=True)
        return [TextContent(type="text", text=f"Failed to explain repository dependency: {exc}")]


async def get_repository_connection_subgraph(
    repository_ids: Optional[Sequence[int]] = None,
    repository_names: Optional[Sequence[str]] = None,
    max_depth: int = 2,
) -> List[TextContent]:
    """Summarize connection structure across N repositories."""
    repo_refs: List[RepositoryRef] = []
    if repository_ids:
        repo_refs.extend(repository_ids)
    if repository_names:
        repo_refs.extend(repository_names)
    if not repo_refs:
        return [
            TextContent(
                type="text",
                text="At least one repository identifier is required via `repository_ids` or `repository_names`.",
            )
        ]

    try:
        async with get_async_session() as session:
            service = RepositoryConnectionService(session)
            subgraph = await service.get_repository_connection_subgraph(
                repo_refs,
                max_depth=max_depth,
            )
            return [TextContent(type="text", text=_format_connection_subgraph(subgraph))]
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp_get_repository_connection_subgraph_failed", error=str(exc), exc_info=True)
        return [TextContent(type="text", text=f"Failed to get repository connection subgraph: {exc}")]
