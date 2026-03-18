import json
import os
import sys
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Skip dotenv loading during tests for deterministic secure defaults."""

        class _ConditionalDotEnvSource(DotEnvSettingsSource):
            def __call__(self) -> dict[str, object]:
                is_pytest_process = "pytest" in sys.modules or any(
                    part.endswith("pytest") for part in sys.argv
                )
                if is_pytest_process or os.getenv("PYTEST_CURRENT_TEST"):
                    return {}

                env_name = (
                    os.getenv("environment")
                    or os.getenv("ENVIRONMENT")
                    or ""
                ).strip().lower()
                if env_name == "testing":
                    return {}

                return super().__call__()

        return (
            init_settings,
            env_settings,
            _ConditionalDotEnvSource(settings_cls),
            file_secret_settings,
        )

    # Application
    app_name: str = "Axon.MCP.Server"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"

    @field_validator("debug", mode="before")
    @classmethod
    def _normalize_debug_aliases(cls, value):
        """Accept the observed DEBUG=release environment variant as false."""
        if isinstance(value, str) and value.strip().lower() == "release":
            return False
        return value

    @staticmethod
    def _parse_listish(value):
        """Normalize JSON-array or comma-separated env input into a list."""
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return json.loads(raw)
            return [item.strip() for item in raw.split(",") if item.strip()]
        return value

    # GitLab
    gitlab_url: str = "https://gitlab.com"
    gitlab_token: str = ""
    gitlab_group_id: Optional[str] = None
    gitlab_webhook_secret: Optional[str] = None
    github_token: str = ""
    generic_git_username: Optional[str] = None
    generic_git_token: Optional[str] = None

    # Database
    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 40
    database_pool_timeout: int = 30
    database_echo: bool = False

    # Redis
    # Note: When running in Docker, use "redis://redis:6379/0" (service name)
    #       When running locally, use "redis://localhost:6379/0"
    #       Docker Compose will override this via environment variable
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 50
    redis_cache_enabled: bool = True  # Set to False to disable Redis caching entirely

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"
    celery_task_time_limit: int = 3600
    celery_task_soft_time_limit: int = 3000

    # Embeddings
    embedding_provider: str = "ollama"  # "local", "openai", or "ollama"
    openai_api_key: Optional[str] = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimension: int = 1536
    ollama_embedding_model: str = "mxbai-embed-large"
    
    # LLM Summarization (Phase 2)
    llm_provider: str = "openrouter"  # "openai" or "openrouter"
    llm_model: str = "gpt-oss:120b"  # Model to use for summarization
    openrouter_api_key: Optional[str] = None  # OpenRouter API key
    ollama_base_url: str = "http://localhost:11434/v1"  # Ollama base URL
    llm_request_timeout: int = 300  # Timeout in seconds (default 5 minutes)
    local_embedding_model: str = "sentence-transformers/all-mpnet-base-v2"
    embedding_batch_size: int = 100

    # Vector Store
    vector_store_type: str = "pgvector"
    vector_similarity_threshold: float = 0.7

    # MCP Server
    mcp_transport: str = "stdio"  # "stdio" or "http"
    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8001
    mcp_http_path: str = "/mcp"  # HTTP endpoint path

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_workers: int = 4
    api_secret_key: str = ""
    api_cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    api_rate_limit: int = 100

    # Security
    auth_enabled: bool = True  # Set to False for local dev
    admin_api_key: str = ""  # Main admin API key
    read_only_api_keys: list[str] = []  # List of read-only keys
    admin_password: str = ""  # Password for UI login (cookie-based)
    mcp_auth_enabled: bool = True  # Secure-by-default for MCP HTTP transport; override only for trusted local clients
    rest_auth_methods: list[str] = ["api_key", "local_jwt"]
    mcp_auth_methods: list[str] = ["api_key", "local_jwt"]

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7
    keycloak_issuer_url: Optional[str] = None
    keycloak_jwks_url: Optional[str] = None
    keycloak_audiences: list[str] = []
    keycloak_client_id: Optional[str] = None
    keycloak_client_secret: Optional[str] = None
    keycloak_redirect_uri: Optional[str] = None
    keycloak_scopes: list[str] = ["openid", "profile", "email"]
    keycloak_username_claim: str = "preferred_username"
    keycloak_role_claim: str = "realm_access.roles"
    keycloak_admin_roles: list[str] = ["admin"]
    keycloak_read_only_roles: list[str] = ["readonly"]
    keycloak_default_role: str = "readonly"

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # Repository Management
    repo_cache_dir: str = "./cache/repos"
    repo_max_size_mb: int = 1000
    repo_cleanup_days: int = 7
    repository_poll_enabled: bool = True
    repository_poll_interval_minutes: int = 15

    # Parsing
    parse_timeout_seconds: int = 300
    parse_max_file_size_mb: int = 10
    inventory_batch_size: int = 500
    inventory_max_inflight_batches: int = 8
    inventory_emit_enabled: bool = True
    inventory_queue_name: str = "discovery_inventory"
    metadata_gate_enabled: bool = True
    metadata_gate_hash_fallback_enabled: bool = True
    metadata_gate_idempotency_ttl_seconds: int = 86400
    metadata_gate_inline_parse_enabled: bool = False
    metadata_gate_parse_enqueue_chunk_size: int = 100
    parse_task_wait_timeout_seconds: int = 7200
    parse_task_wait_poll_seconds: float = 0.5
    file_instance_missing_ttl_days: int = 7
    file_instance_cleanup_batch_size: int = 1000

    # Extraction (automated during sync)
    extract_api_endpoints: bool = True  # Extract API endpoints automatically
    extract_imports: bool = True  # Resolve import relationships automatically
    build_call_graph: bool = True  # Build call graph relationships (can be slow)
    detect_patterns: bool = False  # Detect design patterns (optional, can be slow)
    extract_dependencies: bool = True  # Extract package dependencies (npm, Python)
    extract_configuration: bool = True  # Extract configuration from appsettings.json, etc.

    # Hierarchical Service Detection (for DDD architecture visibility)
    detect_library_services: bool = True  # Detect class libraries as services for hierarchical exploration
    min_library_symbols: int = 10  # Minimum symbols required to detect a library as a service

    # Monitoring
    metrics_enabled: bool = True
    metrics_port: int = 9090
    tracing_enabled: bool = False
    tracing_endpoint: Optional[str] = None

    @field_validator(
        "read_only_api_keys",
        "rest_auth_methods",
        "mcp_auth_methods",
        "keycloak_audiences",
        "keycloak_scopes",
        "keycloak_admin_roles",
        "keycloak_read_only_roles",
        mode="before",
    )
    @classmethod
    def _parse_list_fields(cls, value):
        return cls._parse_listish(value)

    @field_validator("rest_auth_methods", "mcp_auth_methods", mode="after")
    @classmethod
    def _normalize_auth_methods(cls, value: list[str]) -> list[str]:
        supported = {"api_key", "local_jwt", "keycloak_jwt", "personal_token"}
        normalized = []
        for item in value:
            auth_method = str(item).strip().lower()
            if not auth_method:
                continue
            if auth_method not in supported:
                raise ValueError(f"Unsupported auth method: {item}")
            if auth_method not in normalized:
                normalized.append(auth_method)
        return normalized

    @model_validator(mode="after")
    def validate_required_secrets(self) -> "Settings":
        """Require critical secrets outside explicit test environments."""
        if self.environment.lower() == "testing":
            # Keep tests deterministic and avoid leaking local developer secrets.
            self.gitlab_token = ""
            self.api_secret_key = ""
            self.jwt_secret_key = ""
            self.mcp_auth_enabled = True
            self.api_cors_origins = [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
            ]
            return self

        insecure_placeholders = {
            "dummy",
            "changeme",
            "replace-me",
            "test-token",
            "api-secret",
            "jwt-secret",
        }

        missing = [
            name
            for name, value in (
                ("gitlab_token", self.gitlab_token),
                ("api_secret_key", self.api_secret_key),
                (
                    "jwt_secret_key",
                    self.jwt_secret_key,
                ) if (
                    "local_jwt" in self.rest_auth_methods
                    or "local_jwt" in self.mcp_auth_methods
                ) else (None, None),
            )
            if name is not None and (
                not str(value).strip() or str(value).strip().lower() in insecure_placeholders
            )
        ]
        if missing:
            missing_fields = ", ".join(missing)
            raise ValueError(
                f"Missing required settings for environment='{self.environment}': {missing_fields}"
            )

        return self


from functools import lru_cache


@lru_cache()
def get_settings() -> Settings:
    """Get the singleton Settings instance (lazily created).
    
    This defers instantiation until first use, avoiding import-time
    ValidationErrors when environment variables are not set (e.g. during
    test collection or CI environments).
    """
    return Settings()
