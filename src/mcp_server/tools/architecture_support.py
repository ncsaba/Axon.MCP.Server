"""Shared infrastructure-support lookups for architecture-oriented tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import FileInstance as File, Repository
from src.database.query_helpers import active_file_filter


ARCHITECTURE_SUPPORT_REPOSITORY_NAMES = ("infrastructure-automation",)
_STOP_TOKENS = {
    "architecture",
    "architectural",
    "system",
    "repo",
    "repository",
    "service",
    "codebase",
    "structure",
    "module",
    "project",
    "question",
    "overview",
}


@dataclass(frozen=True)
class ArchitectureSupportMatch:
    """One infrastructure artifact relevant to an architecture question."""

    file_path: str
    score: int
    matched_terms: List[str]


async def get_architecture_support_repository(session: AsyncSession) -> Optional[Repository]:
    """Return the configured infrastructure-support repository when indexed."""
    result = await session.execute(
        select(Repository).where(Repository.name.in_(ARCHITECTURE_SUPPORT_REPOSITORY_NAMES))
    )
    return result.scalars().first()


def build_architecture_terms(*values: Optional[str]) -> List[str]:
    """Generate ranked search terms for infrastructure/support matching."""
    terms: list[str] = []
    for value in values:
        if not value:
            continue

        lowered = value.strip().lower()
        if not lowered:
            continue

        variants = {
            lowered,
            lowered.replace("_", "-"),
            lowered.replace("_", " "),
            lowered.replace("-", " "),
        }

        for variant in list(variants):
            if variant.startswith("dasc-"):
                variants.add(variant[len("dasc-") :])
            if variant.startswith("local/"):
                variants.add(variant.split("/", 1)[1])

        for variant in variants:
            normalized = re.sub(r"[^a-z0-9]+", " ", variant).strip()
            if normalized and normalized not in terms:
                terms.append(normalized)
            for token in normalized.split():
                if len(token) >= 4 and token not in _STOP_TOKENS and token not in terms:
                    terms.append(token)

    return terms[:12]


async def find_architecture_support_matches(
    session: AsyncSession,
    *,
    subject_text: str,
    limit: int = 5,
) -> tuple[Optional[Repository], List[ArchitectureSupportMatch]]:
    """Search infrastructure-automation for files relevant to a subject query."""
    support_repo = await get_architecture_support_repository(session)
    if support_repo is None:
        return None, []

    terms = build_architecture_terms(subject_text, support_repo.name)
    if not terms:
        return support_repo, []

    file_result = await session.execute(
        select(File.path)
        .where(File.repository_id == support_repo.id, active_file_filter())
    )

    matches: list[ArchitectureSupportMatch] = []
    seen_paths: set[str] = set()
    for raw_path in file_result.scalars().all():
        path = str(raw_path or "")
        lowered_path = path.lower()
        matched_terms = [term for term in terms if term and term in lowered_path]
        if not matched_terms:
            continue

        score = 0
        normalized_subject = re.sub(r"[^a-z0-9]+", "", subject_text.lower())
        normalized_path = re.sub(r"[^a-z0-9]+", "", lowered_path)
        if normalized_subject and normalized_subject in normalized_path:
            score += 8
        score += sum(3 if "/" not in term and "-" in lowered_path else 2 for term in matched_terms)
        score += max(0, 4 - min(4, lowered_path.count("/")))

        if path in seen_paths:
            continue
        seen_paths.add(path)
        matches.append(
            ArchitectureSupportMatch(
                file_path=path,
                score=score,
                matched_terms=matched_terms[:4],
            )
        )

    matches.sort(key=lambda item: (-item.score, item.file_path))
    return support_repo, matches[:limit]


def format_architecture_support_section(
    *,
    support_repo_name: str,
    subject_label: str,
    matches: Iterable[ArchitectureSupportMatch],
) -> str:
    """Render support-repository context as markdown."""
    match_list = list(matches)
    lines = [
        "## Infrastructure Automation Context\n\n",
        f"Queried `{support_repo_name}` for architecture context related to `{subject_label}`.\n\n",
    ]
    if not match_list:
        lines.append("No matching infrastructure artifacts were found.\n\n")
        return "".join(lines)

    for match in match_list:
        matched_terms = ", ".join(match.matched_terms)
        lines.append(f"- `{match.file_path}`")
        if matched_terms:
            lines.append(f" (matched: {matched_terms})")
        lines.append("\n")
    lines.append("\n")
    return "".join(lines)
