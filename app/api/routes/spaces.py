from __future__ import annotations
from datetime import datetime
from loguru import logger
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from app.api.deps import CurrentPlatformUser, IsSuperAdmin
from app.api.routes.auth import TokenResponse
from app.core.security import create_access_token
from app.core.settings import get_settings
from app.db.sqlserver import tenant_db
from app.services.tenant_service import generate_unique_slug, provision_tenant

router = APIRouter(prefix="/spaces", tags=["spaces"])
settings = get_settings()

class SpaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    # Piano e credenziali admin dedicate: onorati solo se il richiedente è superadmin (vedi
    # create_space), riservati alla creazione "d'ufficio" senza legarlo a chi lo crea.
    plan: str = "starter"
    admin_email: EmailStr | None = None
    admin_password: str | None = None

class SpaceRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

class SpaceSchema(BaseModel):
    id: str
    slug: str
    display_name: str
    plan: str
    is_active: bool
    created_at: datetime
    class Config:
        from_attributes = True



async def _get_managed_space(space_id: str, owner_user_id: str, is_superadmin: bool) -> dict:
    # Un superadmin gestisce qualunque Space (anche quelli senza owner o di un altro
    # account); un utente normale solo i propri.
    async with tenant_db.async_factory() as session:
        if is_superadmin:
            row = await session.execute(
                text("""
                    SELECT id, slug, display_name, [plan], is_active, created_at
                    FROM shared.tenants
                    WHERE id = :id
                """),
                {"id": space_id}
            )
        else:
            row = await session.execute(
                text("""
                    SELECT id, slug, display_name, [plan], is_active, created_at
                    FROM shared.tenants
                    WHERE id = :id AND owner_user_id = :owner_id
                """),
                {"id": space_id, "owner_id": owner_user_id}
            )
        space = row.fetchone()
    if not space:
        raise HTTPException( status_code=404, detail="Space non trovato" )
    return dict( space._mapping )


@router.get("", response_model=list[SpaceSchema])
async def list_spaces(platform_user: CurrentPlatformUser, is_superadmin: IsSuperAdmin) -> list[SpaceSchema]:
    async with tenant_db.async_factory() as session:
        if is_superadmin:
            rows = await session.execute(
                text("""
                    SELECT id, slug, display_name, [plan], is_active, created_at
                    FROM shared.tenants
                    ORDER BY created_at DESC
                """)
            )
        else:
            rows = await session.execute(
                text("""
                    SELECT id, slug, display_name, [plan], is_active, created_at
                    FROM shared.tenants
                    WHERE owner_user_id = :owner_id
                    ORDER BY created_at DESC
                """),
                {"owner_id": platform_user.platform_user_id}
            )
        return [ SpaceSchema.model_validate(dict(r._mapping)) for r in rows ]


