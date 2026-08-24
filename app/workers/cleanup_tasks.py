from __future__ import annotations
import time
from loguru import logger
from sqlalchemy import text
from app.workers.celery_app import celery_app


@celery_app.task(
    name="app.workers.cleanup_tasks.purge_tenant",
    acks_late=True,
)
def purge_tenant(tenant_id: str, tenant_slug: str) -> dict:
    import asyncio
    from app.db.sqlserver import tenant_db
    from app.core.vectorstore import adelete_tenant_collections
    from app.core.redis_client import TenantRedis

    start = time.perf_counter()
    log = logger.bind(tenant_id=tenant_id, tenant_slug=tenant_slug)
    log.warning("Purge tenant avviato — operazione distruttiva e irreversibile")

    loop = asyncio.new_event_loop()
    loop.run_until_complete( adelete_tenant_collections(tenant_slug) )
    loop.close()
    log.info("Purge tenant: collection Qdrant cancellate")

    loop = asyncio.new_event_loop()
    redis = TenantRedis( tenant_id = tenant_id )
    deleted_keys = loop.run_until_complete( redis.flush_tenant() )
    loop.close()
    log.info("Purge tenant: chiavi Redis cancellate", redis_keys_deleted=deleted_keys)

    schema_name = "tenant_" + tenant_slug.replace("-", "_").lower()   #.lower() coerente con _slug_to_schema() in app/db/sqlserver.py
    with tenant_db.sync_factory() as session:
        session.execute(
            text("UPDATE shared.tenants SET is_active = 0 WHERE slug = :slug"),
            {"slug": tenant_slug}
        )
        # DROP SCHEMA fallisce sempre se lo schema contiene ancora oggetti (SQL Server non
        # ha un DROP SCHEMA ... CASCADE) — e lo schema di un tenant ha sempre almeno le
        # tabelle create da shared.sp_provision_tenant. Le droppiamo prima tutte (nessuna FK
        # tra loro, verificato in docker/sqlserver/init.sql), poi l'utente dedicato, poi lo
        # schema stesso — stesso pattern di SQL dinamico già usato in sp_provision_tenant.
        session.execute(text(f"""
            DECLARE @sql NVARCHAR(MAX) = N'';
            SELECT @sql = @sql + N'DROP TABLE [{schema_name}].[' + t.name + N'];' + CHAR(13)
            FROM sys.tables t
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = N'{schema_name}';
            IF @sql <> N'' EXEC sp_executesql @sql;
        """))
        session.execute(text(f"""
            IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'usr_{schema_name}')
                DROP USER [usr_{schema_name}];
        """))
        session.execute(text(f"DROP SCHEMA IF EXISTS [{schema_name}]"))
        session.commit()

    logger.info(
        "Purge tenant completato : schema SQL Server rimosso",
        schema=schema_name,
        tenant=tenant_slug,
        redis_keys_deleted=deleted_keys,
    )
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    log.warning(
        "Purge tenant completato",
        redis_keys_deleted=deleted_keys,
        elapsed_ms=elapsed_ms,
    )
    return {"status": "purged", "tenant": tenant_slug}


@celery_app.task(
    name="app.workers.cleanup_tasks.expire_sessions",
    acks_late=True,
)
def expire_sessions() -> dict:
    import asyncio
    from app.core.redis_client import get_redis

    async def _cleanup():
        client = get_redis()

        cursor = 0
        fixed = 0
        while True:
            cursor, keys = await client.scan( cursor=cursor, match="tenant:*:session:*", count=200 )
            for key in keys:
                ttl = await client.ttl(key)
                if ttl == -1:
                    await client.expire(key, 86400)
                    fixed += 1
            if cursor == 0:
                break
        return fixed

    logger.debug("Session cleanup: avvio scan chiavi Redis senza TTL")
    loop = asyncio.new_event_loop()
    fixed = loop.run_until_complete( _cleanup() )
    loop.close()
    logger.info(f"Session cleanup: {fixed} chiavi senza TTL corrette")
    return {"fixed_keys": fixed}


