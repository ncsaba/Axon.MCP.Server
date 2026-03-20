from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.services.repository_connection_service import (
    RepositoryConnectionEdge,
    RepositoryIdentity,
)
from src.api.services.repository_grouping_service import RepositoryGroupingService
from src.database.models import RepositoryGroup, RepositoryGroupMember


@pytest.mark.asyncio
async def test_refresh_inferred_groups_persists_predictive_stack_membership():
    session = AsyncMock()
    added_objects = []

    def add_object(obj):
        if isinstance(obj, RepositoryGroup) and obj.id is None:
            obj.id = 1
        added_objects.append(obj)

    session.add = MagicMock(side_effect=add_object)
    service = RepositoryGroupingService(session)

    repositories = {
        1: RepositoryIdentity(1, "dasc-ds-recommender", "team/dasc-ds-recommender"),
        3: RepositoryIdentity(3, "dasc-predictive", "team/dasc-predictive"),
        4: RepositoryIdentity(4, "dasc-prediction-domain", "team/dasc-prediction-domain"),
        5: RepositoryIdentity(5, "dasc-prediction-management", "team/dasc-prediction-management"),
        6: RepositoryIdentity(6, "analytics-mainserver", "team/analytics-mainserver"),
        7: RepositoryIdentity(7, "infrastructure-automation", "team/infrastructure-automation"),
    }
    edges = [
        RepositoryConnectionEdge(5, "dasc-prediction-management", 4, "dasc-prediction-domain", "symbol_import", "outbound", 0.9),
        RepositoryConnectionEdge(6, "analytics-mainserver", 4, "dasc-prediction-domain", "symbol_import", "outbound", 0.9),
        RepositoryConnectionEdge(7, "infrastructure-automation", 5, "dasc-prediction-management", "shared_config_reference", "bidirectional", 0.68),
        RepositoryConnectionEdge(7, "infrastructure-automation", 3, "dasc-predictive", "shared_config_reference", "bidirectional", 0.68),
        RepositoryConnectionEdge(6, "analytics-mainserver", 1, "dasc-ds-recommender", "symbol_call", "outbound", 0.87),
    ]

    with patch.object(
        service.connection_service,
        "load_repository_graph",
        AsyncMock(return_value=(repositories, edges)),
    ):
        groups = await service.refresh_inferred_groups()

    group_objects = [obj for obj in added_objects if isinstance(obj, RepositoryGroup)]
    membership_objects = [obj for obj in added_objects if isinstance(obj, RepositoryGroupMember)]

    assert len(groups) == 1
    assert len(group_objects) == 1
    assert set(member.repository_id for member in membership_objects) == {3, 4, 5, 6, 7}
    assert any(member.repository_id == 7 and member.membership_role == "support" for member in membership_objects)
    assert "stack" in group_objects[0].display_name.lower()


@pytest.mark.asyncio
async def test_get_query_scope_returns_primary_repo_first_and_support_repo_last():
    session = AsyncMock()
    repository = MagicMock()
    repository.id = 5
    repository.name = "dasc-prediction-management"
    session.get.return_value = repository

    group = RepositoryGroup(
        id=1,
        slug="inferred-predictive-stack",
        display_name="Inferred predictive stack",
        group_type="inferred_stack",
        inference_version="v1",
        group_metadata={"repository_ids": [5, 4, 7]},
    )
    membership = RepositoryGroupMember(
        group_id=1,
        repository_id=5,
        membership_role="member",
        confidence=100,
    )
    membership_result = MagicMock()
    membership_result.all.return_value = [(membership, group)]

    support_repo = MagicMock()
    support_repo.id = 7
    support_repo.name = "infrastructure-automation"
    domain_repo = MagicMock()
    domain_repo.id = 4
    domain_repo.name = "dasc-prediction-domain"
    management_repo = MagicMock()
    management_repo.id = 5
    management_repo.name = "dasc-prediction-management"

    member_rows = [
        (
            RepositoryGroupMember(group_id=1, repository_id=7, membership_role="support", confidence=60),
            support_repo,
        ),
        (
            RepositoryGroupMember(group_id=1, repository_id=4, membership_role="member", confidence=70),
            domain_repo,
        ),
        (
            RepositoryGroupMember(group_id=1, repository_id=5, membership_role="member", confidence=100),
            management_repo,
        ),
    ]
    members_result = MagicMock()
    members_result.all.return_value = member_rows
    session.execute.side_effect = [membership_result, members_result]

    service = RepositoryGroupingService(session)
    scope = await service.get_query_scope(5)

    assert scope.repository_ids == [5, 4, 7]
    assert scope.repository_names == [
        "dasc-prediction-management",
        "dasc-prediction-domain",
        "infrastructure-automation",
    ]
    assert scope.group_display_name == "Inferred predictive stack"
    assert scope.support_repository_ids == [7]


@pytest.mark.asyncio
async def test_refresh_inferred_groups_uses_runtime_config_edges_for_stack_membership():
    session = AsyncMock()
    added_objects = []

    def add_object(obj):
        if isinstance(obj, RepositoryGroup) and obj.id is None:
            obj.id = 11
        added_objects.append(obj)

    session.add = MagicMock(side_effect=add_object)
    service = RepositoryGroupingService(session)

    repositories = {
        3: RepositoryIdentity(3, "dasc-predictive", "team/dasc-predictive"),
        4: RepositoryIdentity(4, "dasc-prediction-domain", "team/dasc-prediction-domain"),
        5: RepositoryIdentity(5, "dasc-prediction-management", "team/dasc-prediction-management"),
        6: RepositoryIdentity(6, "analytics-mainserver", "team/analytics-mainserver"),
    }
    edges = [
        RepositoryConnectionEdge(
            5,
            "dasc-prediction-management",
            4,
            "dasc-prediction-domain",
            "manifest_dependency",
            "outbound",
            0.86,
        ),
        RepositoryConnectionEdge(
            5,
            "dasc-prediction-management",
            3,
            "dasc-predictive",
            "runtime_config_reference",
            "outbound",
            0.82,
        ),
        RepositoryConnectionEdge(
            6,
            "analytics-mainserver",
            3,
            "dasc-predictive",
            "runtime_config_reference",
            "outbound",
            0.82,
        ),
    ]

    with patch.object(
        service.connection_service,
        "load_repository_graph",
        AsyncMock(return_value=(repositories, edges)),
    ):
        groups = await service.refresh_inferred_groups()

    membership_objects = [obj for obj in added_objects if isinstance(obj, RepositoryGroupMember)]

    assert len(groups) == 1
    assert set(member.repository_id for member in membership_objects) == {3, 4, 5, 6}