@router.post( "", response_model=SpaceSchema, status_code=status.HTTP_201_CREATED )
async def create_space(
    body: SpaceCreate,
    platform_user: CurrentPlatformUser,
    is_superadmin: IsSuperAdmin,
) -> SpaceSchema:
    custom_credentials = body.admin_email is not None or body.admin_password is not None
    if (body.plan != "starter" or custom_credentials) and not is_superadmin:
        raise HTTPException(
            status_code=403,
            detail="Piano personalizzato e credenziali admin dedicate sono riservati ai superadmin",
        )
    if custom_credentials:
        if body.admin_email is None or body.admin_password is None:
            raise HTTPException(status_code=400, detail="Email e password admin vanno fornite insieme")
        if len(body.admin_password) < settings.password_min_length:
            raise HTTPException(
                status_code=400,
                detail=f"Password troppo corta (min {settings.password_min_length} caratteri)"
            )

    try:
        slug = await generate_unique_slug(body.name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if custom_credentials:
        # Ufficio assegnato a un admin dedicato (email/password fornite dal superadmin):
        # non legato all'account platform di chi lo crea, come il vecchio "Gestione uffici".
        logger.info(
            "Creazione nuovo space con admin dedicato (superadmin)",
            platform_user_id=platform_user.platform_user_id,
            name=body.name,
            slug=slug,
            plan=body.plan,
        )
        await provision_tenant(
            slug=slug,
            display_name=body.name,
            plan=body.plan,
            admin_email=body.admin_email,
            admin_password=body.admin_password,
        )
        return await _get_space_by_slug(slug)

    async with tenant_db.async_factory() as session:
        owner_row = await session.execute(
            text("SELECT email, password_hash FROM shared.platform_users WHERE id = :id"),
            {"id": platform_user.platform_user_id}
        )
        owner = owner_row.fetchone()
    if not owner:
        logger.warning(
            "Creazione space fallita: account platform non trovato",
            platform_user_id=platform_user.platform_user_id,
        )
        raise HTTPException(status_code=404, detail="Account platform non trovato")
    logger.info(
        "Creazione nuovo space",
        platform_user_id=platform_user.platform_user_id,
        name=body.name,
        slug=slug,
        plan=body.plan,
    )
    await provision_tenant(
        slug=slug,
        display_name=body.name,
        plan=body.plan,
        owner_user_id=platform_user.platform_user_id,
        owner_email=owner.email,
        owner_password_hash=owner.password_hash,
    )
    logger.info("Space creato", platform_user_id=platform_user.platform_user_id, slug=slug)
    return await _get_owned_space_by_slug(slug, platform_user.platform_user_id)


async def _get_space_by_slug(slug: str) -> SpaceSchema:
    async with tenant_db.async_factory() as session:
        row = await session.execute(
            text("""
                SELECT id, slug, display_name, [plan], is_active, created_at
                FROM shared.tenants
                WHERE slug = :slug
            """),
            {"slug": slug}
        )
        space = row.fetchone()
    if not space:
        raise HTTPException(status_code=404, detail="Space non trovato dopo la creazione")
    return SpaceSchema.model_validate(dict(space._mapping))


async def _get_owned_space_by_slug(slug: str, owner_user_id: str) -> SpaceSchema:
    async with tenant_db.async_factory() as session:
        row = await session.execute(
            text("""
                SELECT id, slug, display_name, [plan], is_active, created_at
                FROM shared.tenants
                WHERE slug = :slug AND owner_user_id = :owner_id
            """),
            {"slug": slug, "owner_id": owner_user_id}
        )
        space = row.fetchone()
    if not space:
        raise HTTPException(status_code=404, detail="Space non trovato dopo la creazione")
    return SpaceSchema.model_validate(dict(space._mapping))


@router.patch("/{space_id}", response_model=SpaceSchema)
async def rename_space(
    space_id: str,
    body: SpaceRename,
    platform_user: CurrentPlatformUser,
    is_superadmin: IsSuperAdmin,
) -> SpaceSchema:
    await _get_managed_space(space_id, platform_user.platform_user_id, is_superadmin)
    async with tenant_db.async_factory() as session:
        await session.execute(
            text("""
                UPDATE shared.tenants
                SET display_name = :name, updated_at = SYSUTCDATETIME()
                WHERE id = :id
            """),
            {"name": body.name, "id": space_id}
        )
        await session.commit()
    logger.info(
        "Space rinominato",
        space_id=space_id,
        platform_user_id=platform_user.platform_user_id,
        new_name=body.name,
    )
    updated = await _get_managed_space(space_id, platform_user.platform_user_id, is_superadmin)
    return SpaceSchema.model_validate(updated)



@router.patch("/{space_id}/disable", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def disable_space(
    space_id: str,
    platform_user: CurrentPlatformUser,
    is_superadmin: IsSuperAdmin,
) -> None:
    await _get_managed_space(space_id, platform_user.platform_user_id, is_superadmin)
    async with tenant_db.async_factory() as session:
        await session.execute(
            text("""
                UPDATE shared.tenants
                SET is_active = 0, updated_at = SYSUTCDATETIME()
                WHERE id = :id
            """),
            {"id": space_id}
        )
        await session.commit()
    logger.info(
        "Space disabilitato",
        space_id=space_id,
        platform_user_id=platform_user.platform_user_id,
    )


@router.post("/{space_id}/select", response_model=TokenResponse)
async def select_space(
    space_id: str,
    platform_user: CurrentPlatformUser,
    is_superadmin: IsSuperAdmin,
) -> TokenResponse:
    space = await _get_managed_space(space_id, platform_user.platform_user_id, is_superadmin)
    if not space["is_active"]:
        logger.warning(
            "Select space rifiutato: space disabilitato",
            space_id=space_id,
            platform_user_id=platform_user.platform_user_id,
        )
        raise HTTPException(status_code=403, detail="Space disabilitato")
    token = create_access_token(data={
        "sub": platform_user.platform_user_id,
        "email": platform_user.email,
        "role": "admin",
        "tenant_id": str(space["id"]),
        "tenant_slug": space["slug"],
    })
    logger.info(
        "Space selezionato: JWT tenant-scoped emesso",
        space_id=space_id,
        tenant_slug=space["slug"],
        platform_user_id=platform_user.platform_user_id,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user_id=platform_user.platform_user_id,
        user_role="admin",
        tenant_slug=space["slug"],
    )
