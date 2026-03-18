from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import src.api.routes.auth as auth_routes


def _build_app():
    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/v1")
    return app


async def _get(app: FastAPI, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        return await client.get(path, **kwargs)


async def test_get_auth_methods_reports_available_browser_flows(monkeypatch):
    settings = SimpleNamespace(
        rest_auth_methods=["local_jwt", "keycloak_jwt"],
        admin_password="secret",
        keycloak_issuer_url="https://kc.example/realms/axon",
        keycloak_client_id="axon-ui",
    )
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)

    response = await _get(_build_app(), "/api/v1/auth/methods")

    assert response.status_code == 200
    assert response.json() == {
        "local_password_enabled": True,
        "keycloak_login_enabled": True,
        "rest_auth_methods": ["local_jwt", "keycloak_jwt"],
    }


async def test_keycloak_login_redirect_sets_flow_cookies(monkeypatch):
    settings = SimpleNamespace(
        environment="development",
        rest_auth_methods=["keycloak_jwt"],
        keycloak_issuer_url="https://kc.example/realms/axon",
        keycloak_client_id="axon-ui",
        keycloak_scopes=["openid", "profile", "email"],
    )
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)

    response = await _get(_build_app(), "/api/v1/auth/keycloak/login?next=/repositories")

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://kc.example/realms/axon/protocol/openid-connect/auth?"
    )
    assert "client_id=axon-ui" in response.headers["location"]
    assert "scope=openid+profile+email" in response.headers["location"]
    set_cookie = "\n".join(response.headers.get_list("set-cookie"))
    assert "keycloak_oauth_state=" in set_cookie
    assert 'keycloak_oauth_next="/repositories"' in set_cookie


async def test_keycloak_callback_sets_access_cookie_and_redirects(monkeypatch):
    settings = SimpleNamespace(
        environment="development",
        keycloak_redirect_uri=None,
    )
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)

    async def _fake_exchange(code: str, redirect_uri: str):
        assert code == "callback-code"
        assert redirect_uri == "http://testserver/api/v1/auth/keycloak/callback"
        return {"access_token": "kc-token", "expires_in": 1200}

    monkeypatch.setattr(auth_routes, "_exchange_keycloak_code_for_tokens", _fake_exchange)
    monkeypatch.setattr(
        auth_routes,
        "verify_keycloak_token",
        lambda token: {"sub": "u1"} if token == "kc-token" else None,
    )

    response = await _get(
        _build_app(),
        "/api/v1/auth/keycloak/callback?code=callback-code&state=expected-state",
        cookies={
            "keycloak_oauth_state": "expected-state",
            "keycloak_oauth_next": "/repositories",
        },
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/repositories"
    set_cookie = "\n".join(response.headers.get_list("set-cookie"))
    assert "access_token=kc-token" in set_cookie
    assert "keycloak_oauth_state=\"\"" in set_cookie
    assert "keycloak_oauth_next=\"\"" in set_cookie


async def test_keycloak_callback_rejects_invalid_state(monkeypatch):
    monkeypatch.setattr(
        auth_routes,
        "get_settings",
        lambda: SimpleNamespace(environment="development", keycloak_redirect_uri=None),
    )

    response = await _get(
        _build_app(),
        "/api/v1/auth/keycloak/callback?code=callback-code&state=wrong-state",
        cookies={"keycloak_oauth_state": "expected-state"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Keycloak login state."
