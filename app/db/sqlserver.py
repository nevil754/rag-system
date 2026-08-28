from __future__ import annotations
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from typing import AsyncGenerator, Generator
from loguru import logger
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker


class TenantDB:
    def __init__(self):
        # self._sync_factory = sessionmaker(
        #     bind= get_sync_engine(),
        #     autocommit= False,
        #     autoflush= False,
        # )
        # self._async_factory = async_sessionmaker(
        #     bind=get_async_engine(),
        #     autocommit=False,
        #     autoflush=False,
        #     expire_on_commit=False,
        # )
        self._sync_factory = None
        self._async_factory = None      

    @property
    def sync_factory(self):
        if self._sync_factory is None:
            self._sync_factory = sessionmaker(
                bind=get_sync_engine(),
                autocommit=False,  #SQLAlchemy non esegue automaticamente il COMMIT dopo ogni operazione
                autoflush=False,   #"Invia al database tutte le modifiche pendenti, ma senza fare COMMIT."
            )
        return self._sync_factory

    @property
    def async_factory(self):
        if self._async_factory is None:                  
            self._async_factory = async_sessionmaker( 
                bind=get_async_engine(),
                autocommit=False,
                autoflush=False,
                expire_on_commit=False,
            )
        return self._async_factory                  


    @contextmanager   #si occupa di gestire il contesto di esecuzione del codice, garantendo che le risorse vengano rilasciate correttamente al termine dell'esecuzione.
    def get_session(self, tenant_slug: str) -> Generator[Session, None, None]:
        session = self.sync_factory()
        impersonated = False
        try:
            self._set_schema_sync(session, tenant_slug)
            impersonated = True
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            if impersonated:
                try:
                    session.execute(text("REVERT"))
                    session.commit()
                except Exception as e:
                    # EXECUTE AS vive sulla connessione fisica, non sulla transazione: un
                    # REVERT fallito la rimanderebbe nel pool ancora impersonata come utente
                    # ristretto del tenant, e la prossima richiesta qualsiasi che la pesca dal
                    # pool (anche non-tenant, es. platform-login su shared.*) erediterebbe
                    # quel contesto e fallirebbe con "permission denied". Invalidarla forza il
                    # pool a scartarla invece di riusarla.
                    logger.error(f"REVERT fallito, invalido la connessione: {e}", tenant_slug=tenant_slug)
                    session.invalidate()
            session.close()

    @asynccontextmanager
    async def aget_session(
        self, tenant_slug: str
    ) -> AsyncGenerator[AsyncSession, None]:
        async with self.async_factory() as session:
            impersonated = False    #flag boolean, x dire "Sono riuscito a impersonare il tenant?"
            try:
                await self._set_schema_async(session, tenant_slug)
                impersonated = True
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise   #passa l'exception al chiamante
            finally:
                if impersonated:
                    try:
                        await session.execute(text("REVERT"))   #se era stato fatto e.g.EXECUTE AS USER = 'tenant_acme', ora con questo invece torni all'user origine e.g. AppLogin
                        await session.commit()
                    except Exception as e:
                        # vedi commento nella versione sync (get_session) qui sopra: REVERT
                        # fallito → connessione ancora impersonata → va invalidata, non
                        # rimandata nel pool, altrimenti "avvelena" la prossima richiesta
                        # qualsiasi che la riceve in checkout dal pool.
                        logger.error(f"REVERT fallito, invalido la connessione: {e}", tenant_slug=tenant_slug)
                        await session.invalidate()


    def _set_schema_sync(self, session: Session, tenant_slug: str) -> None:
        schema_name = _slug_to_schema(tenant_slug)
        result = session.execute(
            text("SELECT 1 FROM sys.schemas WHERE name = :schema"),
            {"schema": schema_name}
        ).fetchone()
        if not result:
            raise ValueError(
                f"Schema tenant '{schema_name}' non trovato. "
                f"Eseguire sp_provision_tenant prima."
            )
        user_name = _slug_to_user(tenant_slug)
        session.execute(text("EXECUTE AS USER = :user_name"), {"user_name": user_name})

    async def _set_schema_async(
        self, session: AsyncSession, tenant_slug: str
    ) -> None:
        schema_name = _slug_to_schema(tenant_slug)
        result = await session.execute(
            text("SELECT 1 FROM sys.schemas WHERE name = :schema"),
            {"schema": schema_name}
        )
        if not result.fetchone():
            raise ValueError(
                f"Schema tenant '{schema_name}' non trovato."
            )
        user_name = _slug_to_user(tenant_slug)
        await session.execute(text("EXECUTE AS USER = :user_name"), {"user_name": user_name})


    async def provision_tenant(
        self,
        slug: str,
        display_name: str,
        plan: str = "starter",
        owner_user_id: str | None = None,
    ) -> None:
        async with self.async_factory() as session:
            try:
                await session.execute(
                    text("""
                        EXEC shared.sp_provision_tenant
                            @slug = :slug,
                            @display_name = :display_name,
                            @plan = :plan,
                            @owner_user_id = :owner_user_id
                    """),
                    {
                        "slug": slug, 
                        "display_name": display_name, 
                        "plan": plan,
                        "owner_user_id": owner_user_id,
                    }
                )
                await session.commit()
                logger.info(
                    "Tenant provisionato",
                    slug=slug,
                    schema=_slug_to_schema(slug),
                )
            except Exception as e:
                await session.rollback()
                logger.error(f"Errore provisioning tenant {slug}: {e}")
                raise

    @staticmethod
    async def ping() -> bool:
        try:
            engine = get_async_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"SQL Server ping fallito: {e}")
            return False



