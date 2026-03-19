from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.api.services.repository_connection_service import (
    RepositoryConnectionAnalysis,
    RepositoryConnectionEdge,
    RepositoryConnectionPath,
    RepositoryConnectionSubgraph,
    RepositoryIdentity,
)
from src.mcp_server.tools.architecture import analyze_architecture
from src.mcp_server.tools.repository_connections import (
    explain_repository_dependency,
    find_repository_connections,
    get_repository_connection_subgraph,
)


def _mock_session_cm(session):
    manager = AsyncMock()
    manager.__aenter__.return_value = session
    manager.__aexit__.return_value = None
    return manager


@pytest.mark.asyncio
async def test_find_repository_connections_formats_grounded_answer():
    analysis = RepositoryConnectionAnalysis(
        repo_a=RepositoryIdentity(id=1, name="frontend", path_with_namespace="team/frontend"),
        repo_b=RepositoryIdentity(id=2, name="backend", path_with_namespace="team/backend"),
        connected=True,
        direct_connections=[
            RepositoryConnectionEdge(
                source_repository_id=1,
                source_repository_name="frontend",
                target_repository_id=2,
                target_repository_name="backend",
                connection_type="api_call",
                direction="outbound",
                confidence=0.93,
                evidence_count=2,
                evidence_samples=["GET /api/users -> UsersController"],
                notes="Direct runtime HTTP/API coupling",
            )
        ],
        indirect_paths=[
            RepositoryConnectionPath(
                repository_ids=[1, 3, 2],
                repository_names=["frontend", "shared-auth", "backend"],
                edge_types=["manifest_dependency", "event_flow"],
                combined_confidence=0.82,
                summary="frontend (manifest_dependency) -> shared-auth (event_flow) -> backend",
            )
        ],
    )
    session = AsyncMock()

    with patch("src.mcp_server.tools.repository_connections.get_async_session", return_value=_mock_session_cm(session)), patch(
        "src.mcp_server.tools.repository_connections.RepositoryConnectionService"
    ) as service_cls:
        service_cls.return_value.find_repository_connections = AsyncMock(return_value=analysis)
        result = await find_repository_connections("frontend", "backend")

    text = result[0].text
    assert "Connected: yes" in text
    assert "frontend -> backend via `api_call`" in text
    assert "GET /api/users -> UsersController" in text
    assert "Indirect Paths" in text
    assert "explain_repository_dependency" in text


@pytest.mark.asyncio
async def test_explain_repository_dependency_formats_directionality_for_support_readers():
    analysis = RepositoryConnectionAnalysis(
        repo_a=RepositoryIdentity(id=1, name="frontend", path_with_namespace="team/frontend"),
        repo_b=RepositoryIdentity(id=2, name="backend", path_with_namespace="team/backend"),
        connected=True,
        direct_connections=[
            RepositoryConnectionEdge(
                source_repository_id=1,
                source_repository_name="frontend",
                target_repository_id=2,
                target_repository_name="backend",
                connection_type="api_call",
                direction="outbound",
                confidence=0.91,
                evidence_count=1,
                evidence_samples=["POST /api/orders -> OrderController"],
                notes="Direct runtime HTTP/API coupling",
            ),
            RepositoryConnectionEdge(
                source_repository_id=1,
                source_repository_name="frontend",
                target_repository_id=2,
                target_repository_name="backend",
                connection_type="service_mapping",
                direction="outbound",
                confidence=0.61,
                evidence_count=1,
                evidence_samples=["service 'orders-api' mapped from docker-compose.yml"],
                status="heuristic",
                notes="Runtime/service-name topology hint",
            ),
        ],
        indirect_paths=[],
    )
    session = AsyncMock()

    with patch("src.mcp_server.tools.repository_connections.get_async_session", return_value=_mock_session_cm(session)), patch(
        "src.mcp_server.tools.repository_connections.RepositoryConnectionService"
    ) as service_cls:
        service_cls.return_value.find_repository_connections = AsyncMock(return_value=analysis)
        result = await explain_repository_dependency("frontend", "backend")

    text = result[0].text
    assert "frontend depends on backend" in text
    assert "Grounded Evidence" in text
    assert "Heuristic Evidence" in text
    assert "POST /api/orders -> OrderController" in text
    assert "service 'orders-api' mapped from docker-compose.yml" in text
    assert "Caveats" in text


