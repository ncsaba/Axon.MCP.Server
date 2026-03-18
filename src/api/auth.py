import secrets
import hashlib
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Dict, Any, Optional

import jwt as pyjwt
from fastapi import Security, HTTPException, status, Depends
from fastapi.security import APIKeyHeader
from fastapi.openapi.models import APIKey, APIKeyIn
from starlette.requests import HTTPConnection
from jose import jwt, JWTError
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select

from src.config.settings import get_settings
from src.database.models import PersonalAccessToken
from src.database.session import AsyncSessionLocal
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class CustomAPIKeyHeader(APIKeyHeader):
    """
    Custom APIKeyHeader that supports both Request and WebSocket connections.
    Includes the 'request' argument in __call__ which is missing in the base class when strict typing is enforced
    or when FastAPI injects dependencies into WebSockets.
    """
    async def __call__(self, request: HTTPConnection) -> Optional[str]:
        api_key = request.headers.get(self.model.name)
        if not api_key:
            if self.auto_error:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Not authenticated"
                )
            else:
                return None
        return api_key

api_key_header = CustomAPIKeyHeader(name="X-API-Key", auto_error=False)


def _extract_bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    """Return bearer token from Authorization header when present."""
    if not authorization_header:
        return None
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def hash_personal_access_token(token: str) -> str:
    """Hash a personal access token for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_personal_access_token() -> str:
    """Generate a new personal access token secret."""
    return f"axon_pat_{secrets.token_urlsafe(32)}"


def build_personal_access_token_prefix(token: str) -> str:
    """Build a short visible prefix for token listing/auditing."""
    return token[:20]


def get_bearer_token(request: HTTPConnection) -> Optional[str]:
    """Extract bearer token from Authorization header."""
    return _extract_bearer_token(request.headers.get("Authorization"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a new JWT access token."""
    to_encode = data.copy()
    now_utc = datetime.now(UTC)
    if expires_delta:
        expire = now_utc + expires_delta
    else:
        expire = now_utc + timedelta(minutes=get_settings().jwt_access_token_expire_minutes)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, get_settings().jwt_secret_key, algorithm=get_settings().jwt_algorithm)
    return encoded_jwt


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify and decode a local Axon JWT token."""
    try:
        payload = jwt.decode(token, get_settings().jwt_secret_key, algorithms=[get_settings().jwt_algorithm])
        return payload
    except JWTError:
        return None


def get_token_from_cookie(request: HTTPConnection) -> Optional[str]:
    """Extract access token from secure cookie (works for Request and WebSocket)."""
    return request.cookies.get("access_token")


def _derive_keycloak_jwks_url() -> Optional[str]:
    """Return the configured or inferred Keycloak JWKS URL."""
    settings = get_settings()
    if settings.keycloak_jwks_url:
        return settings.keycloak_jwks_url
    if settings.keycloak_issuer_url:
        return settings.keycloak_issuer_url.rstrip("/") + "/protocol/openid-connect/certs"
    return None


@lru_cache(maxsize=8)
def _get_keycloak_jwk_client(jwks_url: str) -> PyJWKClient:
    """Cache JWKS clients by URL."""
    return PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)


def _extract_claim_path(payload: Dict[str, Any], claim_path: str) -> Any:
    """Resolve a dotted claim path from JWT payload."""
    current: Any = payload
    for part in claim_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _normalize_roles(value: Any) -> list[str]:
    """Normalize arbitrary role claim content to a lowercase list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip().lower()] if value.strip() else []
    if isinstance(value, list):
        roles = []
        for item in value:
            text = str(item).strip().lower()
            if text and text not in roles:
                roles.append(text)
        return roles
    return []


