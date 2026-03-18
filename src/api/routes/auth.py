"""Authentication endpoints."""
from datetime import UTC, datetime, timedelta
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import httpx
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import (
    build_personal_access_token,
    build_personal_access_token_prefix,
    create_access_token,
    get_current_user,
    hash_personal_access_token,
    verify_keycloak_token,
)
from src.api.dependencies import get_db_session
from src.config.settings import get_settings
from src.database.models import PersonalAccessToken
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    password: str


class AuthMethodsResponse(BaseModel):
    local_password_enabled: bool
    keycloak_login_enabled: bool
    rest_auth_methods: list[str]


class MCPTokenCreateRequest(BaseModel):
    name: str
    expires_in_days: Optional[int] = 30
    role: Optional[str] = None


class MCPTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    token_prefix: str
    subject: str
    display_name: Optional[str] = None
    role: str
    created_by_auth_method: Optional[str] = None
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class MCPTokenCreateResponse(MCPTokenResponse):
    token: str


def _is_production_cookie_mode() -> bool:
    return get_settings().environment.lower() == "production"


def _set_access_token_cookie(response: Response, token: str, *, max_age: Optional[int]) -> None:
    response.set_cookie(
        key="access_token",
        value=token,
        path="/",
        httponly=True,
        secure=_is_production_cookie_mode(),
        samesite="lax",
        max_age=max_age,
    )


def _clear_auth_flow_cookies(response: Response) -> None:
    response.delete_cookie(key="keycloak_oauth_state", path="/")
    response.delete_cookie(key="keycloak_oauth_next", path="/")


def _sanitize_next_path(next_path: Optional[str]) -> str:
    if not next_path:
        return "/"
    candidate = next_path.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    return candidate


def _derive_keycloak_endpoint(kind: str) -> str:
    issuer = getattr(get_settings(), "keycloak_issuer_url", None)
    if not issuer:
        raise HTTPException(status_code=503, detail="Keycloak issuer is not configured.")
    suffix = "auth" if kind == "authorization" else "token"
    return issuer.rstrip("/") + f"/protocol/openid-connect/{suffix}"


def _get_keycloak_redirect_uri(request: Request) -> str:
    return getattr(get_settings(), "keycloak_redirect_uri", None) or str(request.url_for("keycloak_callback"))


def _keycloak_login_enabled() -> bool:
    settings = get_settings()
    return (
        "keycloak_jwt" in getattr(settings, "rest_auth_methods", [])
        and bool(getattr(settings, "keycloak_issuer_url", None))
        and bool(getattr(settings, "keycloak_client_id", None))
    )


async def _exchange_keycloak_code_for_tokens(code: str, redirect_uri: str) -> Dict[str, Any]:
    settings = get_settings()
    token_url = _derive_keycloak_endpoint("token")
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.keycloak_client_id,
        "redirect_uri": redirect_uri,
    }
    if settings.keycloak_client_secret:
        payload["client_secret"] = settings.keycloak_client_secret

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=payload)

    if response.status_code >= 400:
        logger.warning("keycloak_code_exchange_failed", status=response.status_code, body=response.text[:300])
        raise HTTPException(status_code=502, detail="Keycloak token exchange failed.")

    payload = response.json()
    if "access_token" not in payload:
        raise HTTPException(status_code=502, detail="Keycloak response did not include an access token.")
    return payload


def _determine_token_role(current_user: Dict[str, Any], requested_role: Optional[str]) -> str:
    """Return the role granted to the new personal token."""
    current_role = str(current_user.get("role", "readonly")).lower()
    wanted_role = str(requested_role or current_role).lower()
    if wanted_role not in {"admin", "readonly"}:
        raise HTTPException(status_code=400, detail="Unsupported token role. Use 'admin' or 'readonly'.")
    if current_role != "admin" and wanted_role != current_role:
        raise HTTPException(status_code=403, detail="Cannot create a token with elevated privileges.")
    return wanted_role


@router.get("/auth/methods", response_model=AuthMethodsResponse)
async def get_auth_methods():
    """Expose browser-usable login methods for the UI."""
    settings = get_settings()
    return AuthMethodsResponse(
        local_password_enabled=(
            "local_jwt" in settings.rest_auth_methods and bool(settings.admin_password)
        ),
        keycloak_login_enabled=_keycloak_login_enabled(),
        rest_auth_methods=settings.rest_auth_methods,
    )


@router.post("/auth/login")
async def login(request: LoginRequest, response: Response):
    """
    Login with password to get session cookie.
    Used by UI to avoid exposing API keys in browser.
    """
    if "local_jwt" not in get_settings().rest_auth_methods:
        raise HTTPException(status_code=503, detail="Login disabled: local_jwt auth is not enabled for REST.")

    if not get_settings().admin_password:
        raise HTTPException(status_code=500, detail="Login disabled: ADMIN_PASSWORD not configured.")
        
    if not secrets.compare_digest(request.password, get_settings().admin_password):
        logger.warning("failed_login_attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password"
        )
    
    access_token = create_access_token(data={"sub": "admin", "role": "admin"})
    _set_access_token_cookie(
        response,
        access_token,
        max_age=get_settings().jwt_access_token_expire_minutes * 60,
    )
    
    logger.info("login_successful", user="admin")
    return {"message": "Logged in successfully"}


