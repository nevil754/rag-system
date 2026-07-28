from __future__ import annotations
from loguru import logger
from app.core.security import hash_password
from app.core.vectorstore import ensure_collection
from app.db.sqlserver import tenant_db


async def provision_tenant(
    slug: str,
    display_name: str,
    plan: str = "starter",
    admin_email: str | None = None,
    admin_password: str | None = None,
    owner_user_id: str | None = None,
    owner_email: str | None = None,
    owner_password_hash: str | None = None,
) -> dict:
    
    logger.info(f"Provisioning tenant: {slug}")
    await tenant_db.provision_tenant( slug=slug, display_name=display_name, plan=plan, owner_user_id=owner_user_id, )
    
    async with tenant_db.async_factory() as session:
        from sqlalchemy import text
        row = await session.execute(
            text("SELECT id FROM shared.tenants WHERE slug = :slug"),
            {"slug": slug}
        )
        fetched = row.fetchone()
        if not fetched:
            raise ValueError(f"Tenant '{slug}' non trovato dopo provisioning")
        tenant_id = str(fetched.id)

    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor( None, ensure_collection, slug )
    logger.info(f"Collection Qdrant creata per tenant: {slug}")
    admin_user_id = None
    if admin_email and admin_password:
        from uuid import uuid4
        admin_user_id = str(uuid4())
        async with tenant_db.aget_session(slug) as session:
            await session.execute(
                text("""
                    INSERT INTO users (id, email, role, password_hash)
                    VALUES (:id, :email, 'admin', :pwd_hash)
                """),
                {
                    "id": admin_user_id,
                    "email": admin_email,
                    "pwd_hash": hash_password(admin_password),
                }
            )
        logger.info(f"Admin creato per tenant {slug}: {admin_email}")
    elif owner_user_id and owner_email and owner_password_hash:
        #Lo Space è creato dal proprio owner (platform user): riusiamo lo stesso id e lo
        # stesso hash password già presenti in shared.platform_users, nessun re-hash, così
        #/auth/me e un eventuale login classico diretto su questo tenant restano coerenti.
        admin_user_id = owner_user_id
        async with tenant_db.aget_session(slug) as session:
            await session.execute(
                text("""
                    INSERT INTO users (id, email, role, password_hash)
                    VALUES (:id, :email, 'admin', :pwd_hash)
                """),
                {
                    "id": owner_user_id,
                    "email": owner_email,
                    "pwd_hash": owner_password_hash,
                }
            )
        logger.info(f"Owner collegato come admin del tenant {slug}: {owner_email}")
    logger.info(f"Provisioning completato: {slug} (tenant_id={tenant_id})")
    return {
        "tenant_id": tenant_id,
        "slug": slug,
        "plan": plan,
        "admin_user_id": admin_user_id,
    }


