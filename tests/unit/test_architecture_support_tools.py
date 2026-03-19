from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.mcp_server.tools.architecture import analyze_architecture
from src.mcp_server.tools.architecture_support import ArchitectureSupportMatch
from src.mcp_server.tools.exploration import get_project_map, query_codebase_structure


def _mock_session_cm(session):
    manager = AsyncMock()
    manager.__aenter__.return_value = session
    manager.__aexit__.return_value = None
    return manager


@pytest.mark.asyncio
async def test_analyze_architecture_includes_infrastructure_automation_context():
    session = AsyncMock()

    repo = Mock()
    repo.id = 7
    repo.name = "dasc-prediction-management"

    services_result = Mock()
    services_result.scalars.return_value.all.return_value = []
    repo_result = Mock()
    repo_result.scalar_one_or_none.return_value = repo

    session.execute.side_effect = [repo_result, services_result]

    support_repo = Mock()
    support_repo.id = 12
    support_repo.name = "infrastructure-automation"

    with patch("src.mcp_server.tools.architecture.get_async_session", return_value=_mock_session_cm(session)), patch(
        "src.mcp_server.tools.architecture.PatternDetector"
    ) as detector_cls, patch(
        "src.mcp_server.tools.architecture.RepositoryConnectionService"
    ) as service_cls, patch(
        "src.mcp_server.tools.architecture.find_architecture_support_matches",
        new=AsyncMock(
            return_value=(
                support_repo,
                [
                    ArchitectureSupportMatch(
                        file_path="plays/prediction-management.yml",
                        score=10,
                        matched_terms=["prediction", "management"],
                    )
                ],
            )
        ),
    ):
        detector_cls.return_value.detect_patterns = AsyncMock(return_value=[])
        service_cls.return_value.get_repository_connection_subgraph = AsyncMock(
            return_value=Mock(direct_edges=[], pair_summaries=[], hub_repositories=[])
        )
        result = await analyze_architecture(repository_id=7)

    text = result[0].text
    assert "Infrastructure Automation Context" in text
    assert "Queried `infrastructure-automation`" in text
    assert "plays/prediction-management.yml" in text


@pytest.mark.asyncio
async def test_get_project_map_appends_infrastructure_context():
    session = AsyncMock()
    repo = Mock()
    repo.id = 5
    repo.name = "dasc-predictive"
    session.get = AsyncMock(return_value=repo)

    support_repo = Mock()
    support_repo.id = 12
    support_repo.name = "infrastructure-automation"

    with patch("src.mcp_server.tools.exploration.get_async_session", return_value=_mock_session_cm(session)), patch(
        "src.mcp_server.tools.exploration.ProjectMapper"
    ) as mapper_cls, patch(
        "src.mcp_server.tools.exploration.find_architecture_support_matches",
        new=AsyncMock(
            return_value=(
                support_repo,
                [
                    ArchitectureSupportMatch(
                        file_path="plays/predictive-service-v2.yml",
                        score=9,
                        matched_terms=["predictive"],
                    )
                ],
            )
        ),
    ):
        mapper_cls.return_value.generate_project_map = AsyncMock(return_value="# Project Map")
        result = await get_project_map(repository_id=5)

    text = result[0].text
    assert "# Project Map" in text
    assert "Infrastructure Automation Context" in text
    assert "plays/predictive-service-v2.yml" in text


@pytest.mark.asyncio
async def test_query_codebase_structure_appends_infrastructure_context():
    session = AsyncMock()
    repo = Mock()
    repo.id = 6
    repo.name = "analytics-mainserver"
    session.get = AsyncMock(return_value=repo)

    translator = Mock()
    translator.translate = AsyncMock(return_value="SELECT 1")
    translator.execute = AsyncMock(return_value=Mock(row_count=1, execution_time_ms=12))
    translator.format_results_markdown.return_value = "# Query Result"

    support_repo = Mock()
    support_repo.id = 12
    support_repo.name = "infrastructure-automation"

    with patch("src.mcp_server.tools.exploration.get_async_session", return_value=_mock_session_cm(session)), patch(
        "src.mcp_server.tools.exploration.TextToSQLTranslator",
        return_value=translator,
    ), patch(
        "src.mcp_server.tools.exploration.find_architecture_support_matches",
        new=AsyncMock(
            return_value=(
                support_repo,
                [
                    ArchitectureSupportMatch(
                        file_path="plays/analytics.yml",
                        score=8,
                        matched_terms=["analytics"],
                    )
                ],
            )
        ),
    ):
        result = await query_codebase_structure(
            query="show architecture for analytics-mainserver",
            repository_id=6,
        )

    text = result[0].text
    assert "# Query Result" in text
    assert "Infrastructure Automation Context" in text
    assert "plays/analytics.yml" in text
