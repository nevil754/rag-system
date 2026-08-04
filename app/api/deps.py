from __future__ import annotations
from typing import Annotated, AsyncGenerator
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.redis_client import TenantRedis
from app.core.security import decode_access_token, extract_bearer_token, hash_api_key   #extract_bearer_token non server piu xk uso fastapi (better) x split the string in header 'Bearer'+the token
from app.db.sqlserver import tenant_db


tenant_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="TenantBearer",
    description="Incolla solo il token (senza il prefisso 'Bearer '): JWT tenant, per le route "
    "tenant-scoped (/chat, /documents, /collections, /jobs, /users, /auth/me ecc.).",
)
platform_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="PlatformBearer",
    description="Incolla solo il token (senza il prefisso 'Bearer '): JWT platform, per /spaces "
    "e /tenants.",
)
api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="API Key formato rag_xxx — alternativa al Bearer, solo per il livello tenant. attualmente utilizzato solo da 1 script",
)


class TenantContext:
    def __init__(
        self,
        tenant_id: str,
        tenant_slug: str,
        user_id: str,
        user_role: str,
        user_email: str,
    ):
        self.tenant_id = tenant_id
        self.tenant_slug = tenant_slug
        self.user_id = user_id
        self.user_role = user_role
        self.user_email = user_email

    @property
    def is_admin(self) -> bool:
        return self.user_role == "admin"

    @property
    def is_viewer(self) -> bool:
        return self.user_role == "viewer"


async def get_current_tenant(
    request: Request,
    #authorization: Annotated[str | None, Header()] = None,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    x_api_key: Annotated[str | None, Depends(api_key_scheme)] = None,
) -> TenantContext:

    state_tenant_id = getattr(request.state, "tenant_id", None)
    state_user_id = getattr(request.state, "user_id", None)
    if state_tenant_id and state_user_id:
        return TenantContext(
            tenant_id=state_tenant_id,
            tenant_slug=getattr(request.state, "tenant_slug", "") or "",
            user_id=state_user_id,
            user_role=getattr(request.state, "user_role", None) or "user",
            user_email=getattr(request.state, "user_email", None) or "",
        )
    
    # if authorization:
    #     token = extract_bearer_token(authorization)
    #     if token:
    #         payload = decode_access_token(token)
    #         if payload and not payload.get("is_platform"):
    #             return TenantContext(
    #                 tenant_id=payload.get("tenant_id", ""),
    #                 tenant_slug=payload.get("tenant_slug", ""),
    #                 user_id=payload.get("sub", ""),
    #                 user_role=payload.get("role", "user"),
    #                 user_email=payload.get("email", ""),
    #             )

    if credentials:
        payload = decode_access_token(credentials.credentials)
        if payload and not payload.get("is_platform"):
            return TenantContext(
                tenant_id=payload.get("tenant_id", ""),
                tenant_slug=payload.get("tenant_slug", ""),
                user_id=payload.get("sub", ""),
                user_role=payload.get("role", "user"),
                user_email=payload.get("email", ""),
            )

    if x_api_key:
        context = await _validate_api_key(x_api_key)
        if context:
            return context
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token non valido o scaduto",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _validate_api_key(api_key: str) -> TenantContext | None:
    try:
        key_hash = hash_api_key(api_key)

        async with tenant_db.async_factory() as session:
            from sqlalchemy import text
            result = await session.execute(
                text("""
                    SELECT
                        ak.tenant_id,
                        t.slug as tenant_slug,
                        ak.scopes
                    FROM shared.api_keys ak
                    JOIN shared.tenants t ON ak.tenant_id = t.id
                    WHERE ak.key_hash = :hash
                      AND ak.is_active = 1
                      AND (ak.expires_at IS NULL OR ak.expires_at > SYSUTCDATETIME())
                """),
                {"hash": key_hash}
            )
            row = result.fetchone()
            if not row:
                return None
            await session.execute(
                text("UPDATE shared.api_keys SET last_used = SYSUTCDATETIME() WHERE key_hash = :hash"),
                {"hash": key_hash}
            )
            await session.commit()
            return TenantContext(
                tenant_id=str(row.tenant_id),
                tenant_slug=row.tenant_slug,
                user_id=f"api_key_{key_hash[:8]}",
                user_role="api",
                user_email="",
            )
    except Exception as e:
        logger.error(f"Errore validazione API key: {e}")
        return None


async def get_db(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> AsyncGenerator[AsyncSession, None]:
    async with tenant_db.aget_session(tenant.tenant_slug) as session:
        yield session

def get_tenant_redis(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> TenantRedis:
    return TenantRedis(tenant_id=tenant.tenant_id)

async def require_admin(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> TenantContext:
    if not tenant.is_admin:   #check self.user_role == "admin" quindi true/false
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accesso riservato agli amministratori",
        )
    return tenant


class PlatformContext:
    def __init__(self, platform_user_id: str, email: str):
        self.platform_user_id = platform_user_id
        self.email = email


async def get_current_platform_user(
    #authorization: Annotated[str | None, Header()] = None,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(platform_bearer_scheme)] = None,
) -> PlatformContext:
    #token = extract_bearer_token(authorization)
    # if token:
    #     payload = decode_access_token(token)
    #     if payload and payload.get("is_platform"):
    #         return PlatformContext(
    #             platform_user_id=payload.get("sub", ""),
    #             email=payload.get("email", ""),
    #         )
    if credentials:
        payload = decode_access_token(credentials.credentials)
        if payload and payload.get("is_platform"):
            return PlatformContext(
                platform_user_id=payload.get("sub", ""),
                email=payload.get("email", ""),
            )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token platform non valido o scaduto",
        headers={"WWW-Authenticate": "Bearer"},
    )


# async def require_superadmin(
#     tenant: Annotated[TenantContext, Depends(get_current_tenant)],
# ) -> TenantContext:
#     if tenant.user_role != "superadmin":
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Accesso riservato ai superadmin",
#         )
#     return tenant


async def require_superadmin(
    platform_user: Annotated[PlatformContext, Depends(get_current_platform_user)],
) -> PlatformContext:
    from sqlalchemy import text
    async with tenant_db.async_factory() as session:
        row = await session.execute(
            text("SELECT is_superadmin FROM shared.platform_users WHERE id = :id"),
            {"id": platform_user.platform_user_id},
        )
        result = row.fetchone()
    if not result or not result.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accesso riservato ai superadmin",
        )
    return platform_user




CurrentTenant = Annotated[TenantContext, Depends(get_current_tenant)]

CurrentDB = Annotated[AsyncSession, Depends(get_db)]

CurrentRedis = Annotated[TenantRedis, Depends(get_tenant_redis)]

AdminOnly = Annotated[TenantContext, Depends(require_admin)]

SuperAdminOnly = Annotated[PlatformContext, Depends(require_superadmin)]

CurrentPlatformUser = Annotated[PlatformContext, Depends(get_current_platform_user)]


