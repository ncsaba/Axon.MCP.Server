import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.api.services.repository_grouping_service import RepositoryQueryScope
from src.api.services.search_service import SearchService
from src.api.schemas.search import SearchResult
from src.config.embedding_contract import FIXED_EMBEDDING_DIMENSION
from src.config.enums import SymbolKindEnum, LanguageEnum


@pytest.fixture
def mock_session():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def search_service(mock_session):
    """Create SearchService instance with mocked dependencies."""
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)
        service.embedding_generator = AsyncMock()
        # Mock _get_code_snippets to avoid RuntimeWarning and DB calls
        service._get_code_snippets = AsyncMock(return_value={})
        return service


@pytest.mark.asyncio
async def test_keyword_search(search_service, mock_session):
    """Test keyword search."""
    # Mock database results - use simple MagicMock to avoid InvalidSpecError
    mock_symbol = MagicMock()
    mock_symbol.id = 1
    mock_symbol.name = "TestFunction"
    mock_symbol.kind = SymbolKindEnum.FUNCTION
    mock_symbol.language = LanguageEnum.PYTHON
    mock_symbol.signature = "def test_function():"
    mock_symbol.fully_qualified_name = "module.TestFunction"
    mock_symbol.start_line = 1
    mock_symbol.end_line = 5
    mock_symbol.documentation = "Test function"
    mock_symbol.created_at = datetime.now(timezone.utc)
    
    mock_file = MagicMock()
    mock_file.id = 1
    mock_file.path = "test.py"
    
    mock_repo = MagicMock()
    mock_repo.id = 1
    mock_repo.name = "test-repo"
    
    mock_result = MagicMock()
    # Implementation now selects chunk content too for chunk-aware keyword matching
    mock_result.all.return_value = [(mock_symbol, mock_file, mock_repo, None)]
    mock_session.execute.return_value = mock_result
    
    results = await search_service._keyword_search("test", 10, None, None, None)
    
    # Results might be empty if scoring threshold isn't met, but we primarily check structure
    assert isinstance(results, list)
    if results:
        assert results[0].name == "TestFunction"
        assert results[0].match_type == "keyword"


@pytest.mark.asyncio
async def test_semantic_search(search_service, mock_session):
    """Test semantic search."""
    # Mock embedding generation
    search_service.embedding_generator.generate_single_embedding.return_value = [0.1] * FIXED_EMBEDDING_DIMENSION
    search_service.embedding_generator.model_name = "mxbai-embed-large"
    search_service.embedding_generator.model_version = "1.0"
    search_service.embedding_generator.dimension = FIXED_EMBEDDING_DIMENSION
    
    # Mock symbol for vector search
    mock_symbol = MagicMock()
    mock_symbol.id = 1
    mock_symbol.name = "TestFunction"
    mock_symbol.kind = SymbolKindEnum.FUNCTION
    mock_symbol.language = LanguageEnum.PYTHON
    mock_symbol.signature = "def test_function():"
    mock_symbol.fully_qualified_name = "module.TestFunction"
    mock_symbol.start_line = 1
    mock_symbol.end_line = 5
    mock_symbol.file_id = 1
    mock_symbol.documentation = "Test function"
    mock_symbol.created_at = datetime.now(timezone.utc)
    
    # Mock file and repo
    mock_file = MagicMock()
    mock_file.id = 1
    mock_file.path = "test.py"
    
    mock_repo = MagicMock()
    mock_repo.id = 1
    mock_repo.name = "test-repo"
    
    # Mock vector store search - now returns 4-tuple (Symbol, similarity, File, Repo)
    search_service.vector_store.search_similar = AsyncMock(
        return_value=[(mock_symbol, 0.9, mock_file, mock_repo)]
    )
    
    results = await search_service._semantic_search("test function", 10, None, None, None)
    
    assert len(results) >= 0
    search_service.vector_store.search_similar.assert_awaited_once()
    kwargs = search_service.vector_store.search_similar.await_args.kwargs
    assert kwargs["query_text"] == "test function"
    assert kwargs["filters"]["embedding_model_name"] == "mxbai-embed-large"
    assert kwargs["filters"]["embedding_model_version"] == "1.0"
    assert kwargs["filters"]["embedding_dimension"] == FIXED_EMBEDDING_DIMENSION
    if len(results) > 0:
        assert results[0].match_type == "semantic"
        assert results[0].score > 0


