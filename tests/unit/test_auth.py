from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from jose import jwt

from src.api import auth


def test_create_access_token_uses_timezone_aware_exp(monkeypatch):
    settings = SimpleNamespace(
        jwt_access_token_expire_minutes=30,
        jwt_secret_key="test-secret",
        jwt_algorithm="HS256",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    token = auth.create_access_token({"sub": "alice", "role": "admin"})
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

    exp_timestamp = payload["exp"]
    exp_dt = datetime.fromtimestamp(exp_timestamp, tz=UTC)

    now_utc = datetime.now(UTC)
    delta = exp_dt - now_utc
    assert timedelta(minutes=29) <= delta <= timedelta(minutes=31)


def test_create_access_token_honors_custom_expiry(monkeypatch):
    settings = SimpleNamespace(
        jwt_access_token_expire_minutes=30,
        jwt_secret_key="test-secret",
        jwt_algorithm="HS256",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    token = auth.create_access_token({"sub": "alice"}, expires_delta=timedelta(minutes=5))
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

    exp_dt = datetime.fromtimestamp(payload["exp"], tz=UTC)
    delta = exp_dt - datetime.now(UTC)
    assert timedelta(minutes=4) <= delta <= timedelta(minutes=6)


def test_extract_bearer_token():
    assert auth._extract_bearer_token("Bearer abc123") == "abc123"
    assert auth._extract_bearer_token("bearer xyz") == "xyz"
    assert auth._extract_bearer_token("Basic nope") is None
    assert auth._extract_bearer_token(None) is None


@pytest.mark.asyncio
async def test_get_current_user_accepts_api_key_when_configured(monkeypatch):
    settings = SimpleNamespace(
        auth_enabled=True,
        rest_auth_methods=["api_key"],
        admin_api_key="admin-secret",
        read_only_api_keys=[],
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    user = await auth.get_current_user(
        api_key="admin-secret",
        cookie_token=None,
        bearer_token=None,
    )

    assert user["user"] == "admin"
    assert user["role"] == "admin"
    assert user["auth_method"] == "api_key"


@pytest.mark.asyncio
async def test_get_current_user_accepts_local_jwt_bearer_when_configured(monkeypatch):
    settings = SimpleNamespace(
        auth_enabled=True,
        rest_auth_methods=["local_jwt"],
        admin_api_key="",
        read_only_api_keys=[],
        jwt_secret_key="test-secret",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=30,
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    token = auth.create_access_token({"sub": "alice", "role": "admin"})
    user = await auth.get_current_user(
        api_key=None,
        cookie_token=None,
        bearer_token=token,
    )

    assert user["user"] == "alice"
    assert user["role"] == "admin"
    assert user["auth_method"] == "local_jwt"


@pytest.mark.asyncio
async def test_get_current_user_falls_through_to_keycloak_when_local_jwt_fails(monkeypatch):
    settings = SimpleNamespace(
        auth_enabled=True,
        rest_auth_methods=["local_jwt", "keycloak_jwt"],
        admin_api_key="",
        read_only_api_keys=[],
        jwt_secret_key="local-secret",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=30,
        keycloak_username_claim="preferred_username",
        keycloak_role_claim="realm_access.roles",
        keycloak_admin_roles=["admin"],
        keycloak_read_only_roles=["readonly"],
        keycloak_default_role="readonly",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(
        auth,
        "verify_keycloak_token",
        lambda token: {
            "sub": "user-1",
            "preferred_username": "kc-user",
            "realm_access": {"roles": ["admin"]},
        } if token == "kc-token" else None,
    )

    user = await auth.get_current_user(
        api_key=None,
        cookie_token=None,
        bearer_token="kc-token",
    )

    assert user["user"] == "kc-user"
    assert user["role"] == "admin"
    assert user["auth_method"] == "keycloak_jwt"


@pytest.mark.asyncio
async def test_get_current_user_accepts_keycloak_cookie_when_configured(monkeypatch):
    settings = SimpleNamespace(
        auth_enabled=True,
        rest_auth_methods=["keycloak_jwt"],
        admin_api_key="",
        read_only_api_keys=[],
        keycloak_username_claim="preferred_username",
        keycloak_role_claim="realm_access.roles",
        keycloak_admin_roles=["admin"],
        keycloak_read_only_roles=["readonly"],
        keycloak_default_role="readonly",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(
        auth,
        "verify_keycloak_token",
        lambda token: {
            "sub": "user-2",
            "preferred_username": "cookie-user",
            "realm_access": {"roles": ["readonly"]},
        } if token == "kc-cookie-token" else None,
    )

    user = await auth.get_current_user(
        api_key=None,
        cookie_token="kc-cookie-token",
        bearer_token=None,
    )

    assert user["user"] == "cookie-user"
    assert user["role"] == "readonly"
    assert user["auth_method"] == "keycloak_jwt"


def test_build_keycloak_user_maps_readonly_role(monkeypatch):
    settings = SimpleNamespace(
        keycloak_username_claim="preferred_username",
        keycloak_role_claim="realm_access.roles",
        keycloak_admin_roles=["admin"],
        keycloak_read_only_roles=["readonly", "viewer"],
        keycloak_default_role="readonly",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    user = auth._build_keycloak_user(
        {
            "sub": "123",
            "preferred_username": "viewer-user",
            "realm_access": {"roles": ["viewer"]},
        }
    )

    assert user["user"] == "viewer-user"
    assert user["role"] == "readonly"