@pytest.mark.asyncio
async def test_get_repository_connection_subgraph_summarizes_multiple_repositories():
    subgraph = RepositoryConnectionSubgraph(
        repositories=[
            RepositoryIdentity(id=1, name="frontend", path_with_namespace="team/frontend"),
            RepositoryIdentity(id=2, name="backend", path_with_namespace="team/backend"),
            RepositoryIdentity(id=3, name="shared-auth", path_with_namespace="team/shared-auth"),
        ],
        direct_edges=[
            RepositoryConnectionEdge(
                source_repository_id=1,
                source_repository_name="frontend",
                target_repository_id=3,
                target_repository_name="shared-auth",
                connection_type="manifest_dependency",
                direction="outbound",
                confidence=0.82,
                evidence_count=1,
                evidence_samples=["shared-auth in package.json"],
            ),
            RepositoryConnectionEdge(
                source_repository_id=3,
                source_repository_name="shared-auth",
                target_repository_id=2,
                target_repository_name="backend",
                connection_type="api_call",
                direction="outbound",
                confidence=0.89,
                evidence_count=1,
                evidence_samples=["POST /validate -> ValidationController"],
            ),
        ],
        pair_summaries=[
            RepositoryConnectionAnalysis(
                repo_a=RepositoryIdentity(id=1, name="frontend", path_with_namespace="team/frontend"),
                repo_b=RepositoryIdentity(id=2, name="backend", path_with_namespace="team/backend"),
                connected=True,
                direct_connections=[],
                indirect_paths=[
                    RepositoryConnectionPath(
                        repository_ids=[1, 3, 2],
                        repository_names=["frontend", "shared-auth", "backend"],
                        edge_types=["manifest_dependency", "api_call"],
                        combined_confidence=0.82,
                        summary="frontend (manifest_dependency) -> shared-auth (api_call) -> backend",
                    )
                ],
            )
        ],
        hub_repositories=["shared-auth"],
    )
    session = AsyncMock()

    with patch("src.mcp_server.tools.repository_connections.get_async_session", return_value=_mock_session_cm(session)), patch(
        "src.mcp_server.tools.repository_connections.RepositoryConnectionService"
    ) as service_cls:
        service_cls.return_value.get_repository_connection_subgraph = AsyncMock(return_value=subgraph)
        result = await get_repository_connection_subgraph(repository_names=["frontend", "backend"])

    text = result[0].text
    assert "Repository Connection Subgraph" in text
    assert "Hub Repositories" in text
    assert "shared-auth" in text
    assert "frontend <-> backend: connected" in text


@pytest.mark.asyncio
async def test_analyze_architecture_includes_external_repository_connections():
    session = AsyncMock()

    repo = Mock()
    repo.id = 7
    repo.name = "frontend"

    services_result = Mock()
    services_result.scalars.return_value.all.return_value = []
    repo_result = Mock()
    repo_result.scalar_one_or_none.return_value = repo

    session.execute.side_effect = [repo_result, services_result]

    subgraph = RepositoryConnectionSubgraph(
        repositories=[
            RepositoryIdentity(id=7, name="frontend", path_with_namespace="team/frontend"),
            RepositoryIdentity(id=8, name="backend", path_with_namespace="team/backend"),
        ],
        direct_edges=[
            RepositoryConnectionEdge(
                source_repository_id=7,
                source_repository_name="frontend",
                target_repository_id=8,
                target_repository_name="backend",
                connection_type="api_call",
                direction="outbound",
                confidence=0.94,
                evidence_count=1,
                evidence_samples=["GET /api/users -> UsersController"],
            )
        ],
        pair_summaries=[],
        hub_repositories=["frontend"],
    )

    with patch("src.mcp_server.tools.architecture.get_async_session", return_value=_mock_session_cm(session)), patch(
        "src.mcp_server.tools.architecture.PatternDetector"
    ) as detector_cls, patch(
        "src.mcp_server.tools.architecture.RepositoryConnectionService"
    ) as service_cls, patch(
        "src.mcp_server.tools.architecture.find_architecture_support_matches",
        new=AsyncMock(return_value=(None, [])),
    ):
        detector_cls.return_value.detect_patterns = AsyncMock(return_value=[])
        service_cls.return_value.get_repository_connection_subgraph = AsyncMock(return_value=subgraph)
        result = await analyze_architecture(repository_id=7)

    text = result[0].text
    assert "External Repository Connections" in text
    assert "frontend -> backend" in text
    assert "GET /api/users -> UsersController" in text
    assert "find_repository_connections" in text
