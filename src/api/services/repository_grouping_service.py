"""Persisted repository stack inference and query-scope expansion."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.services.repository_connection_service import (
    RepositoryConnectionEdge,
    RepositoryConnectionService,
    RepositoryIdentity,
)
from src.database.models import Repository, RepositoryGroup, RepositoryGroupMember


@dataclass
class RepositoryQueryScope:
    """Expanded repository scope for retrieval."""

    primary_repository_id: int
    repository_ids: List[int]
    repository_names: List[str]
    group_slug: Optional[str] = None
    group_display_name: Optional[str] = None
    support_repository_ids: List[int] = field(default_factory=list)

    @property
    def expanded(self) -> bool:
        return len(self.repository_ids) > 1


class RepositoryGroupingService:
    """Infer and persist repository stacks from grounded repository graph evidence."""

    INFERRED_GROUP_TYPE = "inferred_stack"
    INFERENCE_VERSION = "v1"
    MIN_GROUP_EDGE_CONFIDENCE = 0.61
    GROUPABLE_CONNECTION_TYPES = {
        "api_call",
        "event_flow",
        "manifest_dependency",
        "service_mapping",
        "symbol_import",
        "symbol_inheritance",
        "symbol_implementation",
        "symbol_usage",
        "shared_config_reference",
    }

    def __init__(self, session: AsyncSession):
        self.session = session
        self.connection_service = RepositoryConnectionService(session)

    async def refresh_inferred_groups(self) -> List[RepositoryGroup]:
        """Rebuild inferred repository stacks from the current repository graph."""
        repositories_by_id, edges = await self.connection_service.load_repository_graph()
        components = self._connected_components(repositories_by_id, edges)

        await self.session.execute(
            delete(RepositoryGroup).where(RepositoryGroup.group_type == self.INFERRED_GROUP_TYPE)
        )
        await self.session.flush()

        created_groups: List[RepositoryGroup] = []
        for component in components:
            if len(component) < 2:
                continue

            component_edges = self._component_edges(component, edges)
            display_name = self._build_group_display_name(component, repositories_by_id)
            slug = self._build_group_slug(component, repositories_by_id)
            group = RepositoryGroup(
                slug=slug,
                display_name=display_name,
                group_type=self.INFERRED_GROUP_TYPE,
                inference_version=self.INFERENCE_VERSION,
                group_metadata={
                    "repository_ids": component,
                    "repository_names": [repositories_by_id[repo_id].name for repo_id in component],
                    "edge_types": sorted({edge.connection_type for edge in component_edges}),
                    "max_confidence": max((edge.confidence for edge in component_edges), default=0.0),
                },
            )
            self.session.add(group)
            await self.session.flush()

            member_scores = self._membership_scores(component, component_edges)
            for repo_id in component:
                repo = repositories_by_id[repo_id]
                self.session.add(
                    RepositoryGroupMember(
                        group_id=group.id,
                        repository_id=repo_id,
                        membership_role=self._membership_role(repo),
                        confidence=member_scores.get(repo_id, 0),
                        membership_metadata={
                            "repository_name": repo.name,
                            "path_with_namespace": repo.path_with_namespace,
                        },
                    )
                )

            created_groups.append(group)

        await self.session.flush()
        return created_groups

    async def get_query_scope(self, repository_id: int) -> RepositoryQueryScope:
        """Return the persisted query scope for a repository."""
        repository = await self.session.get(Repository, repository_id)
        if repository is None:
            raise ValueError(f"Repository not found: {repository_id}")

        membership_stmt = (
            select(RepositoryGroupMember, RepositoryGroup)
            .join(RepositoryGroup, RepositoryGroupMember.group_id == RepositoryGroup.id)
            .where(
                RepositoryGroupMember.repository_id == repository_id,
                RepositoryGroup.group_type == self.INFERRED_GROUP_TYPE,
            )
            .order_by(RepositoryGroup.updated_at.desc(), RepositoryGroup.id.desc())
        )
        membership_result = await self.session.execute(membership_stmt)
        memberships = membership_result.all()

        if not memberships:
            await self.refresh_inferred_groups()
            await self.session.commit()
            membership_result = await self.session.execute(membership_stmt)
            memberships = membership_result.all()

        if not memberships:
            return RepositoryQueryScope(
                primary_repository_id=repository_id,
                repository_ids=[repository_id],
                repository_names=[repository.name],
                support_repository_ids=[],
            )

        best_group = max(
            memberships,
            key=lambda item: (
                len((item[1].group_metadata or {}).get("repository_ids", [])),
                item[0].confidence,
                item[1].id,
            ),
        )[1]

        member_stmt = (
            select(RepositoryGroupMember, Repository)
            .join(Repository, RepositoryGroupMember.repository_id == Repository.id)
            .where(RepositoryGroupMember.group_id == best_group.id)
        )
        member_result = await self.session.execute(member_stmt)
        member_rows = member_result.all()

        member_rows.sort(
            key=lambda item: (
                0 if item[1].id == repository_id else 1,
                0 if item[0].membership_role != "support" else 1,
                item[1].name,
            )
        )
        return RepositoryQueryScope(
            primary_repository_id=repository_id,
            repository_ids=[repo.id for _, repo in member_rows],
            repository_names=[repo.name for _, repo in member_rows],
            group_slug=best_group.slug,
            group_display_name=best_group.display_name,
            support_repository_ids=[
                repo.id for membership, repo in member_rows if membership.membership_role == "support"
            ],
        )

    def _connected_components(
        self,
        repositories_by_id: Dict[int, RepositoryIdentity],
        edges: Sequence[RepositoryConnectionEdge],
    ) -> List[List[int]]:
        """Compute stack-like components without letting support repos bridge unrelated systems."""
        eligible_edges = [
            edge
            for edge in edges
            if edge.confidence >= self.MIN_GROUP_EDGE_CONFIDENCE
            and edge.connection_type in self.GROUPABLE_CONNECTION_TYPES
        ]
        support_ids = {
            repo_id
            for repo_id, repo in repositories_by_id.items()
            if self._membership_role(repo) == "support"
        }
        app_repo_ids = set(repositories_by_id) - support_ids

        app_edges = [
            edge
            for edge in eligible_edges
            if edge.source_repository_id in app_repo_ids
            and edge.target_repository_id in app_repo_ids
        ]
        app_components = self._bfs_components(app_repo_ids, app_edges)
        seeded_components = [component for component in app_components if len(component) > 1]

        repo_to_component: Dict[int, int] = {}
        for index, component in enumerate(seeded_components):
            for repo_id in component:
                repo_to_component[repo_id] = index

        support_edges = [
            edge
            for edge in eligible_edges
            if edge.connection_type == "shared_config_reference"
            and (edge.source_repository_id in support_ids or edge.target_repository_id in support_ids)
        ]

        for support_id in sorted(support_ids):
            component_indexes = {
                repo_to_component[target_id]
                for target_id in self._support_neighbors(support_id, support_edges)
                if target_id in repo_to_component
            }
            if len(component_indexes) == 1:
                component_index = next(iter(component_indexes))
                seeded_components[component_index].append(support_id)
                repo_to_component[support_id] = component_index

        candidate_ids = [
            repo_id
            for repo_id in sorted(app_repo_ids)
            if repo_id not in repo_to_component
        ]
        for candidate_id in candidate_ids:
            attached = False
            for support_id in self._app_support_neighbors(candidate_id, support_edges, support_ids):
                component_index = repo_to_component.get(support_id)
                if component_index is None:
                    continue
                component = seeded_components[component_index]
                if self._lexically_matches_component(candidate_id, component, repositories_by_id):
                    component.append(candidate_id)
                    repo_to_component[candidate_id] = component_index
                    attached = True
                    break
            if not attached:
                seeded_components.append([candidate_id])
                repo_to_component[candidate_id] = len(seeded_components) - 1

        for support_id in sorted(support_ids):
            if support_id not in repo_to_component:
                seeded_components.append([support_id])

        normalized_components = [sorted(set(component)) for component in seeded_components]
        normalized_components.sort(key=lambda item: (-len(item), item))
        return normalized_components

    def _bfs_components(
        self,
        repository_ids: set[int],
        edges: Sequence[RepositoryConnectionEdge],
    ) -> List[List[int]]:
        """Compute undirected connected components across the provided edge set."""
        adjacency: Dict[int, set[int]] = defaultdict(set)
        for edge in edges:
            adjacency[edge.source_repository_id].add(edge.target_repository_id)
            adjacency[edge.target_repository_id].add(edge.source_repository_id)

        visited: set[int] = set()
        components: List[List[int]] = []
        for repo_id in sorted(repository_ids):
            if repo_id in visited:
                continue

            queue = deque([repo_id])
            component: List[int] = []
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                for neighbor in sorted(adjacency.get(current, set())):
                    if neighbor not in visited:
                        queue.append(neighbor)

            components.append(sorted(component))

        return components

    def _support_neighbors(
        self,
        support_id: int,
        support_edges: Sequence[RepositoryConnectionEdge],
    ) -> set[int]:
        """Return app repositories referenced by a support repository."""
        neighbors: set[int] = set()
        for edge in support_edges:
            if edge.source_repository_id == support_id:
                neighbors.add(edge.target_repository_id)
            elif edge.target_repository_id == support_id:
                neighbors.add(edge.source_repository_id)
        return neighbors

    def _app_support_neighbors(
        self,
        app_repo_id: int,
        support_edges: Sequence[RepositoryConnectionEdge],
        support_ids: set[int],
    ) -> List[int]:
        """Return support repositories connected to an app repository."""
        neighbors: List[int] = []
        for edge in support_edges:
            if edge.source_repository_id == app_repo_id and edge.target_repository_id in support_ids:
                neighbors.append(edge.target_repository_id)
            elif edge.target_repository_id == app_repo_id and edge.source_repository_id in support_ids:
                neighbors.append(edge.source_repository_id)
        return neighbors

    def _lexically_matches_component(
        self,
        candidate_repo_id: int,
        component: Sequence[int],
        repositories_by_id: Dict[int, RepositoryIdentity],
    ) -> bool:
        """Require a weak lexical family match before support edges can pull an app repo into a stack."""
        candidate_tokens = self._grouping_identity_tokens(repositories_by_id[candidate_repo_id])
        candidate_tokens = {token for token in candidate_tokens if len(token) >= 8}
        for repo_id in component:
            repo = repositories_by_id[repo_id]
            if self._membership_role(repo) == "support":
                continue
            for component_token in self._grouping_identity_tokens(repo):
                if len(component_token) < 8:
                    continue
                if candidate_tokens & {component_token}:
                    return True
                if any(
                    token[:7] == component_token[:7]
                    for token in candidate_tokens
                    if len(token) >= 7 and len(component_token) >= 7
                ):
                    return True
        return False

    def _grouping_identity_tokens(self, repo: RepositoryIdentity) -> set[str]:
        """Grouping-specific identity tokens that ignore shared namespace prefixes."""
        path_leaf = repo.path_with_namespace.split("/")[-1]
        tokens = set()
        tokens.update(self.connection_service._expanded_identity_tokens(repo.name))
        tokens.update(self.connection_service._expanded_identity_tokens(path_leaf))
        return tokens

    def _component_edges(
        self,
        component: Sequence[int],
        edges: Sequence[RepositoryConnectionEdge],
    ) -> List[RepositoryConnectionEdge]:
        component_ids = set(component)
        return [
            edge
            for edge in edges
            if edge.source_repository_id in component_ids
            and edge.target_repository_id in component_ids
        ]

    def _membership_scores(
        self,
        component: Sequence[int],
        edges: Sequence[RepositoryConnectionEdge],
    ) -> Dict[int, int]:
        scores = {repo_id: 0 for repo_id in component}
        for edge in edges:
            bump = max(1, int(round(edge.confidence * 100)))
            scores[edge.source_repository_id] = min(100, scores.get(edge.source_repository_id, 0) + bump)
            scores[edge.target_repository_id] = min(100, scores.get(edge.target_repository_id, 0) + bump)
        return scores

    def _build_group_display_name(
        self,
        component: Sequence[int],
        repositories_by_id: Dict[int, RepositoryIdentity],
    ) -> str:
        counter: Counter[str] = Counter()
        stop_tokens = {
            "dasc",
            "repo",
            "repository",
            "infra",
            "infrastructure",
            "automation",
            "main",
            "server",
            "service",
        }
        for repo_id in component:
            repo = repositories_by_id[repo_id]
            tokens = [
                token
                for token in repo.path_with_namespace.split("/")[-1].replace("_", "-").split("-")
                if token and token not in stop_tokens
            ]
            counter.update(tokens)

        dominant_token = None
        dominant_count = 0
        for token, count in counter.most_common():
            if len(token) >= 6:
                dominant_token = token
                dominant_count = count
                break

        if dominant_token and dominant_count >= 2:
            return f"Inferred {dominant_token} stack"

        lead_repo = repositories_by_id[component[0]].name
        if len(component) == 2:
            return f"Inferred stack: {lead_repo} + 1 related repo"
        return f"Inferred stack: {lead_repo} + {len(component) - 1} related repos"

    def _build_group_slug(
        self,
        component: Sequence[int],
        repositories_by_id: Dict[int, RepositoryIdentity],
    ) -> str:
        digest = hashlib.sha1(",".join(str(repo_id) for repo_id in component).encode("utf-8")).hexdigest()[:12]
        lead_repo = repositories_by_id[component[0]].path_with_namespace.split("/")[-1].lower().replace("_", "-")
        return f"inferred-{lead_repo}-{digest}"

    def _membership_role(self, repository: RepositoryIdentity) -> str:
        lowered = repository.name.lower()
        if "infrastructure" in lowered or "automation" in lowered:
            return "support"
        return "member"