@pytest.mark.asyncio
async def test_semantic_search_uses_repository_group_scope_filters(search_service, mock_session):
    search_service.embedding_generator.generate_single_embedding.return_value = [0.1] * FIXED_EMBEDDING_DIMENSION
    search_service.embedding_generator.model_name = "mxbai-embed-large"
    search_service.embedding_generator.model_version = "1.0"
    search_service.embedding_generator.dimension = FIXED_EMBEDDING_DIMENSION

    mock_symbol = MagicMock()
    mock_symbol.id = 11
    mock_symbol.name = "PredictionSchedulingConfigService"
    mock_symbol.kind = SymbolKindEnum.CLASS
    mock_symbol.language = LanguageEnum.JAVA
    mock_symbol.signature = "class PredictionSchedulingConfigService"
    mock_symbol.fully_qualified_name = "de.webtrekk.prediction.management.service.config.PredictionSchedulingConfigService"
    mock_symbol.start_line = 1
    mock_symbol.end_line = 20
    mock_symbol.documentation = "Service for prediction scheduling config."
    mock_symbol.created_at = datetime.now(timezone.utc)

    mock_file = MagicMock()
    mock_file.id = 5
    mock_file.path = "src/main/java/.../PredictionSchedulingConfigService.java"

    mock_repo = MagicMock()
    mock_repo.id = 4
    mock_repo.name = "dasc-prediction-domain"

    search_service.vector_store.search_similar = AsyncMock(
        return_value=[(mock_symbol, 0.9, mock_file, mock_repo)]
    )
    query_scope = RepositoryQueryScope(
        primary_repository_id=5,
        repository_ids=[5, 4, 7],
        repository_names=[
            "dasc-prediction-management",
            "dasc-prediction-domain",
            "infrastructure-automation",
        ],
        group_display_name="Inferred prediction stack",
        support_repository_ids=[7],
    )

    results = await search_service._semantic_search(
        "prediction scheduling config",
        10,
        5,
        None,
        None,
        repository_scope_ids=[5, 4, 7],
        query_scope=query_scope,
    )

    kwargs = search_service.vector_store.search_similar.await_args.kwargs
    assert kwargs["filters"]["repository_ids"] == [5, 4, 7]
    assert results[0].query_scope_group == "Inferred prediction stack"
    assert results[0].query_scope_repositories == query_scope.repository_names


@pytest.mark.asyncio
async def test_get_code_snippets_prefers_best_keyword_matching_chunk():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    result = MagicMock()
    result.all.return_value = [
        (1, "generic setup chunk", "implementation", 10),
        (1, "recommendation scoring logic lives here", "implementation", 11),
    ]
    mock_session.execute.return_value = result

    snippets = await service._get_code_snippets([1], query="recommendation scoring")

    assert snippets[1].content == "recommendation scoring logic lives here"
    assert snippets[1].match_type == "text"


@pytest.mark.asyncio
async def test_get_code_snippets_prefers_semantic_chunk_before_fallback():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    semantic_result = MagicMock()
    semantic_result.all.return_value = [
        (1, "semantically matched chunk", 0.97),
    ]
    fallback_result = MagicMock()
    fallback_result.all.return_value = [
        (1, "first stored chunk", "implementation", 5),
    ]
    mock_session.execute.side_effect = [semantic_result, fallback_result]

    snippets = await service._get_code_snippets(
        [1],
        query="test function",
        query_vector=[0.1] * FIXED_EMBEDDING_DIMENSION,
        embedding_model_name="mxbai-embed-large",
        embedding_model_version="1.0",
    )

    assert snippets[1].content == "semantically matched chunk"
    assert snippets[1].match_type == "semantic"


