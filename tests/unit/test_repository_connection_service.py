from unittest.mock import AsyncMock, patch

import pytest

from src.api.services.repository_connection_service import (
    RepositoryConnectionEdge,
    RepositoryConnectionService,
    RepositoryIdentity,
)


@pytest.mark.asyncio
async def test_find_repository_connections_finds_direct_and_indirect_paths():
    service = RepositoryConnectionService(AsyncMock())
    frontend = RepositoryIdentity(id=1, name="frontend", path_with_namespace="team/frontend")
    backend = RepositoryIdentity(id=2, name="backend", path_with_namespace="team/backend")
    shared = RepositoryIdentity(id=3, name="shared-auth", path_with_namespace="team/shared-auth")

    edges = [
        RepositoryConnectionEdge(
            source_repository_id=1,
            source_repository_name="frontend",
            target_repository_id=2,
            target_repository_name="backend",
            connection_type="api_call",
            direction="outbound",
            confidence=0.93,
            evidence_samples=["GET /api/users -> UsersController"],
        ),
        RepositoryConnectionEdge(
            source_repository_id=1,
            source_repository_name="frontend",
            target_repository_id=3,
            target_repository_name="shared-auth",
            connection_type="manifest_dependency",
            direction="outbound",
            confidence=0.82,
            evidence_samples=["shared-auth in package.json"],
        ),
        RepositoryConnectionEdge(
            source_repository_id=3,
            source_repository_name="shared-auth",
            target_repository_id=2,
            target_repository_name="backend",
            connection_type="event_flow",
            direction="outbound",
            confidence=0.88,
            evidence_samples=["UserValidatedEvent"],
        ),
    ]

    with patch.object(service, "_resolve_repository", side_effect=[frontend, backend]), patch.object(
        service,
        "_build_repository_graph",
        return_value=({1: frontend, 2: backend, 3: shared}, edges),
    ):
        analysis = await service.find_repository_connections("frontend", "backend", max_depth=3)

    assert analysis.connected is True
    assert len(analysis.direct_connections) == 1
    assert analysis.direct_connections[0].connection_type == "api_call"
    assert analysis.indirect_paths
    assert analysis.indirect_paths[0].repository_names == ["frontend", "shared-auth", "backend"]


@pytest.mark.asyncio
async def test_get_repository_connection_subgraph_surfaces_hub_repositories():
    service = RepositoryConnectionService(AsyncMock())
    frontend = RepositoryIdentity(id=1, name="frontend", path_with_namespace="team/frontend")
    backend = RepositoryIdentity(id=2, name="backend", path_with_namespace="team/backend")
    shared = RepositoryIdentity(id=3, name="shared-auth", path_with_namespace="team/shared-auth")

    edges = [
        RepositoryConnectionEdge(
            source_repository_id=1,
            source_repository_name="frontend",
            target_repository_id=3,
            target_repository_name="shared-auth",
            connection_type="manifest_dependency",
            direction="outbound",
            confidence=0.82,
            evidence_count=2,
            evidence_samples=["shared-auth in package.json"],
        ),
        RepositoryConnectionEdge(
            source_repository_id=3,
            source_repository_name="shared-auth",
            target_repository_id=2,
            target_repository_name="backend",
            connection_type="api_call",
            direction="outbound",
            confidence=0.88,
            evidence_count=3,
            evidence_samples=["POST /validate -> ValidationController"],
        ),
    ]

    resolver = AsyncMock(side_effect=lambda ref: {"frontend": frontend, "backend": backend, 1: frontend, 2: backend}.get(ref))

    with patch.object(service, "_resolve_repository", resolver), patch.object(
        service,
        "_build_repository_graph",
        return_value=({1: frontend, 2: backend, 3: shared}, edges),
    ):
        subgraph = await service.get_repository_connection_subgraph(["frontend", "backend"], max_depth=3)

    assert [repo.name for repo in subgraph.repositories] == ["backend", "frontend", "shared-auth"]
    assert subgraph.hub_repositories[0] == "shared-auth"
    assert subgraph.pair_summaries[0].connected is True


@pytest.mark.asyncio
async def test_predictive_stack_relations_surface_direct_and_indirect_connections():
    service = RepositoryConnectionService(AsyncMock())
    management = RepositoryIdentity(
        id=5,
        name="dasc-prediction-management",
        path_with_namespace="team/dasc-prediction-management",
    )
    domain = RepositoryIdentity(
        id=4,
        name="dasc-prediction-domain",
        path_with_namespace="team/dasc-prediction-domain",
    )
    predictive = RepositoryIdentity(
        id=3,
        name="dasc-predictive",
        path_with_namespace="team/dasc-predictive",
    )
    infra = RepositoryIdentity(
        id=7,
        name="infrastructure-automation",
        path_with_namespace="team/infrastructure-automation",
    )

    edges = [
        RepositoryConnectionEdge(
            source_repository_id=5,
            source_repository_name=management.name,
            target_repository_id=4,
            target_repository_name=domain.name,
            connection_type="symbol_import",
            direction="outbound",
            confidence=0.9,
            evidence_samples=["IPredictiveRestService IMPORTS ILegacyPredictiveRestService"],
        ),
        RepositoryConnectionEdge(
            source_repository_id=5,
            source_repository_name=management.name,
            target_repository_id=7,
            target_repository_name=infra.name,
            connection_type="shared_config_reference",
            direction="bidirectional",
            confidence=0.68,
            evidence_samples=["prediction-management.j2 references 'predictionmanagement'"],
            status="heuristic",
        ),
        RepositoryConnectionEdge(
            source_repository_id=7,
            source_repository_name=infra.name,
            target_repository_id=3,
            target_repository_name=predictive.name,
            connection_type="shared_config_reference",
            direction="bidirectional",
            confidence=0.68,
            evidence_samples=["predictive-service-v2.yml references 'predictive'"],
            status="heuristic",
        ),
    ]

    resolver = AsyncMock(
        side_effect=lambda ref: {
            "management": management,
            "domain": domain,
            "predictive": predictive,
            5: management,
            4: domain,
            3: predictive,
        }.get(ref)
    )

    with patch.object(service, "_resolve_repository", resolver), patch.object(
        service,
        "_build_repository_graph",
        return_value=({3: predictive, 4: domain, 5: management, 7: infra}, edges),
    ):
        direct_analysis = await service.find_repository_connections("management", "domain", max_depth=3)
        indirect_analysis = await service.find_repository_connections("management", "predictive", max_depth=3)

    assert direct_analysis.connected is True
    assert direct_analysis.direct_connections[0].connection_type == "symbol_import"
    assert indirect_analysis.connected is True
    assert indirect_analysis.indirect_paths[0].repository_names == [
        "dasc-prediction-management",
        "infrastructure-automation",
        "dasc-predictive",
    ]


