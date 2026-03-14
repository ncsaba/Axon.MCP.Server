from src.utils.file_exclusion import FileExclusionRules


def test_should_exclude_directory_matches_directory_path_itself() -> None:
    rules = FileExclusionRules(custom_exclusions=["**/build/", "**/.git/**"])

    assert rules.should_exclude_directory("model/build") is True
    assert rules.should_exclude_directory(".git") is True
    assert rules.should_exclude_directory("src/main") is False