@router.get("/auth/keycloak/login")
async def keycloak_login(
    request: Request,
    next: Optional[str] = Query(default="/"),
):
    """Start the Keycloak auth-code flow for browser login."""
    settings = get_settings()
    if not _keycloak_login_enabled():
        raise HTTPException(
            status_code=503,
            detail="Keycloak browser login is not configured. Enable keycloak_jwt and set KEYCLOAK_ISSUER_URL plus KEYCLOAK_CLIENT_ID.",
        )

    state = secrets.token_urlsafe(32)
    redirect_uri = _get_keycloak_redirect_uri(request)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.keycloak_client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(settings.keycloak_scopes),
            "state": state,
        }
    )
    response = RedirectResponse(url=f"{_derive_keycloak_endpoint('authorization')}?{query}", status_code=302)
    response.set_cookie(
        key="keycloak_oauth_state",
        value=state,
        path="/",
        httponly=True,
        secure=_is_production_cookie_mode(),
        samesite="lax",
        max_age=600,
    )
    response.set_cookie(
        key="keycloak_oauth_next",
        value=_sanitize_next_path(next),
        path="/",
        httponly=True,
        secure=_is_production_cookie_mode(),
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/auth/keycloak/callback", name="keycloak_callback")
async def keycloak_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
):
    """Complete the Keycloak auth-code flow and establish the browser session."""
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing Keycloak callback parameters.")

    expected_state = request.cookies.get("keycloak_oauth_state")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="Invalid Keycloak login state.")

    redirect_uri = _get_keycloak_redirect_uri(request)
    token_payload = await _exchange_keycloak_code_for_tokens(code, redirect_uri)

    if not verify_keycloak_token(token_payload["access_token"]):
        raise HTTPException(status_code=401, detail="Keycloak returned an invalid access token.")

    next_path = _sanitize_next_path(request.cookies.get("keycloak_oauth_next"))
    response = RedirectResponse(url=next_path, status_code=302)
    expires_in = token_payload.get("expires_in")
    max_age = int(expires_in) if isinstance(expires_in, int) or str(expires_in).isdigit() else None
    _set_access_token_cookie(response, token_payload["access_token"], max_age=max_age)
    _clear_auth_flow_cookies(response)
    logger.info("keycloak_browser_login_successful", redirect_to=next_path)
    return response


@router.post("/auth/logout")
async def logout(response: Response):
    """Clear session cookie."""
    response.delete_cookie(key="access_token", path="/")
    _clear_auth_flow_cookies(response)
    return {"message": "Logged out successfully"}


@router.post("/auth/mcp-tokens", response_model=MCPTokenCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_token(
    payload: MCPTokenCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Create a new personal access token for MCP usage."""
    subject = str(current_user.get("user") or "anonymous")
    display_name = subject
    token_role = _determine_token_role(current_user, payload.role)

    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Token name is required.")

    expires_at = None
    if payload.expires_in_days is not None:
        if payload.expires_in_days <= 0:
            raise HTTPException(status_code=400, detail="expires_in_days must be positive.")
        expires_at = datetime.now(UTC) + timedelta(days=payload.expires_in_days)

    raw_token = build_personal_access_token()
    record = PersonalAccessToken(
        name=payload.name.strip(),
        token_hash=hash_personal_access_token(raw_token),
        token_prefix=build_personal_access_token_prefix(raw_token),
        subject=subject,
        display_name=display_name,
        role=token_role,
        created_by_auth_method=current_user.get("auth_method"),
        expires_at=expires_at,
    )
    session.add(record)
    await session.flush()
    await session.refresh(record)

    logger.info("mcp_personal_token_created", subject=subject, token_id=record.id, role=record.role)
    response = MCPTokenResponse.model_validate(record)
    return MCPTokenCreateResponse(**response.model_dump(), token=raw_token)


@router.get("/auth/mcp-tokens", response_model=list[MCPTokenResponse])
async def list_mcp_tokens(
    current_user: Dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """List the caller's personal MCP tokens."""
    subject = str(current_user.get("user") or "anonymous")
    result = await session.execute(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.subject == subject)
        .order_by(PersonalAccessToken.created_at.desc())
    )
    records = result.scalars().all()
    return [MCPTokenResponse.model_validate(record) for record in records]


@router.delete("/auth/mcp-tokens/{token_id}", response_model=MCPTokenResponse)
async def revoke_mcp_token(
    token_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Revoke one of the caller's personal MCP tokens."""
    subject = str(current_user.get("user") or "anonymous")
    record = await session.get(PersonalAccessToken, token_id)
    if record is None or record.subject != subject:
        raise HTTPException(status_code=404, detail="MCP token not found.")

    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await session.flush()

    logger.info("mcp_personal_token_revoked", subject=subject, token_id=record.id)
    return MCPTokenResponse.model_validate(record)