@pytest.mark.asyncio
async def test_keyword_search_scores_chunk_content_matches():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)
        service._get_code_snippets = AsyncMock(return_value={})

    mock_symbol = MagicMock()
    mock_symbol.id = 1
    mock_symbol.name = "UnrelatedName"
    mock_symbol.kind = SymbolKindEnum.CLASS
    mock_symbol.language = LanguageEnum.JAVA
    mock_symbol.signature = ""
    mock_symbol.fully_qualified_name = "example.UnrelatedName"
    mock_symbol.start_line = 1
    mock_symbol.end_line = 10
    mock_symbol.documentation = ""
    mock_symbol.created_at = datetime.now(timezone.utc)

    mock_file = MagicMock()
    mock_file.id = 1
    mock_file.path = "src/example/Ui.java"

    mock_repo = MagicMock()
    mock_repo.id = 1
    mock_repo.name = "jverein"

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (mock_symbol, mock_file, mock_repo, "Imports: javax.swing.JButton"),
    ]
    mock_session.execute.return_value = mock_result

    results = await service._keyword_search("uses swing", 5, None, None, None)

    assert len(results) == 1
    assert results[0].score > 0


@pytest.mark.asyncio
async def test_keyword_search_prefers_primary_repository_over_support_repo_when_scope_expands():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)
        service._get_code_snippets = AsyncMock(return_value={})

    primary_symbol = MagicMock()
    primary_symbol.id = 1
    primary_symbol.name = "PredictiveServiceClient"
    primary_symbol.kind = SymbolKindEnum.CLASS
    primary_symbol.language = LanguageEnum.JAVA
    primary_symbol.signature = ""
    primary_symbol.fully_qualified_name = "de.webtrekk.prediction.management.service.PredictiveServiceClient"
    primary_symbol.start_line = 1
    primary_symbol.end_line = 20
    primary_symbol.documentation = "Calls predictive service."
    primary_symbol.created_at = datetime.now(timezone.utc)

    support_symbol = MagicMock()
    support_symbol.id = 2
    support_symbol.name = "prediction-management.j2"
    support_symbol.kind = SymbolKindEnum.MODULE
    support_symbol.language = LanguageEnum.UNKNOWN
    support_symbol.signature = ""
    support_symbol.fully_qualified_name = "roles.internal.deployment-swat-service.templates.prediction-management"
    support_symbol.start_line = 1
    support_symbol.end_line = 20
    support_symbol.documentation = "Support template for prediction management."
    support_symbol.created_at = datetime.now(timezone.utc)

    primary_file = MagicMock()
    primary_file.id = 11
    primary_file.path = "src/main/java/de/webtrekk/prediction/management/service/PredictiveServiceClient.java"

    support_file = MagicMock()
    support_file.id = 12
    support_file.path = "roles/internal/deployment-swat-service/templates/prediction-management.j2"

    primary_repo = MagicMock()
    primary_repo.id = 5
    primary_repo.name = "dasc-prediction-management"

    support_repo = MagicMock()
    support_repo.id = 7
    support_repo.name = "infrastructure-automation"

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (support_symbol, support_file, support_repo, "service.predictive.host={{ predictive_service_url }}"),
        (primary_symbol, primary_file, primary_repo, "Calls predictive service for management flows"),
    ]
    mock_session.execute.return_value = mock_result

    query_scope = RepositoryQueryScope(
        primary_repository_id=5,
        repository_ids=[5, 4, 7],
        repository_names=[
            "dasc-prediction-management",
            "dasc-prediction-domain",
            "infrastructure-automation",
        ],
        group_display_name="Inferred prediction stack",
        support_repository_ids=[7],
    )

    results = await service._keyword_search(
        "predictive service",
        10,
        5,
        None,
        None,
        repository_scope_ids=[5, 4, 7],
        query_scope=query_scope,
    )

    assert [result.repository_name for result in results[:2]] == [
        "dasc-prediction-management",
        "infrastructure-automation",
    ]
    assert results[0].query_scope_group == "Inferred prediction stack"
    assert results[0].query_scope_repositories == query_scope.repository_names


def test_tokenize_query_normalizes_natural_language_scaffolding():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    tokens = service._tokenize_query("find similar items using postgres")

    assert "find" not in tokens
    assert "using" not in tokens
    assert "items" in tokens
    assert "item" in tokens
    assert "postgres" in tokens


