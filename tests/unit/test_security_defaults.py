import pytest

from src.config.settings import Settings


def _make_settings(**overrides):
    base = {
        "gitlab_token": "test-token",
        "database_url": "sqlite+aiosqlite:///./test.db",
        "api_secret_key": "api-secret",
        "jwt_secret_key": "jwt-secret",
    }
    base.update(overrides)
    return Settings(**base)


def test_secure_defaults_enabled():
    settings = _make_settings()

    assert settings.mcp_auth_enabled is True


def test_default_cors_origins_are_explicit_not_wildcard():
    settings = _make_settings()

    assert "*" not in settings.api_cors_origins
    assert "http://localhost:3000" in settings.api_cors_origins
    assert "http://127.0.0.1:3000" in settings.api_cors_origins


def test_default_auth_methods_include_api_key_and_local_jwt():
    settings = _make_settings()

    assert settings.rest_auth_methods == ["api_key", "local_jwt"]
    assert settings.mcp_auth_methods == ["api_key", "local_jwt"]


def test_auth_methods_accept_csv_configuration():
    settings = _make_settings(
        rest_auth_methods="api_key,keycloak_jwt",
        mcp_auth_methods="keycloak_jwt",
    )

    assert settings.rest_auth_methods == ["api_key", "keycloak_jwt"]
    assert settings.mcp_auth_methods == ["keycloak_jwt"]


def test_keycloak_scopes_accept_csv_configuration():
    settings = _make_settings(keycloak_scopes="openid,profile,email")

    assert settings.keycloak_scopes == ["openid", "profile", "email"]


def test_testing_environment_allows_missing_secrets(monkeypatch):
    monkeypatch.delenv("gitlab_token", raising=False)
    monkeypatch.delenv("api_secret_key", raising=False)
    monkeypatch.delenv("jwt_secret_key", raising=False)

    settings = Settings(environment="testing", database_url="sqlite+aiosqlite:///./test.db")

    assert settings.gitlab_token == ""
    assert settings.api_secret_key == ""
    assert settings.jwt_secret_key == ""


def test_non_testing_environment_requires_critical_secrets(monkeypatch):
    for key in ("gitlab_token", "api_secret_key", "jwt_secret_key"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.upper(), raising=False)

    with pytest.raises(ValueError, match="Missing required settings"):
        Settings(environment="development", database_url="sqlite+aiosqlite:///./test.db")


def test_non_testing_environment_rejects_whitespace_only_secrets():
    with pytest.raises(ValueError, match="Missing required settings"):
        Settings(
            environment="development",
            database_url="sqlite+aiosqlite:///./test.db",
            gitlab_token="   ",
            api_secret_key="\t",
            jwt_secret_key="\n",
        )


def test_debug_release_alias_is_treated_as_false():
    settings = Settings(
        environment="testing",
        database_url="sqlite+aiosqlite:///./test.db",
        debug="release",
    )

    assert settings.debug is False


def test_jwt_secret_not_required_when_local_jwt_disabled():
    settings = Settings(
        environment="development",
        database_url="sqlite+aiosqlite:///./test.db",
        gitlab_token="gitlab-real-token",
        api_secret_key="api-secret-real",
        jwt_secret_key="",
        rest_auth_methods=["api_key", "keycloak_jwt"],
        mcp_auth_methods=["api_key"],
    )

    assert settings.jwt_secret_key == ""
