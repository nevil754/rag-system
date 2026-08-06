from __future__ import annotations
import secrets
from datetime import datetime
from loguru import logger
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from slugify import slugify
from sqlalchemy import text

from app.api.deps import CurrentPlatformUser
from app.api.routes.auth import TokenResponse
from app.core.security import create_access_token
from app.core.settings import get_settings
from app.db.sqlserver import tenant_db
from app.services.tenant_service import provision_tenant

router = APIRouter(prefix="/spaces", tags=["spaces"])
settings = get_settings()

class SpaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

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



async def _get_owned_space( space_id: str, owner_user_id: str ) -> dict:
    async with tenant_db.async_factory() as session:
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
async def list_spaces(platform_user: CurrentPlatformUser) -> list[SpaceSchema]:
    async with tenant_db.async_factory() as session:
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
) -> SpaceSchema:
    base_slug = slugify(body.name) or "space"
    slug = base_slug
    async with tenant_db.async_factory() as session:
        for _ in range(5):   #5 cycles
            existing = await session.execute(
                text("SELECT 1 FROM shared.tenants WHERE slug = :slug"),
                {"slug": slug}
            )
            if not existing.fetchone():
                break
            slug = f"{base_slug}-{secrets.token_hex(3)}"  #secrets.token_hex(3) random text string in hexadecimal format containing 3 random bytes so it's 6 characters long (e.g. 'a1b2c3')
        else:
            raise HTTPException( status_code=409, detail="Impossibile generare uno slug univoco" )
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
    )
    await provision_tenant(
        slug=slug,
        display_name=body.name,
        plan="starter",
        owner_user_id=platform_user.platform_user_id,
        owner_email=owner.email,
        owner_password_hash=owner.password_hash,
    )
    logger.info("Space creato", platform_user_id=platform_user.platform_user_id, slug=slug)
    return await _get_owned_space_by_slug(slug, platform_user.platform_user_id)


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
) -> SpaceSchema:
    await _get_owned_space(space_id, platform_user.platform_user_id)
    async with tenant_db.async_factory() as session:
        await session.execute(
            text("""
                UPDATE shared.tenants
                SET display_name = :name, updated_at = SYSUTCDATETIME()
                WHERE id = :id AND owner_user_id = :owner_id
            """),
            {"name": body.name, "id": space_id, "owner_id": platform_user.platform_user_id}
        )
        await session.commit()
    logger.info(
        "Space rinominato",
        space_id=space_id,
        platform_user_id=platform_user.platform_user_id,
        new_name=body.name,
    )
    updated = await _get_owned_space(space_id, platform_user.platform_user_id)
    return SpaceSchema.model_validate(updated)



@router.patch("/{space_id}/disable", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def disable_space(space_id: str, platform_user: CurrentPlatformUser) -> None:
    await _get_owned_space(space_id, platform_user.platform_user_id)
    async with tenant_db.async_factory() as session:
        await session.execute(
            text("""
                UPDATE shared.tenants
                SET is_active = 0, updated_at = SYSUTCDATETIME()
                WHERE id = :id AND owner_user_id = :owner_id
            """),
            {"id": space_id, "owner_id": platform_user.platform_user_id}
        )
        await session.commit()
    logger.info(
        "Space disabilitato",
        space_id=space_id,
        platform_user_id=platform_user.platform_user_id,
    )


@router.post("/{space_id}/select", response_model=TokenResponse)
async def select_space(space_id: str, platform_user: CurrentPlatformUser) -> TokenResponse:
    space = await _get_owned_space(space_id, platform_user.platform_user_id)
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


