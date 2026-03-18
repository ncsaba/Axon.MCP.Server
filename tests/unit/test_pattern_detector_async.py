"""Async tests for PatternDetector complex detection methods."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from src.extractors.pattern_detector import PatternDetector, Pattern
from src.database.models import Symbol, FileInstance as File, Relation
from src.config.enums import SymbolKindEnum, RelationTypeEnum, LanguageEnum


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    session = AsyncMock(spec=AsyncSession)
    return session


@pytest.mark.asyncio
async def test_god_class_detection_counts_correctly(mock_session):
    """Test that God Class correctly counts methods belonging to a class."""
    
    # Mock execute result for God Class query
    mock_result = MagicMock()
    # Simulate: ClassA has 50 methods (should be detected)
    mock_result.all.return_value = [
        (1, "HugeController", "Api.Controllers.HugeController", 10, 50)
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    detector = PatternDetector(mock_session)
    patterns = await detector._detect_god_class(repository_id=1)
    
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.pattern_name == "God Class"
    assert pattern.pattern_type == "anti_pattern"
    assert pattern.confidence == 1.0  # 50/50 = 1.0
    assert pattern.symbols == [1]
    assert "50 methods" in pattern.description


@pytest.mark.asyncio
async def test_god_class_no_detection_under_threshold(mock_session):
    """Test that classes with <= 30 methods are not detected."""
    
    # Mock execute result - class with only 25 methods
    mock_result = MagicMock()
    mock_result.all.return_value = []  # having() clause filters it out
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    detector = PatternDetector(mock_session)
    patterns = await detector._detect_god_class(repository_id=1)
    
    assert len(patterns) == 0


@pytest.mark.asyncio
async def test_circular_dependencies_detects_2_node_cycle(mock_session):
    """Test that 2-node cycles (A↔B) are still detected with DFS."""
    
    # Mock relations: Symbol 1 → Symbol 2 → Symbol 1
    rel1 = MagicMock(spec=Relation)
    rel1.from_symbol_id = 1
    rel1.to_symbol_id = 2
    
    rel2 = MagicMock(spec=Relation)
    rel2.from_symbol_id = 2
    rel2.to_symbol_id = 1
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [rel1, rel2]
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    detector = PatternDetector(mock_session)
    patterns = await detector._detect_circular_dependencies(repository_id=1)
    
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.pattern_name == "Circular Dependency"
    assert pattern.pattern_type == "anti_pattern"
    assert set(pattern.symbols) == {1, 2}
    assert "2-node cycle" in pattern.description


@pytest.mark.asyncio
async def test_circular_dependencies_detects_3_node_cycle(mock_session):
    """Test that multi-node cycles (A→B→C→A) are detected with DFS."""
    
    # Mock relations: Symbol 1 → Symbol 2 → Symbol 3 → Symbol 1
    rel1 = MagicMock(spec=Relation)
    rel1.from_symbol_id = 1
    rel1.to_symbol_id = 2
    
    rel2 = MagicMock(spec=Relation)
    rel2.from_symbol_id = 2
    rel2.to_symbol_id = 3
    
    rel3 = MagicMock(spec=Relation)
    rel3.from_symbol_id = 3
    rel3.to_symbol_id = 1
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [rel1, rel2, rel3]
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    detector = PatternDetector(mock_session)
    patterns = await detector._detect_circular_dependencies(repository_id=1)
    
    assert len(patterns) >= 1
    # Find the 3-node cycle
    three_node_cycles = [p for p in patterns if "3-node" in p.description]
    assert len(three_node_cycles) >= 1
    pattern = three_node_cycles[0]
    assert pattern.pattern_name == "Circular Dependency"
    assert set(pattern.symbols) == {1, 2, 3}


@pytest.mark.asyncio
async def test_circular_dependencies_no_cycles(mock_session):
    """Test that no cycles are detected in acyclic graph."""
    
    # Mock relations: Symbol 1 → Symbol 2 → Symbol 3 (no back edge)
    rel1 = MagicMock(spec=Relation)
    rel1.from_symbol_id = 1
    rel1.to_symbol_id = 2
    
    rel2 = MagicMock(spec=Relation)
    rel2.from_symbol_id = 2
    rel2.to_symbol_id = 3
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [rel1, rel2]
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    detector = PatternDetector(mock_session)
    patterns = await detector._detect_circular_dependencies(repository_id=1)
    
    assert len(patterns) == 0


@pytest.mark.asyncio
async def test_god_class_confidence_scaling(mock_session):
    """Test that God Class confidence scales correctly with method count."""
    
    # Test class with exactly 35 methods (35/50 = 0.7)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (1, "MediumController", "Api.Controllers.MediumController", 10, 35)
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    detector = PatternDetector(mock_session)
    patterns = await detector._detect_god_class(repository_id=1)
    
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.confidence == 0.7  # 35/50 = 0.7


@pytest.mark.asyncio
async def test_circular_dependencies_complex_graph(mock_session):
    """Test cycle detection in a more complex graph with multiple cycles."""
    
    # Graph: 1→2→3→1 (cycle) and 4→5→6 (no cycle) and 2→4 (connection)
    relations = [
        # Cycle 1
        MagicMock(spec=Relation, from_symbol_id=1, to_symbol_id=2),
        MagicMock(spec=Relation, from_symbol_id=2, to_symbol_id=3),
        MagicMock(spec=Relation, from_symbol_id=3, to_symbol_id=1),
        # Connection to acyclic part
        MagicMock(spec=Relation, from_symbol_id=2, to_symbol_id=4),
        # Acyclic chain
        MagicMock(spec=Relation, from_symbol_id=4, to_symbol_id=5),
        MagicMock(spec=Relation, from_symbol_id=5, to_symbol_id=6),
    ]
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = relations
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    detector = PatternDetector(mock_session)
    patterns = await detector._detect_circular_dependencies(repository_id=1)
    
    # Should detect the 1→2→3→1 cycle
    assert len(patterns) >= 1
    cycle_symbols_sets = [set(p.symbols) for p in patterns]
    assert {1, 2, 3} in cycle_symbols_sets
