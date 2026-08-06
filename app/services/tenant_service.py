from __future__ import annotations
import time
from loguru import logger
from app.core.security import hash_password
from app.core.vectorstore import ensure_collection
from app.db.sqlserver import tenant_db
from sqlalchemy import text


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
    
    start = time.perf_counter()
    log = logger.bind(tenant_slug=slug, plan=plan)   #crea new logger che contiene gia questi campi
    log.info("Provisioning tenant: avvio", display_name=display_name, self_service=bool(owner_user_id))

    await tenant_db.provision_tenant( slug=slug, display_name=display_name, plan=plan, owner_user_id=owner_user_id, )  #execute function of tenant_db that came from sqlserver.py
    log.debug("Schema/tabelle tenant create su SQL Server")
    
    async with tenant_db.async_factory() as session:
        from sqlalchemy import text
        row = await session.execute(
            text("SELECT id FROM shared.tenants WHERE slug = :slug"),
            {"slug": slug}
        )
        fetched = row.fetchone()
        if not fetched:
            log.error("Tenant non trovato subito dopo il provisioning SQL")
            raise ValueError(f"Tenant '{slug}' non trovato dopo provisioning")
        tenant_id = str(fetched.id)
    log = log.bind(tenant_id=tenant_id)

    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor( None, ensure_collection, slug )
    logger.info(f"Collection Qdrant creata per il tenant: {slug}")
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
        log.info("Admin creato per il tenant", admin_email=admin_email, admin_user_id=admin_user_id)
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
        log.info("Owner platform collegato come admin del tenant", owner_email=owner_email)

    else:
        log.warning("Provisioning senza admin/owner: nessun utente creato nel nuovo tenant")
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    log.info("Provisioning tenant completato", admin_user_id=admin_user_id, elapsed_ms=elapsed_ms)
    
    return {
        "tenant_id": tenant_id,
        "slug": slug,
        "plan": plan,
        "admin_user_id": admin_user_id,
    }