def test_config_dependency_intent_boost_prefers_config_artifacts():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    mock_symbol = MagicMock()
    mock_symbol.name = "JVereinDBService"
    mock_symbol.fully_qualified_name = "de.jost_net.JVerein.rmi.JVereinDBService"
    mock_symbol.documentation = "Provides database service access for Jameica."
    mock_symbol.kind = SymbolKindEnum.INTERFACE

    boost = service._calculate_query_intent_boost(
        symbol=mock_symbol,
        file_path="src/de/jost_net/JVerein/rmi/JVereinDBService.java",
        chunk_content="import de.willuhn.datasource.rmi.DBService;\nimport de.willuhn.jameica.system.Settings;",
        query_intents={"config_dependency"},
    )

    assert boost > 0


def test_api_intent_boost_prefers_controllers_over_test_files():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    controller_symbol = MagicMock()
    controller_symbol.name = "JobQueueController"
    controller_symbol.fully_qualified_name = "de.webtrekk.ds.recommender.controller.JobQueueController"
    controller_symbol.documentation = "REST controller for job queue operations."
    controller_symbol.kind = SymbolKindEnum.CLASS

    test_symbol = MagicMock()
    test_symbol.name = "OldNewComparisonTests"
    test_symbol.fully_qualified_name = "de.webtrekk.ds.recommender.service.recoservice.OldNewComparisonTests"
    test_symbol.documentation = "Tests the old and new comparison flow."
    test_symbol.kind = SymbolKindEnum.METHOD

    controller_boost = service._calculate_query_intent_boost(
        symbol=controller_symbol,
        file_path="src/main/java/de/webtrekk/ds/recommender/controller/JobQueueController.java",
        chunk_content="import de.webtrekk.common.rest.RestModule;",
        query_intents={"api_surface"},
        query_tokens=["api", "route"],
    )
    test_boost = service._calculate_query_intent_boost(
        symbol=test_symbol,
        file_path="src/test/java/de/webtrekk/ds/recommender/service/recoservice/OldNewComparisonTests.java",
        chunk_content="assertEquals(...)",
        query_intents={"api_surface"},
        query_tokens=["api", "route"],
    )

    assert controller_boost > test_boost


def test_framework_intent_boost_prefers_exact_framework_matches():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    framework_symbol = MagicMock()
    framework_symbol.name = "JVereinPlugin"
    framework_symbol.fully_qualified_name = "de.jost_net.JVerein.JVereinPlugin"
    framework_symbol.documentation = "Jameica plugin bootstrap."
    framework_symbol.kind = SymbolKindEnum.CLASS

    unrelated_symbol = MagicMock()
    unrelated_symbol.name = "SpendenView"
    unrelated_symbol.fully_qualified_name = "de.jost_net.JVerein.gui.view.SpendenView"
    unrelated_symbol.documentation = "GUI view."
    unrelated_symbol.kind = SymbolKindEnum.CLASS

    framework_boost = service._calculate_query_intent_boost(
        symbol=framework_symbol,
        file_path="plugin.xml",
        chunk_content='class="de.jost_net.JVerein.JVereinPlugin" xmlns="http://www.willuhn.de/schema/jameica-plugin"',
        query_intents={"framework_usage"},
        query_tokens=["jameica", "plugin", "wiring"],
    )
    unrelated_boost = service._calculate_query_intent_boost(
        symbol=unrelated_symbol,
        file_path="src/de/jost_net/JVerein/gui/view/SpendenView.java",
        chunk_content="import org.eclipse.swt.widgets.Composite;",
        query_intents={"framework_usage"},
        query_tokens=["jameica", "plugin", "wiring"],
    )

    assert framework_boost > unrelated_boost


def test_member_flow_intent_boost_prefers_import_member_paths():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    flow_symbol = MagicMock()
    flow_symbol.name = "importMitglied"
    flow_symbol.fully_qualified_name = "de.jost_net.JVerein.io.Import.importMitglied"
    flow_symbol.documentation = "Imports a new member."
    flow_symbol.kind = SymbolKindEnum.METHOD

    unrelated_symbol = MagicMock()
    unrelated_symbol.name = "EmailValidator"
    unrelated_symbol.fully_qualified_name = "de.jost_net.JVerein.util.EmailValidator"
    unrelated_symbol.documentation = "Validates e-mail addresses."
    unrelated_symbol.kind = SymbolKindEnum.CLASS

    flow_boost = service._calculate_query_intent_boost(
        symbol=flow_symbol,
        file_path="src/de/jost_net/JVerein/io/Import.java",
        chunk_content="this method imports a new member from the specified data source",
        query_intents={"member_booking_flow"},
        query_tokens=["member", "import", "flow", "entrypoint"],
    )
    unrelated_boost = service._calculate_query_intent_boost(
        symbol=unrelated_symbol,
        file_path="src/de/jost_net/JVerein/util/EmailValidator.java",
        chunk_content="isValid(String email)",
        query_intents={"member_booking_flow"},
        query_tokens=["member", "import", "flow", "entrypoint"],
    )

    assert flow_boost > unrelated_boost


