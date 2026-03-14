#!/usr/bin/env python3
"""Benchmark the discovery-only file inventory path on a local repository.

This benchmark intentionally excludes metadata-gate, parsing, and queue sends.
It measures the current discovery semantics:

1. file inventory enumeration via the configured provider
2. discovery filters (gitignore, extension, size)
3. in-memory file list population for compatibility with the pipeline
4. batch payload construction that would precede queue emission

Use repeated runs on the same repository to estimate warm-cache effects.
The "first" run is only a cold-ish baseline; this script does not drop OS page cache.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.WARNING)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
    cache_logger_on_first_use=False,
)

from src.utils.file_exclusion import FileExclusionRules

_PROVIDER_SPEC = importlib.util.spec_from_file_location(
    "benchmark_file_inventory_provider",
    ROOT_DIR / "src" / "workers" / "file_inventory" / "provider.py",
)
if _PROVIDER_SPEC is None or _PROVIDER_SPEC.loader is None:
    raise RuntimeError("Failed to load file inventory provider module")
_PROVIDER_MODULE = importlib.util.module_from_spec(_PROVIDER_SPEC)
sys.modules[_PROVIDER_SPEC.name] = _PROVIDER_MODULE
_PROVIDER_SPEC.loader.exec_module(_PROVIDER_MODULE)

DEFAULT_DISCOVERY_EXTENSIONS = _PROVIDER_MODULE.DEFAULT_DISCOVERY_EXTENSIONS
FileMeta = _PROVIDER_MODULE.FileMeta
ScandirFileInventoryProvider = _PROVIDER_MODULE.ScandirFileInventoryProvider


@dataclass
class DiscoveryRunResult:
    run_index: int
    elapsed_seconds: float
    files_seen: int
    files_matched: int
    files_excluded: int
    directories_enumerated: int
    batches_constructed: int
    payload_records_built: int
    files_per_second_seen: float
    files_per_second_matched: float


def _build_exclusion_rules(repo_path: Path) -> FileExclusionRules:
    exclusion_rules = FileExclusionRules()
    gitignore_path = repo_path / ".gitignore"
    if gitignore_path.exists():
        gitignore_patterns = FileExclusionRules.parse_gitignore(gitignore_path)
        exclusion_rules = FileExclusionRules(custom_exclusions=gitignore_patterns)
    return exclusion_rules


def _serialize_file_meta(file_meta: FileMeta) -> dict[str, Any]:
    return {
        "rel_path": file_meta.rel_path,
        "size_bytes": file_meta.size_bytes,
        "mtime_ns": file_meta.mtime_ns,
        "kind": file_meta.kind,
    }


def run_discovery_benchmark(
    repo_path: Path,
    *,
    batch_size: int,
    max_file_size_mb: int,
) -> DiscoveryRunResult:
    exclusion_rules = _build_exclusion_rules(repo_path)
    file_size_limit_bytes = max_file_size_mb * 1024 * 1024
    suffixes = tuple(sorted(DEFAULT_DISCOVERY_EXTENSIONS))
    provider = ScandirFileInventoryProvider()

    def should_include(rel_path: str, size_bytes: int) -> bool:
        if not rel_path.lower().endswith(suffixes):
            return False
        if size_bytes > file_size_limit_bytes:
            return False
        return True

    files: list[Path] = []
    current_batch: list[FileMeta] = []
    batches_constructed = 0
    payload_records_built = 0

    started = time.perf_counter()
    for file_meta in provider.stream(
        repo_path,
        should_include=should_include,
        should_exclude=exclusion_rules.should_exclude,
    ):
        files.append(repo_path / file_meta.rel_path)
        current_batch.append(file_meta)

        if len(current_batch) >= batch_size:
            batches_constructed += 1
            payload_records_built += len([_serialize_file_meta(item) for item in current_batch])
            current_batch = []

    if current_batch:
        batches_constructed += 1
        payload_records_built += len([_serialize_file_meta(item) for item in current_batch])

    elapsed_seconds = time.perf_counter() - started
    files_seen = int(provider.files_seen)
    files_matched = len(files)
    files_excluded = max(0, files_seen - files_matched)

    return DiscoveryRunResult(
        run_index=0,
        elapsed_seconds=elapsed_seconds,
        files_seen=files_seen,
        files_matched=files_matched,
        files_excluded=files_excluded,
        directories_enumerated=int(provider.directories_enumerated),
        batches_constructed=batches_constructed,
        payload_records_built=payload_records_built,
        files_per_second_seen=(files_seen / elapsed_seconds) if elapsed_seconds > 0 else 0.0,
        files_per_second_matched=(files_matched / elapsed_seconds) if elapsed_seconds > 0 else 0.0,
    )


def _summarize_results(results: list[DiscoveryRunResult]) -> dict[str, Any]:
    first = results[0]
    warm = results[1:]
    summary: dict[str, Any] = {
        "first_run": asdict(first),
        "warm_runs": [asdict(item) for item in warm],
        "warm_summary": None,
        "cache_effect_estimate": None,
    }

    if warm:
        warm_elapsed = [item.elapsed_seconds for item in warm]
        warm_seen_rate = [item.files_per_second_seen for item in warm]
        warm_matched_rate = [item.files_per_second_matched for item in warm]
        warm_summary = {
            "runs": len(warm),
            "elapsed_seconds_avg": statistics.mean(warm_elapsed),
            "elapsed_seconds_min": min(warm_elapsed),
            "elapsed_seconds_max": max(warm_elapsed),
            "files_per_second_seen_avg": statistics.mean(warm_seen_rate),
            "files_per_second_matched_avg": statistics.mean(warm_matched_rate),
        }
        if len(warm_elapsed) > 1:
            warm_summary["elapsed_seconds_stdev"] = statistics.pstdev(warm_elapsed)
        summary["warm_summary"] = warm_summary

        first_elapsed = first.elapsed_seconds
        warm_avg_elapsed = warm_summary["elapsed_seconds_avg"]
        if first_elapsed > 0:
            summary["cache_effect_estimate"] = {
                "warm_vs_first_elapsed_delta_seconds": warm_avg_elapsed - first_elapsed,
                "warm_vs_first_elapsed_delta_percent": ((warm_avg_elapsed - first_elapsed) / first_elapsed) * 100.0,
                "warm_vs_first_seen_rate_delta_percent": (
                    (warm_summary["files_per_second_seen_avg"] - first.files_per_second_seen) / first.files_per_second_seen
                )
                * 100.0
                if first.files_per_second_seen > 0
                else 0.0,
            }

    return summary


def _print_human_summary(
    repo_path: Path,
    results: list[DiscoveryRunResult],
    summary: dict[str, Any],
    *,
    batch_size: int,
    max_file_size_mb: int,
) -> None:
    print(f"benchmark=discovery_only")
    print(f"repository={repo_path}")
    print(f"started_at={datetime.now(UTC).isoformat()}")
    print(f"runs={len(results)}")
    print(f"batch_size={batch_size}")
    print(f"max_file_size_mb={max_file_size_mb}")
    print("note=first run is only a cold-ish baseline; OS page cache is not dropped")
    print("")

    for result in results:
        print(
            "run="
            f"{result.run_index} "
            f"elapsed_s={result.elapsed_seconds:.6f} "
            f"files_seen={result.files_seen} "
            f"files_matched={result.files_matched} "
            f"dirs={result.directories_enumerated} "
            f"batches={result.batches_constructed} "
            f"seen_rate={result.files_per_second_seen:.2f} "
            f"matched_rate={result.files_per_second_matched:.2f}"
        )

    warm_summary = summary.get("warm_summary")
    if warm_summary:
        print("")
        print(
            "warm_avg="
            f"{warm_summary['elapsed_seconds_avg']:.6f}s "
            f"warm_min={warm_summary['elapsed_seconds_min']:.6f}s "
            f"warm_max={warm_summary['elapsed_seconds_max']:.6f}s "
            f"warm_seen_rate_avg={warm_summary['files_per_second_seen_avg']:.2f}"
        )
        cache_effect = summary.get("cache_effect_estimate") or {}
        print(
            "cache_effect="
            f"elapsed_delta_pct={cache_effect.get('warm_vs_first_elapsed_delta_percent', 0.0):.2f} "
            f"seen_rate_delta_pct={cache_effect.get('warm_vs_first_seen_rate_delta_percent', 0.0):.2f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the discovery-only file inventory path for a local repository.",
    )
    parser.add_argument("path", help="Repository root path to benchmark")
    parser.add_argument("--runs", type=int, default=6, help="Number of benchmark runs (default: %(default)s)")
    parser.add_argument("--batch-size", type=int, default=500, help="Discovery batch size to simulate")
    parser.add_argument(
        "--max-file-size-mb",
        type=int,
        default=10,
        help="Maximum file size filter to apply, matching discovery settings",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_path = Path(args.path).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        parser.error(f"Repository path does not exist or is not a directory: {repo_path}")

    runs = max(1, int(args.runs))
    batch_size = max(1, int(args.batch_size))
    max_file_size_mb = max(1, int(args.max_file_size_mb))

    results: list[DiscoveryRunResult] = []
    for run_index in range(1, runs + 1):
        result = run_discovery_benchmark(
            repo_path,
            batch_size=batch_size,
            max_file_size_mb=max_file_size_mb,
        )
        result.run_index = run_index
        results.append(result)

    summary = {
        "benchmark": "discovery_only",
        "repository": str(repo_path),
        "runs": runs,
        "batch_size": batch_size,
        "max_file_size_mb": max_file_size_mb,
        "note": "first run is only a cold-ish baseline; OS page cache is not dropped",
        **_summarize_results(results),
    }

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=False))
    else:
        _print_human_summary(
            repo_path,
            results,
            summary,
            batch_size=batch_size,
            max_file_size_mb=max_file_size_mb,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