def _slug_to_schema(slug: str) -> str:
    return "tenant_" + slug.replace("-", "_").lower()

def _slug_to_user(slug: str) -> str:
    return "usr_" + _slug_to_schema(slug)


def _revert_impersonation_on_checkin(dbapi_connection, connection_record) -> None:
    # Le sessioni tenant-scoped (get_session/aget_session) fanno EXECUTE AS USER su un
    # utente ristretto del tenant e poi REVERT a fine richiesta. Se quel REVERT non va a
    # buon fine (eccezione non propagata fin qui, richiesta cancellata a metà, ecc.) la
    # connessione fisica torna nel pool ancora impersonata: una richiesta successiva
    # completamente scollegata (es. shared.* via async_factory() nudo) la ripescherebbe
    # dal pool ereditando quel contesto ristretto e fallirebbe con "permission denied" su
    # oggetti shared.* (vedi bug 2026-08-27 su POST /api/v1/spaces). Prima che una
    # connessione rientri nel pool, tenta comunque un REVERT: no-op se non c'era nulla da
    # revertire, altrimenti scarta la connessione invece di rimetterla in circolo.
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("BEGIN TRY REVERT; END TRY BEGIN CATCH END CATCH")
        dbapi_connection.commit()
        cursor.close()
    except Exception as e:
        logger.error(f"Reset impersonazione fallito al checkin, scarto la connessione: {e}")
        connection_record.invalidate()


@lru_cache(maxsize=1)
def get_sync_engine():
    from app.core.settings import get_settings
    settings = get_settings()
    engine = create_engine(
        settings.sqlserver_url,
        pool_size=5,   #num connessioni 'normali' mantenute nel pool, e.g.5 vuol dire 5 connessioni persistenti aperte
        max_overflow=10,   #num connessioni 'extra' mantenute nel pool
        pool_pre_ping=True,   #controlla la connessione prima di ogni query
        pool_recycle=3600,   #ricicla le connessioni dopo 3600 secondi (1 ora) per evitare timeout
        echo=False,  #settings.app_debug. cosi attualmente non ho il doppio log 1 x loguru 1 x sqlalchemy
        # con collation *_SC_UTF8 (vedi init.sql) il driver ODBC 18 rifiuta i parametri
        # NVARCHAR(MAX)/Text perche' SQLAlchemy li mappa a SQL_WLONGVARCHAR (legacy ntext)
        # in setinputsizes -> "Cannot convert to text/ntext ... (4189)". Disabilitando
        # setinputsizes pyodbc torna all'inferenza di tipo di default, compatibile con UTF8.
        use_setinputsizes=False,
    )
    event.listen(engine, "checkin", _revert_impersonation_on_checkin)
    logger.info("Engine SQL Server sincrono creato")
    return engine

@lru_cache(maxsize=1)
def get_async_engine():
    from app.core.settings import get_settings
    settings = get_settings()

    async_url = settings.sqlserver_url.replace(
        "mssql+pyodbc", "mssql+aioodbc"
    )
    engine = create_async_engine(
        async_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,  #settings.app_debug,
        # vedi commento in get_sync_engine
        use_setinputsizes=False,
    )
    # gli eventi del pool si registrano sul sync_engine sottostante: asyncio non ha un
    # equivalente nativo del pool di SQLAlchemy, quindi il pooling (e i suoi eventi)
    # avviene comunque a livello sincrono anche per un AsyncEngine.
    event.listen(engine.sync_engine, "checkin", _revert_impersonation_on_checkin)
    logger.info("Engine SQL Server asincrono creato")
    return engine


tenant_db = TenantDB()