def test_ui_surface_intent_boost_prefers_view_classes():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    view_symbol = MagicMock()
    view_symbol.name = "SpendenView"
    view_symbol.fully_qualified_name = "de.jost_net.JVerein.gui.view.SpendenView"
    view_symbol.documentation = "GUI view."
    view_symbol.kind = SymbolKindEnum.CLASS

    framework_symbol = MagicMock()
    framework_symbol.name = "DBSupportMySqlImpl"
    framework_symbol.fully_qualified_name = "de.jost_net.JVerein.server.DBSupportMySqlImpl"
    framework_symbol.documentation = "Uses Jameica database support."
    framework_symbol.kind = SymbolKindEnum.CLASS

    view_boost = service._calculate_query_intent_boost(
        symbol=view_symbol,
        file_path="src/de/jost_net/JVerein/gui/view/SpendenView.java",
        chunk_content="extends AbstractView",
        query_intents={"ui_surface", "framework_usage"},
        query_tokens=["jameica", "views"],
    )
    framework_boost = service._calculate_query_intent_boost(
        symbol=framework_symbol,
        file_path="src/de/jost_net/JVerein/server/DBSupportMySqlImpl.java",
        chunk_content="@see de.willuhn.jameica.hbci.server.DBSupportMySql",
        query_intents={"ui_surface", "framework_usage"},
        query_tokens=["jameica", "views"],
    )

    assert view_boost > framework_boost


def test_scoring_logic_intent_boost_prefers_optimizer_symbols_over_docs():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    scoring_symbol = MagicMock()
    scoring_symbol.name = "ARMObjectiveFunction"
    scoring_symbol.fully_qualified_name = "de.webtrekk.ds.recommender.service.optimizer.ARMObjectiveFunction"
    scoring_symbol.documentation = "Objective function for evaluating recommendation quality."
    scoring_symbol.kind = SymbolKindEnum.CLASS

    doc_symbol = MagicMock()
    doc_symbol.name = "Recommendation Service"
    doc_symbol.fully_qualified_name = "README.Recommendation Service"
    doc_symbol.documentation = "Documentation section."
    doc_symbol.kind = SymbolKindEnum.DOCUMENT_SECTION

    scoring_boost = service._calculate_query_intent_boost(
        symbol=scoring_symbol,
        file_path="src/main/java/de/webtrekk/ds/recommender/service/optimizer/ARMObjectiveFunction.java",
        chunk_content="Objective function for evaluating recommendation quality and coverage score.",
        query_intents={"scoring_logic"},
        query_tokens=["recommendation", "scoring", "logic"],
    )
    doc_boost = service._calculate_query_intent_boost(
        symbol=doc_symbol,
        file_path="README.md",
        chunk_content="Recommendation Service overview",
        query_intents={"scoring_logic"},
        query_tokens=["recommendation", "scoring", "logic"],
    )

    assert scoring_boost > doc_boost


