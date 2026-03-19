"""Repository-to-repository connection retrieval primitives."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Sequence, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.config.enums import FileLifecycleStateEnum, RelationTypeEnum
from src.database.models import (
    ApiEndpointLink,
    Dependency,
    DockerService,
    EventLink,
    EventSubscription,
    FileInstance as File,
    OutgoingApiCall,
    PublishedEvent,
    Relation,
    Repository,
    ServiceRepositoryMapping,
    Symbol,
)


RepositoryRef = Union[int, str]


@dataclass(frozen=True)
class RepositoryIdentity:
    """Resolved repository identity."""

    id: int
    name: str
    path_with_namespace: str


@dataclass
class RepositoryConnectionEdge:
    """Normalized repository-to-repository edge."""

    source_repository_id: int
    source_repository_name: str
    target_repository_id: int
    target_repository_name: str
    connection_type: str
    direction: str
    confidence: float
    evidence_count: int = 1
    evidence_samples: List[str] = field(default_factory=list)
    status: str = "direct"
    notes: Optional[str] = None


@dataclass
class RepositoryConnectionPath:
    """Indirect path between repositories."""

    repository_ids: List[int]
    repository_names: List[str]
    edge_types: List[str]
    combined_confidence: float
    summary: str


@dataclass
class RepositoryConnectionAnalysis:
    """Pairwise repository connection analysis."""

    repo_a: RepositoryIdentity
    repo_b: RepositoryIdentity
    connected: bool
    direct_connections: List[RepositoryConnectionEdge]
    indirect_paths: List[RepositoryConnectionPath]


@dataclass
class RepositoryConnectionSubgraph:
    """Multi-repository connection view."""

    repositories: List[RepositoryIdentity]
    direct_edges: List[RepositoryConnectionEdge]
    pair_summaries: List[RepositoryConnectionAnalysis]
    hub_repositories: List[str]


class RepositoryConnectionService:
    """Build grounded repository-to-repository connection views."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def load_repository_graph(
        self,
    ) -> tuple[Dict[int, RepositoryIdentity], List[RepositoryConnectionEdge]]:
        """Expose the normalized repository graph for other retrieval services."""
        return await self._build_repository_graph()

    async def find_repository_connections(
        self,
        repo_a: RepositoryRef,
        repo_b: RepositoryRef,
        *,
        max_depth: int = 2,
    ) -> RepositoryConnectionAnalysis:
        """Return direct and indirect connections between two repositories."""
        resolved_a = await self._resolve_repository(repo_a)
        resolved_b = await self._resolve_repository(repo_b)

        if resolved_a is None:
            raise ValueError(f"Repository not found: {repo_a}")
        if resolved_b is None:
            raise ValueError(f"Repository not found: {repo_b}")

        _, edges = await self._build_repository_graph()
        direct_connections = [
            edge
            for edge in edges
            if {
                edge.source_repository_id,
                edge.target_repository_id,
            } == {resolved_a.id, resolved_b.id}
        ]
        direct_connections.sort(key=lambda edge: (-edge.confidence, edge.connection_type))

        indirect_paths: List[RepositoryConnectionPath] = []
        indirect_paths.extend(
            self._find_directed_paths(
                start_id=resolved_a.id,
                target_id=resolved_b.id,
                edges=edges,
                max_depth=max_depth,
            )
        )
        indirect_paths.extend(
            self._find_directed_paths(
                start_id=resolved_b.id,
                target_id=resolved_a.id,
                edges=edges,
                max_depth=max_depth,
            )
        )

        seen_paths: set[tuple[int, ...]] = set()
        deduped_paths: List[RepositoryConnectionPath] = []
        for path in sorted(
            indirect_paths,
            key=lambda item: (-item.combined_confidence, len(item.repository_ids), item.summary),
        ):
            key = tuple(path.repository_ids)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            deduped_paths.append(path)

        return RepositoryConnectionAnalysis(
            repo_a=resolved_a,
            repo_b=resolved_b,
            connected=bool(direct_connections or deduped_paths),
            direct_connections=direct_connections,
            indirect_paths=deduped_paths,
        )

    async def get_repository_connection_subgraph(
        self,
        repo_refs: Sequence[RepositoryRef],
        *,
        max_depth: int = 2,
    ) -> RepositoryConnectionSubgraph:
        """Return a reusable connection subgraph across one or more repositories."""
        if not repo_refs:
            raise ValueError("At least one repository reference is required")

        resolved: List[RepositoryIdentity] = []
        for ref in repo_refs:
            repo = await self._resolve_repository(ref)
            if repo is None:
                raise ValueError(f"Repository not found: {ref}")
            if all(existing.id != repo.id for existing in resolved):
                resolved.append(repo)

        repositories_by_id, edges = await self._build_repository_graph()
        seed_ids = {repo.id for repo in resolved}
        relevant_node_ids = set(seed_ids)

        if len(resolved) == 1:
            relevant_node_ids.update(
                edge.target_repository_id
                for edge in edges
                if edge.source_repository_id in seed_ids
            )
            relevant_node_ids.update(
                edge.source_repository_id
                for edge in edges
                if edge.target_repository_id in seed_ids
            )
        else:
            for left, right in combinations(resolved, 2):
                analysis = await self.find_repository_connections(left.id, right.id, max_depth=max_depth)
                for edge in analysis.direct_connections:
                    relevant_node_ids.add(edge.source_repository_id)
                    relevant_node_ids.add(edge.target_repository_id)
                for path in analysis.indirect_paths:
                    relevant_node_ids.update(path.repository_ids)

        direct_edges = [
            edge
            for edge in edges
            if edge.source_repository_id in relevant_node_ids
            and edge.target_repository_id in relevant_node_ids
        ]
        direct_edges.sort(
            key=lambda edge: (
                -edge.confidence,
                edge.source_repository_name,
                edge.target_repository_name,
                edge.connection_type,
            )
        )

        pair_summaries: List[RepositoryConnectionAnalysis] = []
        if len(resolved) > 1:
            for left, right in combinations(resolved, 2):
                pair_summaries.append(
                    await self.find_repository_connections(left.id, right.id, max_depth=max_depth)
                )

        hub_scores: Dict[int, int] = defaultdict(int)
        for edge in direct_edges:
            hub_scores[edge.source_repository_id] += edge.evidence_count
            hub_scores[edge.target_repository_id] += edge.evidence_count

        hub_repositories = [
            repositories_by_id[repo_id].name
            for repo_id, _ in sorted(
                hub_scores.items(),
                key=lambda item: (-item[1], repositories_by_id[item[0]].name),
            )[:5]
            if repo_id in repositories_by_id
        ]

        relevant_repositories = [
            repositories_by_id[repo_id]
            for repo_id in sorted(relevant_node_ids, key=lambda item: repositories_by_id[item].name)
            if repo_id in repositories_by_id
        ]

        return RepositoryConnectionSubgraph(
            repositories=relevant_repositories,
            direct_edges=direct_edges,
            pair_summaries=pair_summaries,
            hub_repositories=hub_repositories,
        )

    async def _resolve_repository(self, ref: RepositoryRef) -> Optional[RepositoryIdentity]:
        """Resolve a repository by id, name, or path."""
        if isinstance(ref, int):
            result = await self.session.execute(select(Repository).where(Repository.id == ref))
            repo = result.scalars().first()
            return self._to_identity(repo) if repo else None

        normalized = str(ref).strip()
        if not normalized:
            return None

        exact = await self.session.execute(
            select(Repository).where(
                (Repository.name == normalized) | (Repository.path_with_namespace == normalized)
            )
        )
        repo = exact.scalars().first()
        if repo:
            return self._to_identity(repo)

        fuzzy = await self.session.execute(
            select(Repository).where(
                Repository.name.ilike(f"%{normalized}%")
                | Repository.path_with_namespace.ilike(f"%{normalized}%")
            )
        )
        repo = fuzzy.scalars().first()
        return self._to_identity(repo) if repo else None

    async def _build_repository_graph(
        self,
    ) -> tuple[Dict[int, RepositoryIdentity], List[RepositoryConnectionEdge]]:
        """Load repositories and all normalized direct edges."""
        repo_result = await self.session.execute(select(Repository))
        repos = repo_result.scalars().all()
        repositories_by_id = {
            repo.id: RepositoryIdentity(
                id=repo.id,
                name=repo.name,
                path_with_namespace=repo.path_with_namespace,
            )
            for repo in repos
        }

        edges: List[RepositoryConnectionEdge] = []
        edges.extend(await self._load_api_call_edges())
        edges.extend(await self._load_event_edges())
        edges.extend(await self._load_service_mapping_edges())
        edges.extend(await self._load_manifest_dependency_edges(list(repositories_by_id.values())))
        edges.extend(await self._load_relation_edges())
        edges.extend(await self._load_support_reference_edges(list(repositories_by_id.values())))

        return repositories_by_id, self._aggregate_edges(edges)

    async def _load_api_call_edges(self) -> List[RepositoryConnectionEdge]:
        """Load direct API-call evidence between repositories."""
        source_repo = aliased(Repository)
        target_repo = aliased(Repository)
        stmt = (
            select(
                ApiEndpointLink.source_repository_id,
                source_repo.name,
                ApiEndpointLink.target_repository_id,
                target_repo.name,
                ApiEndpointLink.match_confidence,
                ApiEndpointLink.match_method,
                ApiEndpointLink.gateway_route_pattern,
                OutgoingApiCall.http_method,
                OutgoingApiCall.url_pattern,
                Symbol.name,
                File.path,
            )
            .join(source_repo, ApiEndpointLink.source_repository_id == source_repo.id)
            .join(target_repo, ApiEndpointLink.target_repository_id == target_repo.id)
            .join(OutgoingApiCall, ApiEndpointLink.outgoing_call_id == OutgoingApiCall.id)
            .outerjoin(Symbol, ApiEndpointLink.target_symbol_id == Symbol.id)
            .outerjoin(File, Symbol.file_instance_id == File.id)
            .where(ApiEndpointLink.target_repository_id.is_not(None))
        )
        result = await self.session.execute(stmt)

        edges: List[RepositoryConnectionEdge] = []
        for row in result.all():
            target_symbol = row[9] or row[10] or row[3]
            route_hint = f" via gateway {row[6]}" if row[6] else ""
            edges.append(
                RepositoryConnectionEdge(
                    source_repository_id=row[0],
                    source_repository_name=row[1],
                    target_repository_id=row[2],
                    target_repository_name=row[3],
                    connection_type="api_call",
                    direction="outbound",
                    confidence=(row[4] or 0) / 100.0,
                    evidence_samples=[
                        f"{row[7] or 'HTTP'} {row[8]} -> {target_symbol} ({row[5]}){route_hint}"
                    ],
                    notes="Direct runtime HTTP/API coupling",
                )
            )
        return edges

    async def _load_event_edges(self) -> List[RepositoryConnectionEdge]:
        """Load event-flow evidence between repositories."""
        publisher_repo = aliased(Repository)
        subscriber_repo = aliased(Repository)
        stmt = (
            select(
                EventLink.publisher_repository_id,
                publisher_repo.name,
                EventLink.subscriber_repository_id,
                subscriber_repo.name,
                EventLink.match_confidence,
                EventLink.match_method,
                PublishedEvent.event_type_name,
                PublishedEvent.topic_name,
                EventSubscription.queue_name,
            )
            .join(publisher_repo, EventLink.publisher_repository_id == publisher_repo.id)
            .join(subscriber_repo, EventLink.subscriber_repository_id == subscriber_repo.id)
            .join(PublishedEvent, EventLink.published_event_id == PublishedEvent.id)
            .join(EventSubscription, EventLink.event_subscription_id == EventSubscription.id)
        )
        result = await self.session.execute(stmt)

        edges: List[RepositoryConnectionEdge] = []
        for row in result.all():
            queue_hint = f" -> queue {row[8]}" if row[8] else ""
            topic_hint = f" topic {row[7]}" if row[7] else ""
            edges.append(
                RepositoryConnectionEdge(
                    source_repository_id=row[0],
                    source_repository_name=row[1],
                    target_repository_id=row[2],
                    target_repository_name=row[3],
                    connection_type="event_flow",
                    direction="outbound",
                    confidence=(row[4] or 0) / 100.0,
                    evidence_samples=[
                        f"{row[6]} ({row[5]}{topic_hint}{queue_hint})"
                    ],
                    notes="Asynchronous publisher/subscriber coupling",
                )
            )
        return edges

    async def _load_service_mapping_edges(self) -> List[RepositoryConnectionEdge]:
        """Load Docker/service mapping evidence between repositories."""
        source_repo = aliased(Repository)
        target_repo = aliased(Repository)
        stmt = (
            select(
                DockerService.repository_id,
                source_repo.name,
                ServiceRepositoryMapping.target_repository_id,
                target_repo.name,
                ServiceRepositoryMapping.confidence,
                ServiceRepositoryMapping.mapping_method,
                ServiceRepositoryMapping.service_name,
                DockerService.file_path,
            )
            .join(ServiceRepositoryMapping, ServiceRepositoryMapping.docker_service_id == DockerService.id)
            .join(source_repo, DockerService.repository_id == source_repo.id)
            .join(target_repo, ServiceRepositoryMapping.target_repository_id == target_repo.id)
        )
        result = await self.session.execute(stmt)

        edges: List[RepositoryConnectionEdge] = []
        for row in result.all():
            edges.append(
                RepositoryConnectionEdge(
                    source_repository_id=row[0],
                    source_repository_name=row[1],
                    target_repository_id=row[2],
                    target_repository_name=row[3],
                    connection_type="service_mapping",
                    direction="outbound",
                    confidence=(row[4] or 0) / 100.0,
                    evidence_samples=[
                        f"service '{row[6]}' mapped from {row[7]} ({row[5]})"
                    ],
                    status="heuristic",
                    notes="Runtime/service-name topology hint",
                )
            )
        return edges

    async def _load_manifest_dependency_edges(
        self,
        repositories: Sequence[RepositoryIdentity],
    ) -> List[RepositoryConnectionEdge]:
        """Load repository-matched manifest/package dependency evidence."""
        dep_result = await self.session.execute(select(Dependency))
        dependencies = dep_result.scalars().all()

        if not dependencies:
            return []

        repositories_by_id = {repo.id: repo for repo in repositories}
        edges: List[RepositoryConnectionEdge] = []
        for dependency in dependencies:
            source_repo = repositories_by_id.get(dependency.repository_id)
            if source_repo is None:
                continue

            matched_target, confidence = self._match_dependency_to_repository(
                package_name=dependency.package_name,
                repositories=repositories,
                source_repository_id=dependency.repository_id,
            )
            if matched_target is None:
                continue

            dep_kind = "dev dependency" if dependency.is_dev_dependency else "dependency"
            edges.append(
                RepositoryConnectionEdge(
                    source_repository_id=source_repo.id,
                    source_repository_name=source_repo.name,
                    target_repository_id=matched_target.id,
                    target_repository_name=matched_target.name,
                    connection_type="manifest_dependency",
                    direction="outbound",
                    confidence=confidence,
                    evidence_samples=[
                        f"{dependency.package_name} in {dependency.file_path or 'manifest'} ({dep_kind})"
                    ],
                    notes="Build/package dependency matched to an indexed repository",
                )
            )
        return edges

    async def _load_relation_edges(self) -> List[RepositoryConnectionEdge]:
        """Load cross-repository symbol relation evidence."""
        from_symbol = aliased(Symbol)
        to_symbol = aliased(Symbol)
        from_file = aliased(File)
        to_file = aliased(File)
        source_repo = aliased(Repository)
        target_repo = aliased(Repository)

        stmt = (
            select(
                from_file.repository_id,
                source_repo.name,
                to_file.repository_id,
                target_repo.name,
                Relation.relation_type,
                from_symbol.name,
                to_symbol.name,
                from_file.path,
                to_file.path,
            )
            .join(from_symbol, Relation.from_symbol_id == from_symbol.id)
            .join(to_symbol, Relation.to_symbol_id == to_symbol.id)
            .join(from_file, from_symbol.file_instance_id == from_file.id)
            .join(to_file, to_symbol.file_instance_id == to_file.id)
            .join(source_repo, from_file.repository_id == source_repo.id)
            .join(target_repo, to_file.repository_id == target_repo.id)
            .where(
                from_file.repository_id != to_file.repository_id,
                from_file.lifecycle_state == FileLifecycleStateEnum.ACTIVE,
                to_file.lifecycle_state == FileLifecycleStateEnum.ACTIVE,
                Relation.relation_type.in_(
                    [
                        RelationTypeEnum.CALLS,
                        RelationTypeEnum.IMPORTS,
                        RelationTypeEnum.INHERITS,
                        RelationTypeEnum.IMPLEMENTS,
                        RelationTypeEnum.USES,
                        RelationTypeEnum.REFERENCES,
                    ]
                ),
            )
        )
        result = await self.session.execute(stmt)

        relation_config = {
            RelationTypeEnum.CALLS: ("symbol_call", 0.87, "Cross-repo call edge"),
            RelationTypeEnum.IMPORTS: ("symbol_import", 0.9, "Cross-repo import or package coupling"),
            RelationTypeEnum.INHERITS: ("symbol_inheritance", 0.93, "Cross-repo inheritance coupling"),
            RelationTypeEnum.IMPLEMENTS: ("symbol_implementation", 0.92, "Cross-repo implementation coupling"),
            RelationTypeEnum.USES: ("symbol_usage", 0.8, "Cross-repo usage coupling"),
            RelationTypeEnum.REFERENCES: ("symbol_reference", 0.74, "Cross-repo reference coupling"),
        }

        edges: List[RepositoryConnectionEdge] = []
        for row in result.all():
            connection_type, confidence, note = relation_config[row[4]]
            edges.append(
                RepositoryConnectionEdge(
                    source_repository_id=row[0],
                    source_repository_name=row[1],
                    target_repository_id=row[2],
                    target_repository_name=row[3],
                    connection_type=connection_type,
                    direction="outbound",
                    confidence=confidence,
                    evidence_samples=[
                        f"{row[5]} {row[4].value} {row[6]} ({row[7]} -> {row[8]})"
                    ],
                    notes=note,
                )
            )

        return edges

    async def _load_support_reference_edges(
        self,
        repositories: Sequence[RepositoryIdentity],
    ) -> List[RepositoryConnectionEdge]:
        """Load support/config file references that tie deployment repos to app repos."""
        source_repo = aliased(Repository)
        stmt = (
            select(
                File.repository_id,
                source_repo.name,
                File.path,
            )
            .join(source_repo, File.repository_id == source_repo.id)
            .where(File.lifecycle_state == FileLifecycleStateEnum.ACTIVE)
        )
        result = await self.session.execute(stmt)

        edges: List[RepositoryConnectionEdge] = []
        repositories_by_id = {repo.id: repo for repo in repositories}
        for source_repository_id, source_repository_name, file_path in result.all():
            if not self._looks_like_support_context_file(file_path):
                continue

            path_tokens = self._path_match_tokens(file_path)
            if not path_tokens:
                continue

            for target_repo in repositories:
                if target_repo.id == source_repository_id:
                    continue

                matched_token, confidence = self._match_path_tokens_to_repository(
                    path_tokens=path_tokens,
                    repo=target_repo,
                )
                if matched_token is None:
                    continue

                evidence = f"{file_path} references '{matched_token}'"
                edges.append(
                    RepositoryConnectionEdge(
                        source_repository_id=source_repository_id,
                        source_repository_name=source_repository_name,
                        target_repository_id=target_repo.id,
                        target_repository_name=target_repo.name,
                        connection_type="shared_config_reference",
                        direction="bidirectional",
                        confidence=confidence,
                        evidence_samples=[evidence],
                        status="heuristic",
                        notes="Deployment/support artifact references this repository stack member",
                    )
                )
                edges.append(
                    RepositoryConnectionEdge(
                        source_repository_id=target_repo.id,
                        source_repository_name=target_repo.name,
                        target_repository_id=source_repository_id,
                        target_repository_name=source_repository_name,
                        connection_type="shared_config_reference",
                        direction="bidirectional",
                        confidence=confidence,
                        evidence_samples=[evidence],
                        status="heuristic",
                        notes="Deployment/support artifact references this repository stack member",
                    )
                )

        return edges

    def _aggregate_edges(
        self,
        edges: Iterable[RepositoryConnectionEdge],
    ) -> List[RepositoryConnectionEdge]:
        """Merge repeated evidence of the same edge type."""
        grouped: Dict[tuple[int, int, str], RepositoryConnectionEdge] = {}
        for edge in edges:
            key = (
                edge.source_repository_id,
                edge.target_repository_id,
                edge.connection_type,
            )
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = RepositoryConnectionEdge(
                    source_repository_id=edge.source_repository_id,
                    source_repository_name=edge.source_repository_name,
                    target_repository_id=edge.target_repository_id,
                    target_repository_name=edge.target_repository_name,
                    connection_type=edge.connection_type,
                    direction=edge.direction,
                    confidence=edge.confidence,
                    evidence_count=edge.evidence_count,
                    evidence_samples=list(edge.evidence_samples[:3]),
                    status=edge.status,
                    notes=edge.notes,
                )
                continue

            existing.confidence = max(existing.confidence, edge.confidence)
            existing.evidence_count += edge.evidence_count
            for sample in edge.evidence_samples:
                if sample not in existing.evidence_samples and len(existing.evidence_samples) < 3:
                    existing.evidence_samples.append(sample)
            if existing.status != "direct" and edge.status == "direct":
                existing.status = edge.status

        return sorted(
            grouped.values(),
            key=lambda item: (
                -item.confidence,
                -item.evidence_count,
                item.source_repository_name,
                item.target_repository_name,
                item.connection_type,
            ),
        )

    def _find_directed_paths(
        self,
        *,
        start_id: int,
        target_id: int,
        edges: Sequence[RepositoryConnectionEdge],
        max_depth: int,
    ) -> List[RepositoryConnectionPath]:
        """Find short directed paths using the aggregated graph."""
        if max_depth < 2:
            return []

        adjacency: Dict[int, List[RepositoryConnectionEdge]] = defaultdict(list)
        for edge in edges:
            adjacency[edge.source_repository_id].append(edge)

        queue = deque([(start_id, [start_id], [])])
        paths: List[RepositoryConnectionPath] = []
        while queue:
            current_id, visited_ids, visited_edges = queue.popleft()
            if len(visited_ids) - 1 >= max_depth:
                continue

            for edge in adjacency.get(current_id, []):
                next_id = edge.target_repository_id
                if next_id in visited_ids:
                    continue

                next_ids = visited_ids + [next_id]
                next_edges = visited_edges + [edge]
                if next_id == target_id and len(next_edges) > 1:
                    repository_names = [next_edges[0].source_repository_name] + [
                        item.target_repository_name for item in next_edges
                    ]
                    edge_types = [item.connection_type for item in next_edges]
                    confidence = min(item.confidence for item in next_edges)
                    summary = " -> ".join(
                        f"{repository_names[index]} ({edge_types[index]})"
                        for index in range(len(edge_types))
                    )
                    summary = f"{summary} -> {repository_names[-1]}"
                    paths.append(
                        RepositoryConnectionPath(
                            repository_ids=next_ids,
                            repository_names=repository_names,
                            edge_types=edge_types,
                            combined_confidence=confidence,
                            summary=summary,
                        )
                    )
                    continue

                if len(next_edges) < max_depth:
                    queue.append((next_id, next_ids, next_edges))

        return paths

    def _match_dependency_to_repository(
        self,
        *,
        package_name: str,
        repositories: Sequence[RepositoryIdentity],
        source_repository_id: int,
    ) -> tuple[Optional[RepositoryIdentity], float]:
        """Best-effort mapping from package name to indexed repository."""
        dependency_tokens = self._dependency_match_tokens(package_name)
        if not dependency_tokens:
            return None, 0.0

        for repo in repositories:
            if repo.id == source_repository_id:
                continue
            tokens = self._repository_match_tokens(repo)
            for dependency_token in dependency_tokens:
                if dependency_token not in tokens:
                    continue
                if dependency_token == self._normalize_token(repo.name):
                    return repo, 0.95
                if dependency_token == self._normalize_token(repo.path_with_namespace):
                    return repo, 0.9
                if dependency_token == self._normalize_token(repo.path_with_namespace.split("/")[-1]):
                    return repo, 0.88
                return repo, 0.84
        return None, 0.0

    def _repository_match_tokens(self, repo: RepositoryIdentity) -> set[str]:
        """Generate normalized repo identity tokens."""
        path_leaf = repo.path_with_namespace.split("/")[-1]
        values = [repo.name, repo.path_with_namespace, path_leaf]
        tokens = {
            token
            for value in values
            for token in self._expanded_identity_tokens(value)
        }
        return {token for token in tokens if token}

    def _expanded_identity_tokens(self, value: Optional[str]) -> set[str]:
        """Generate whole-value and suffix aliases for repository matching."""
        if not value:
            return set()

        lowered = value.lower()
        raw_parts = [part for part in lowered.replace("/", "-").split("-") if part]
        raw_parts = [part for part in raw_parts if part not in {"repo", "repository"}]
        tokens = {self._normalize_token(value)}
        tokens.update(self._normalize_token(part) for part in raw_parts)

        vendor_prefixes = {"dasc", "axon", "wt", "webtrekk", "ms", "local"}
        if raw_parts and raw_parts[0] in vendor_prefixes:
            raw_parts = raw_parts[1:]

        for index in range(len(raw_parts)):
            suffix = self._normalize_token("".join(raw_parts[index:]))
            if len(suffix) >= 6:
                tokens.add(suffix)

        return {token for token in tokens if token}

    def _dependency_match_tokens(self, package_name: Optional[str]) -> List[str]:
        """Extract ordered candidate tokens from a manifest dependency name."""
        if not package_name:
            return []

        candidates: List[str] = []
        raw_parts = [part for part in package_name.replace("/", ":").split(":") if part]
        if raw_parts:
            artifact = self._normalize_token(raw_parts[-1])
            if artifact:
                candidates.append(artifact)

        normalized_whole = self._normalize_token(package_name)
        if normalized_whole:
            candidates.append(normalized_whole)

        for part in raw_parts:
            normalized = self._normalize_token(part)
            if normalized and normalized not in candidates and len(normalized) >= 6:
                candidates.append(normalized)

        return candidates

    def _looks_like_support_context_file(self, file_path: Optional[str]) -> bool:
        """Heuristic gate for deployment/support/config artifacts."""
        if not file_path:
            return False

        lowered = file_path.lower()
        markers = (
            "group_vars/",
            "host_vars/",
            "plays/",
            "templates/",
            "roles/",
            "deploy",
            "stage-",
            ".yml",
            ".yaml",
            ".ini",
            ".tf",
            ".j2",
            ".properties",
            ".conf",
            "docker-compose",
        )
        return any(marker in lowered for marker in markers)

    def _path_match_tokens(self, file_path: Optional[str]) -> set[str]:
        """Extract stable matching tokens from a support/config path."""
        if not file_path:
            return set()

        lowered = file_path.lower()
        raw_parts = [
            part
            for part in lowered.replace("/", " ").replace(".", " ").replace("_", " ").replace("-", " ").split()
            if part
        ]
        basename = lowered.split("/")[-1]
        basename_parts = [part for part in basename.replace(".", " ").replace("_", " ").replace("-", " ").split() if part]

        tokens = {self._normalize_token(part) for part in raw_parts}
        if basename_parts:
            tokens.add(self._normalize_token("".join(basename_parts)))
        return {token for token in tokens if len(token) >= 6}

    def _match_path_tokens_to_repository(
        self,
        *,
        path_tokens: set[str],
        repo: RepositoryIdentity,
    ) -> tuple[Optional[str], float]:
        """Match support/config path tokens to repository aliases."""
        heuristic_stop_tokens = {"domain", "server", "config", "service"}
        for candidate in sorted(self._repository_match_tokens(repo), key=len, reverse=True):
            if candidate in heuristic_stop_tokens:
                continue
            if candidate in path_tokens:
                confidence = 0.68 if len(candidate) >= 10 else 0.61
                return candidate, confidence
        return None, 0.0

    def _normalize_token(self, value: Optional[str]) -> str:
        """Normalize repository/package tokens for loose matching."""
        if not value:
            return ""
        return "".join(ch for ch in value.lower() if ch.isalnum())

    def _to_identity(self, repo: Repository) -> RepositoryIdentity:
        """Convert ORM repository row to identity dataclass."""
        return RepositoryIdentity(
            id=repo.id,
            name=repo.name,
            path_with_namespace=repo.path_with_namespace,
        )
