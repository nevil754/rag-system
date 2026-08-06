from __future__ import annotations
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from app.api.deps import AdminOnly, CurrentDB, CurrentTenant
from app.core.vectorstore import aensure_collection
from app.schemas.common import PaginatedResponse
from app.schemas.document import CollectionCreate, CollectionSchema
from loguru import logger



router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", response_model=CollectionSchema, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CollectionCreate,
    tenant: CurrentTenant,
    db: CurrentDB,
) -> CollectionSchema:
    from uuid import uuid4
    from slugify import slugify   #x convert str normale in uno slug, cioè una versione "URL-friendly"

    coll_id = str(uuid4())
    qdrant_name = f"tenant_{tenant.tenant_slug.replace('-','_')}_{slugify(body.name)}"
    logger.warning("my1-ARRIVATO FINO A PRIMA DI AENSURE_COLLECTION()")
    await aensure_collection( tenant.tenant_slug )
    logger.warning("my2-ARRIVATO FINO A DOPO AENSURE_COLLECTION()")
    await db.execute(
        text("""
            INSERT INTO collections (id, name, description, qdrant_name, created_by)
            VALUES (:id, :name, :desc, :qdrant_name, :user_id)
        """),
        {
            "id": coll_id,
            "name": body.name,
            "desc": body.description,
            "qdrant_name": qdrant_name,
            "user_id": tenant.user_id,
        }
    )
    logger.warning("my3-ARRIVATO FINO A DOPO DB.EXECUTE()")
    row = await db.execute(
        text("SELECT * FROM collections WHERE id = :id"), {"id": coll_id}
    )
    logger.info(
        "Collection creata",
        tenant_id=tenant.tenant_id, created_by=tenant.user_id,
        collection_id=coll_id, name=body.name,
    )
    return CollectionSchema.model_validate( dict(row.fetchone()._mapping) )


@router.get("", response_model=PaginatedResponse[CollectionSchema])
async def list_collections(
    tenant: CurrentTenant,
    db: CurrentDB,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedResponse[CollectionSchema]:
    offset = (page - 1) * page_size
    total = ( await db.execute( text("SELECT COUNT(*) FROM collections WHERE is_active = 1") ) ).scalar() or 0  #scalar() prende il primo valore della prima riga 
    rows = await db.execute(
        text("""
            SELECT id, name, description, qdrant_name, is_active, created_at
            FROM collections WHERE is_active = 1
            ORDER BY created_at DESC
            OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
        """),
        {"offset": offset, "limit": page_size}
    )
    items = [CollectionSchema.model_validate(dict(r._mapping)) for r in rows]
    return PaginatedResponse.build( items=items, total=total, page=page, page_size=page_size )


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_collection(
    collection_id: str,
    tenant: AdminOnly,   #fa il check in deps.py self.user_role == "admin" quindi true/false e se è false allora Error 403
    db: CurrentDB,
) -> None:
    await db.execute(
        text("UPDATE collections SET is_active = 0 WHERE id = :id"),
        {"id": collection_id}
    )
    logger.info(
        "Collection eliminata (soft-delete)",
        tenant_id=tenant.tenant_id, deleted_by=tenant.user_id, collection_id=collection_id,
    )