def verify_keycloak_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify and decode a Keycloak JWT token."""
    settings = get_settings()
    jwks_url = _derive_keycloak_jwks_url()
    if not settings.keycloak_issuer_url or not jwks_url:
        return None

    try:
        signing_key = _get_keycloak_jwk_client(jwks_url).get_signing_key_from_jwt(token)
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            audience=settings.keycloak_audiences or None,
            issuer=settings.keycloak_issuer_url,
            options={"verify_aud": bool(settings.keycloak_audiences)},
        )
        return payload
    except InvalidTokenError:
        return None


def _build_keycloak_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Map a verified Keycloak token to the internal auth payload."""
    settings = get_settings()
    username = (
        payload.get(settings.keycloak_username_claim)
        or payload.get("preferred_username")
        or payload.get("email")
        or payload.get("sub")
        or "keycloak-user"
    )
    roles = _normalize_roles(_extract_claim_path(payload, settings.keycloak_role_claim))
    admin_roles = {role.lower() for role in settings.keycloak_admin_roles}
    readonly_roles = {role.lower() for role in settings.keycloak_read_only_roles}

    if any(role in admin_roles for role in roles):
        role = "admin"
    elif any(role in readonly_roles for role in roles):
        role = "readonly"
    else:
        role = settings.keycloak_default_role

    return {
        "user": username,
        "role": role,
        "auth_method": "keycloak_jwt",
        "claims": payload,
    }


async def verify_personal_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a personal access token stored in the database."""
    token_hash = hash_personal_access_token(token)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PersonalAccessToken).where(PersonalAccessToken.token_hash == token_hash)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None

        now = datetime.now(UTC)
        if record.revoked_at is not None:
            return None
        if record.expires_at is not None and record.expires_at <= now:
            return None

        record.last_used_at = now
        await session.commit()

        return {
            "user": record.subject,
            "role": record.role,
            "auth_method": "personal_token",
            "token_id": record.id,
        }


async def _authenticate_via_methods(
    methods: list[str],
    *,
    api_key: Optional[str],
    bearer_token: Optional[str],
    cookie_token: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Attempt authentication using the configured methods in order."""
    settings = get_settings()

    for method in methods:
        if method == "api_key" and api_key:
            if settings.admin_api_key and secrets.compare_digest(api_key, settings.admin_api_key):
                return {"user": "admin", "role": "admin", "auth_method": "api_key"}

            for key in settings.read_only_api_keys:
                if secrets.compare_digest(api_key, key):
                    return {"user": "readonly", "role": "readonly", "auth_method": "api_key"}

            logger.warning("invalid_api_key_attempt", key_prefix=api_key[:8] if len(api_key) > 8 else "too_short")

        elif method == "local_jwt":
            token = bearer_token or cookie_token
            if token:
                payload = verify_token(token)
                if payload:
                    return {
                        "user": payload.get("sub", "local-user"),
                        "role": payload.get("role", "admin"),
                        "auth_method": "local_jwt",
                        "claims": payload,
                    }

        elif method == "keycloak_jwt":
            token = bearer_token or cookie_token
            if not token:
                continue
            payload = verify_keycloak_token(token)
            if payload:
                return _build_keycloak_user(payload)

        elif method == "personal_token" and api_key:
            token_user = await verify_personal_access_token(api_key)
            if token_user:
                return token_user

    return None


async def get_current_user(
    api_key: Optional[str] = Security(api_key_header),
    cookie_token: Optional[str] = Depends(get_token_from_cookie),
    bearer_token: Optional[str] = Depends(get_bearer_token),
) -> Dict[str, Any]:
    """
    Validate Authentication (API Key OR Cookie).
    
    Raises:
        HTTPException: 401 if not authenticated
    """
    # Allow bypass in dev/test environments
    if not get_settings().auth_enabled:
        return {"user": "anonymous", "role": "admin"}

    user = await _authenticate_via_methods(
        get_settings().rest_auth_methods,
        api_key=api_key,
        bearer_token=bearer_token,
        cookie_token=cookie_token,
    )
    if user:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Not authenticated. Provide a configured auth method "
            "(for example X-API-Key, local JWT, or Keycloak bearer token)."
        )
    )


async def get_current_user_mcp(
    api_key: Optional[str] = Security(api_key_header),
    cookie_token: Optional[str] = Depends(get_token_from_cookie),
    bearer_token: Optional[str] = Depends(get_bearer_token),
) -> Dict[str, Any]:
    """
    MCP-specific auth that allows bypass via configuration.
    Used for MCP clients (like Claude Desktop) that may not support headers.
    """
    if not get_settings().mcp_auth_enabled:
        # Bypass auth for MCP if disabled
        return {"user": "mcp_client", "role": "admin"}

    user = await _authenticate_via_methods(
        get_settings().mcp_auth_methods,
        api_key=api_key,
        bearer_token=bearer_token,
        cookie_token=cookie_token,
    )
    if user:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Not authenticated. Provide a configured MCP auth method "
            "(for example X-API-Key, local JWT, or Keycloak bearer token)."
        ),
    )
