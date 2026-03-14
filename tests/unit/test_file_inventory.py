from pathlib import Path

from src.workers.file_inventory import ScandirFileInventoryProvider


def test_scandir_provider_streams_deterministic_file_meta(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("print('b')", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "src" / "skip.txt").write_text("skip", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")

    provider = ScandirFileInventoryProvider()

    records = list(
        provider.stream(
            tmp_path,
            should_include=lambda rel_path, _size: rel_path.endswith((".py", ".md")),
            should_exclude=lambda rel_path: rel_path == "src/b.py",
        )
    )

    rel_paths = [record.rel_path for record in records]
    assert rel_paths == ["README.md", "src/a.py"]
    assert provider.directories_enumerated >= 2
    assert provider.files_seen == 4
    assert all(record.size_bytes >= 0 for record in records)
    assert all(record.mtime_ns > 0 for record in records)


def test_scandir_provider_prunes_excluded_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.py").write_text("print('keep')", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generated.py").write_text("print('generated')", encoding="utf-8")

    provider = ScandirFileInventoryProvider()
    visited_directories: list[str] = []
    pruned_directories: list[str] = []

    records = list(
        provider.stream(
            tmp_path,
            should_include=lambda rel_path, _size: rel_path.endswith(".py"),
            should_exclude=lambda _rel_path: False,
            should_exclude_directory=lambda rel_path: rel_path == "build",
            on_directory_enter=visited_directories.append,
            on_directory_pruned=pruned_directories.append,
        )
    )

    assert [record.rel_path for record in records] == ["src/keep.py"]
    assert visited_directories == [".", "src"]
    assert pruned_directories == ["build"]
    assert provider.files_seen == 1