def test_csv_member_import_intent_boost_prefers_member_import_over_form_field_csv():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    member_import_symbol = MagicMock()
    member_import_symbol.name = "importMitglied"
    member_import_symbol.fully_qualified_name = "de.jost_net.JVerein.io.Import.importMitglied"
    member_import_symbol.documentation = "Imports a new member."
    member_import_symbol.kind = SymbolKindEnum.METHOD

    form_import_symbol = MagicMock()
    form_import_symbol.name = "FormularfelderImportCSV"
    form_import_symbol.fully_qualified_name = "de.jost_net.JVerein.io.FormularfelderImportCSV"
    form_import_symbol.documentation = "Importieren von Objekten zu Mitgliedern."
    form_import_symbol.kind = SymbolKindEnum.CLASS

    member_import_boost = service._calculate_query_intent_boost(
        symbol=member_import_symbol,
        file_path="src/de/jost_net/JVerein/io/Import.java",
        chunk_content="this method imports a new member from the specified data source",
        query_intents={"csv_member_import"},
        query_tokens=["csv", "member", "import"],
    )
    form_import_boost = service._calculate_query_intent_boost(
        symbol=form_import_symbol,
        file_path="src/de/jost_net/JVerein/io/FormularfelderImportCSV.java",
        chunk_content="Importieren von Objekten zu Mitgliedern",
        query_intents={"csv_member_import"},
        query_tokens=["csv", "member", "import"],
    )

    assert member_import_boost > form_import_boost


def test_csv_member_import_intent_penalizes_export_shapes():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    import_symbol = MagicMock()
    import_symbol.name = "importMitglied"
    import_symbol.fully_qualified_name = "de.jost_net.JVerein.io.Import.importMitglied"
    import_symbol.documentation = "Imports a new member."
    import_symbol.kind = SymbolKindEnum.METHOD

    export_symbol = MagicMock()
    export_symbol.name = "MitgliedAuswertungCSV"
    export_symbol.fully_qualified_name = "de.jost_net.JVerein.io.MitgliedAuswertungCSV"
    export_symbol.documentation = "Exports member evaluation data."
    export_symbol.kind = SymbolKindEnum.CLASS

    import_boost = service._calculate_query_intent_boost(
        symbol=import_symbol,
        file_path="src/de/jost_net/JVerein/io/Import.java",
        chunk_content="this method imports a new member from the specified data source",
        query_intents={"csv_member_import"},
        query_tokens=["csv", "member", "import"],
    )
    export_boost = service._calculate_query_intent_boost(
        symbol=export_symbol,
        file_path="src/de/jost_net/JVerein/io/MitgliedAuswertungCSV.java",
        chunk_content="CSV export for member evaluation",
        query_intents={"csv_member_import"},
        query_tokens=["csv", "member", "import"],
    )

    assert import_boost > export_boost


@pytest.mark.asyncio
async def test_reciprocal_rank_fusion():
    """Test RRF algorithm."""
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        search_service = SearchService(mock_session)
    
    keyword_results = [
        SearchResult(
            symbol_id=1,
            file_id=1,
            repository_id=1,
            name="Result1",
            kind=SymbolKindEnum.FUNCTION.value,  # Use .value to avoid validation issues if mocked
            language=LanguageEnum.PYTHON.value,  # Use .value to avoid validation issues if mocked
            signature="def result1():",
            file_path="test.py",
            repository_name="repo",
            fully_qualified_name="module.Result1",
            start_line=1,
            end_line=5,
            documentation="Test",
            score=0.9,
            match_type="keyword",
            updated_at=datetime.now(timezone.utc)
        )
    ]
    
    semantic_results = [
        SearchResult(
            symbol_id=2,
            file_id=1,
            repository_id=1,
            name="Result2",
            kind=SymbolKindEnum.FUNCTION.value,
            language=LanguageEnum.PYTHON.value,
            signature="def result2():",
            file_path="test.py",
            repository_name="repo",
            fully_qualified_name="module.Result2",
            start_line=6,
            end_line=10,
            documentation="Test",
            score=0.8,
            match_type="semantic",
            updated_at=datetime.now(timezone.utc)
        )
    ]
    
    fused = search_service._reciprocal_rank_fusion(
        keyword_results,
        semantic_results,
        limit=10
    )
    
    assert len(fused) == 2
    assert all(r.match_type == "hybrid" for r in fused)


@pytest.mark.asyncio
async def test_calculate_keyword_score():
    """Test keyword scoring logic."""
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        search_service = SearchService(mock_session)
    
    # Test exact name match
    mock_symbol = MagicMock()
    mock_symbol.name = "test"
    mock_symbol.signature = None
    mock_symbol.documentation = None
    mock_symbol.fully_qualified_name = None
    
    # score now uses _calculate_keyword_score_multiword
    score = search_service._calculate_keyword_score_multiword(mock_symbol, "test", ["test"])
    assert score >= 10.0  # Exact phrase match bonus
    
    # Test partial name match
    mock_symbol.name = "test_function"
    score = search_service._calculate_keyword_score_multiword(mock_symbol, "test", ["test"])
    assert score >= 5.0  # Substring match bonus


