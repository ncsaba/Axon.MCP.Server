import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from src.extractors.config_extractor import ConfigExtractor
from src.database.models import Repository, FileInstance as File
from src.config.enums import SourceControlProviderEnum

@pytest.mark.asyncio
async def test_config_extractor_types():
    # Mock session
    session = AsyncMock()
    
    # Mock repository
    repo = Repository(id=1, provider=SourceControlProviderEnum.GITLAB, path_with_namespace="test/repo")
    
    # extract_configuration makes 2 execute calls:
    # 1. DELETE(ConfigurationEntry) - to clear existing entries
    # 2. SELECT(File) - to get config files
    
    mock_result_delete = MagicMock()
    
    file_obj = File(id=1, path="appsettings.json", repository_id=1)
    mock_result_files = MagicMock()
    mock_result_files.scalars.return_value.all.return_value = [file_obj]
    
    session.execute.side_effect = [mock_result_delete, mock_result_files]
    
    # Mock the Path object for file operations
    # extract_configuration does: file_abs_path = repo_path / file.path
    mock_repo_path = MagicMock(spec=Path)
    
    # Create a mock for the constructed file path
    mock_file_path = MagicMock()
    mock_file_path.exists.return_value = True
    mock_file_path.read_text.return_value = '{"Integer": 301, "Boolean": true, "String": "text"}'
    
    # Mock the truediv operation to return our mock file path
    mock_repo_path.__truediv__.return_value = mock_file_path
    
    extractor = ConfigExtractor(session)
    await extractor.extract_configuration(1, mock_repo_path)
    
    # Verify session.add was called
    assert session.add.call_count == 3
    
    calls = session.add.call_args_list
    
    # Helper to find entry by key
    def get_entry(key):
        for call in calls:
            entry = call[0][0]
            if entry.config_key == key:
                return entry
        return None
        
    int_entry = get_entry("Integer")
    assert int_entry is not None
    assert int_entry.config_value == "301"
    assert int_entry.config_type == "number"
    assert isinstance(int_entry.config_value, str)
    
    bool_entry = get_entry("Boolean")
    assert bool_entry is not None
    assert bool_entry.config_value == "true"
    assert bool_entry.config_type == "boolean"
    assert isinstance(bool_entry.config_value, str)
    
    str_entry = get_entry("String")
    assert str_entry is not None
    assert str_entry.config_value == "text"
    assert str_entry.config_type == "string"
    assert isinstance(str_entry.config_value, str)
