from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api import auth
from src.api.routes.auth import (
    MCPTokenCreateRequest,
    create_mcp_token,
    list_mcp_tokens,
    revoke_mcp_token,
)
from src.database.models import PersonalAccessToken


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_create_mcp_token_persists_hashed_token(async_session):
    current_user = {"user": "alice", "role": "admin", "auth_method": "keycloak_jwt"}

    response = await create_mcp_token(
        MCPTokenCreateRequest(name="CLI token", expires_in_days=7, role="readonly"),
        current_user=current_user,
        session=async_session,
    )

    stored = (
        await async_session.execute(select(PersonalAccessToken).where(PersonalAccessToken.id == response.id))
    ).scalar_one()

    assert response.token.startswith("axon_pat_")
    assert response.subject == "alice"
    assert response.role == "readonly"
    assert stored.subject == "alice"
    assert stored.role == "readonly"
    assert stored.token_hash == auth.hash_personal_access_token(response.token)
    assert stored.token_prefix == auth.build_personal_access_token_prefix(response.token)
    assert stored.created_by_auth_method == "keycloak_jwt"


@pytest.mark.asyncio
async def test_revoke_and_list_mcp_tokens(async_session):
    current_user = {"user": "bob", "role": "admin", "auth_method": "api_key"}

    created = await create_mcp_token(
        MCPTokenCreateRequest(name="MCP desktop", expires_in_days=30),
        current_user=current_user,
        session=async_session,
    )
    listed = await list_mcp_tokens(current_user=current_user, session=async_session)
    revoked = await revoke_mcp_token(created.id, current_user=current_user, session=async_session)

    assert [token.id for token in listed] == [created.id]
    assert revoked.id == created.id
    assert revoked.revoked_at is not None


@pytest.mark.asyncio
async def test_verify_personal_access_token_resolves_user(async_session, monkeypatch):
    created = await create_mcp_token(
        MCPTokenCreateRequest(name="Verifier token", expires_in_days=5),
        current_user={"user": "carol", "role": "admin", "auth_method": "api_key"},
        session=async_session,
    )

    monkeypatch.setattr(auth, "AsyncSessionLocal", lambda: _SessionFactory(async_session))

    user = await auth.verify_personal_access_token(created.token)

    assert user is not None
    assert user["user"] == "carol"
    assert user["role"] == "admin"
    assert user["auth_method"] == "personal_token"


@pytest.mark.asyncio
async def test_readonly_user_cannot_create_admin_token(async_session):
    with pytest.raises(HTTPException) as exc_info:
        await create_mcp_token(
            MCPTokenCreateRequest(name="Escalation", expires_in_days=7, role="admin"),
            current_user={"user": "dave", "role": "readonly", "auth_method": "keycloak_jwt"},
            session=async_session,
        )

    assert "elevated privileges" in str(exc_info.value)