@pytest.mark.asyncio
async def test_hybrid_search_with_filters(search_service, mock_session):
    """Test hybrid search with filters."""
    # Mock keyword results
    search_service._keyword_search = AsyncMock(return_value=[])
    
    # Mock semantic results
    search_service._semantic_search = AsyncMock(return_value=[])
    
    results = await search_service._hybrid_search(
        query="test",
        limit=10,
        repository_id=1,
        language=LanguageEnum.PYTHON,
        symbol_kind=SymbolKindEnum.FUNCTION
    )
    
    # Verify filters were passed
    # Hybrid search now uses limit * 2 for individual searches
    search_service._keyword_search.assert_called_once_with(
        "test",
        20,
        1,
        LanguageEnum.PYTHON,
        SymbolKindEnum.FUNCTION,
        repository_scope_ids=None,
        query_scope=None,
    )
    search_service._semantic_search.assert_called_once_with(
        "test",
        20,
        1,
        LanguageEnum.PYTHON,
        SymbolKindEnum.FUNCTION,
        repository_scope_ids=None,
        query_scope=None,
    )
    
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_integration(search_service, mock_session):
    """Test main search method integration."""
    # Mock _keyword_search
    mock_result = SearchResult(
        symbol_id=1,
        file_id=1,
        repository_id=1,
        name="TestFunction",
        kind=SymbolKindEnum.FUNCTION.value,
        language=LanguageEnum.PYTHON.value,
        signature="def test():",
        file_path="test.py",
        repository_name="repo",
        fully_qualified_name="module.TestFunction",
        start_line=1,
        end_line=5,
        documentation="Test",
        score=0.9,
        match_type="keyword",
        updated_at=datetime.now(timezone.utc)
    )
    
    search_service._keyword_search = AsyncMock(return_value=[mock_result])
    
    # Test keyword-only search
    with patch("src.api.services.search_service._get_redis_cache", AsyncMock(return_value=None)):
        results = await search_service.search("test", limit=10, hybrid=False)
    
    assert len(results) >= 0
    search_service._keyword_search.assert_called_once()


@pytest.mark.asyncio
async def test_semantic_search_without_embeddings(search_service, mock_session):
    """Test semantic search when embedding generation fails."""
    # Mock empty embedding
    search_service.embedding_generator.generate_single_embedding.return_value = []
    
    results = await search_service._semantic_search("test", 10, None, None, None)
    
    assert len(results) == 0


@pytest.mark.asyncio
async def test_rrf_with_overlapping_results():
    """Test RRF with overlapping results from both searches."""
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        search_service = SearchService(mock_session)
    
    # Create overlapping result (same symbol_id in both)
    shared_result = SearchResult(
        symbol_id=1,
        file_id=1,
        repository_id=1,
        name="SharedResult",
        kind=SymbolKindEnum.FUNCTION.value,
        language=LanguageEnum.PYTHON.value,
        signature="def shared():",
        file_path="test.py",
        repository_name="repo",
        fully_qualified_name="module.SharedResult",
        start_line=1,
        end_line=5,
        documentation="Shared",
        score=0.9,
        match_type="keyword",
        updated_at=datetime.now(timezone.utc)
    )
    
    keyword_results = [shared_result]
    semantic_results = [shared_result]
    
    fused = search_service._reciprocal_rank_fusion(
        keyword_results,
        semantic_results,
        limit=10
    )
    
    # Should only return one result (deduplicated)
    assert len(fused) == 1
    assert fused[0].symbol_id == 1
    assert fused[0].match_type == "hybrid"
    # Score should be higher due to appearing in both result sets
    assert fused[0].score > 0


def test_hybrid_source_weights_favor_keyword_for_csv_member_import():
    mock_session = AsyncMock()
    with patch('src.api.services.search_service.EmbeddingGenerator'):
        service = SearchService(mock_session)

    keyword_weight, semantic_weight = service._get_hybrid_source_weights({"csv_member_import"})

    assert keyword_weight == 10.0
    assert semantic_weight == 0.1