def test_match_dependency_to_repository_maps_maven_artifact_to_repo_name():
    service = RepositoryConnectionService(AsyncMock())
    repositories = [
        RepositoryIdentity(
            id=4,
            name="dasc-prediction-domain",
            path_with_namespace="team/dasc-prediction-domain",
        ),
        RepositoryIdentity(
            id=5,
            name="dasc-prediction-management",
            path_with_namespace="team/dasc-prediction-management",
        ),
    ]

    matched_repo, confidence = service._match_dependency_to_repository(
        package_name="de.webtrekk.prediction:prediction-domain",
        repositories=repositories,
        source_repository_id=5,
    )

    assert matched_repo is not None
    assert matched_repo.name == "dasc-prediction-domain"
    assert confidence >= 0.84


def test_infer_artifact_reference_edges_promotes_manifest_dependency_text():
    service = RepositoryConnectionService(AsyncMock())
    repositories = [
        RepositoryIdentity(
            id=4,
            name="dasc-prediction-domain",
            path_with_namespace="team/dasc-prediction-domain",
        ),
        RepositoryIdentity(
            id=5,
            name="dasc-prediction-management",
            path_with_namespace="team/dasc-prediction-management",
        ),
    ]

    edges = service._infer_artifact_reference_edges(
        source_repository_id=5,
        source_repository_name="dasc-prediction-management",
        file_path="pom.xml",
        content="""
        <dependency>
            <groupId>de.webtrekk.prediction</groupId>
            <artifactId>prediction-domain</artifactId>
        </dependency>
        """,
        repositories=repositories,
    )

    assert len(edges) == 1
    edge = edges[0]
    assert edge.connection_type == "manifest_dependency"
    assert edge.target_repository_name == "dasc-prediction-domain"
    assert edge.status == "direct"
    assert "prediction-domain" in edge.evidence_samples[0]
    assert edge.confidence >= 0.84


def test_infer_artifact_reference_edges_promotes_runtime_config_to_predictive_service():
    service = RepositoryConnectionService(AsyncMock())
    repositories = [
        RepositoryIdentity(
            id=3,
            name="dasc-predictive",
            path_with_namespace="team/dasc-predictive",
        ),
        RepositoryIdentity(
            id=5,
            name="dasc-prediction-management",
            path_with_namespace="team/dasc-prediction-management",
        ),
    ]

    edges = service._infer_artifact_reference_edges(
        source_repository_id=5,
        source_repository_name="dasc-prediction-management",
        file_path="src/main/resources/application-dev.yml",
        content="""
        predictive:
          host: http://predictive-dev-01.nbg.webtrekk.com:8080
        service.predictive.model.find-latest-by-account: http://predictive-dev-01.nbg.webtrekk.com:6000/urm/api/v0.2/model?track_id={accoundId}&latest={latest}
        """,
        repositories=repositories,
    )

    assert len(edges) == 1
    edge = edges[0]
    assert edge.connection_type == "runtime_config_reference"
    assert edge.target_repository_name == "dasc-predictive"
    assert "predictive-dev-01.nbg.webtrekk.com" in edge.evidence_samples[0]
    assert edge.confidence >= 0.79


def test_infer_artifact_reference_edges_promotes_runtime_config_to_ds_recommender():
    service = RepositoryConnectionService(AsyncMock())
    repositories = [
        RepositoryIdentity(
            id=1,
            name="dasc-ds-recommender",
            path_with_namespace="team/dasc-ds-recommender",
        ),
        RepositoryIdentity(
            id=6,
            name="analytics-mainserver",
            path_with_namespace="team/analytics-mainserver",
        ),
    ]

    edges = service._infer_artifact_reference_edges(
        source_repository_id=6,
        source_repository_name="analytics-mainserver",
        file_path="plugins/feedExportPlugin/server/src/main/resources/feedExportPlugin.property",
        content="""
        ds.recommender.url=http://ds-recommender-dev-01.nbg.webtrekk.com/api/jobqueue
        ds.recommender.mongo.host=db-common-dev-01-01.nbg.webtrekk.com
        """,
        repositories=repositories,
    )

    assert len(edges) == 1
    edge = edges[0]
    assert edge.connection_type == "runtime_config_reference"
    assert edge.target_repository_name == "dasc-ds-recommender"
    assert "ds.recommender.url" in edge.evidence_samples[0]
