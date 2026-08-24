from __future__ import annotations
import time
from loguru import logger
from sqlalchemy import text
from app.workers.celery_app import celery_app


@celery_app.task(
    name="app.workers.scheduled_tasks.rollup_usage",
    acks_late=True,
)
def rollup_usage() -> dict:
    import asyncio
    from app.db.sqlserver import tenant_db
    from app.core.redis_client import get_redis

    start = time.perf_counter()
    logger.info("Usage rollup: avvio")

    async def _get_all_tenants():
        async with tenant_db.async_factory() as session:
            result = await session.execute(
                text("SELECT id, slug FROM shared.tenants WHERE is_active = 1")
            )
            return result.fetchall()
    async def _get_tenant_stats( tenant_id: str ):
        client = get_redis()
        today = __import__("datetime").date.today().isoformat()
        base = f"tenant:{tenant_id}:stats:{today}"
        pipe = client.pipeline()
        pipe.get(f"{base}:tokens_in")
        pipe.get(f"{base}:tokens_out")
        pipe.get(f"{base}:queries")
        pipe.get(f"{base}:docs_ingested")
        results = await pipe.execute()
        return {
            "tokens_in": int(results[0] or 0),
            "tokens_out": int(results[1] or 0),
            "queries_count": int(results[2] or 0),
            "docs_ingested": int(results[3] or 0),
        }
    loop = asyncio.new_event_loop()
    tenants = loop.run_until_complete( _get_all_tenants() )
    logger.debug("Usage rollup: tenant attivi trovati", count=len(tenants))
    saved = 0
    for tenant in tenants:
        stats = loop.run_until_complete( _get_tenant_stats(str(tenant.id)) )
        if stats["queries_count"] == 0 and stats["docs_ingested"] == 0:
            continue

        with tenant_db.sync_factory() as session:
            session.execute(
                text("""
                    MERGE shared.usage_stats AS target
                    USING (VALUES (:tenant_id, CAST(SYSUTCDATETIME() AS DATE),
                           :tokens_in, :tokens_out, :queries_count, :docs_ingested))
                    AS source (tenant_id, stat_date, tokens_in, tokens_out,
                               queries_count, docs_ingested)
                    ON target.tenant_id = source.tenant_id
                       AND target.stat_date = source.stat_date
                    WHEN MATCHED THEN UPDATE SET
                        tokens_in = target.tokens_in + source.tokens_in,
                        tokens_out = target.tokens_out + source.tokens_out,
                        queries_count = target.queries_count + source.queries_count,
                        docs_ingested = target.docs_ingested + source.docs_ingested
                    WHEN NOT MATCHED THEN INSERT
                        (tenant_id, stat_date, tokens_in, tokens_out,
                         queries_count, docs_ingested)
                    VALUES (source.tenant_id, source.stat_date, source.tokens_in,
                            source.tokens_out, source.queries_count, source.docs_ingested);
                """),
                {
                    "tenant_id": str(tenant.id),
                    **stats,
                }
            )
            session.commit()
        saved += 1
    loop.close()
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    logger.info(
        "Usage rollup completato",
        tenants_checked=len(tenants), tenants_saved=saved, elapsed_ms=elapsed_ms,
    )
    return {"tenants_saved": saved}


