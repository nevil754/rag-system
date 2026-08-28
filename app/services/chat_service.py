from __future__ import annotations
import hashlib
import json
import time
from typing import AsyncGenerator, Any
from uuid import uuid4
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.redis_client import TenantRedis
from app.core.settings import get_settings
from app.db.sqlserver import tenant_db
from app.rag.retrieval.retriever import retrieve
from app.rag.generation.chain import arun_rag_chain, astream_rag_chain
from app.rag.generation.answer_validator import validate_answer
from app.rag.generation.hallucination import check_faithfulness, is_hallucination
from app.rag.memory.context_builder import format_sources_for_response


settings = get_settings()


class ChatService:

    def __init__(
        self,
        #db: AsyncSession,
        redis: TenantRedis,
        tenant_id: str,
        tenant_slug: str,
        user_id: str,
    ):
        #self.db = db
        self.redis = redis
        self.tenant_id = tenant_id
        self.tenant_slug = tenant_slug
        self.user_id = user_id



    async def query(
        self,
        question: str,
        conversation_id: str | None = None,
        collection_id: str | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        conv_id = conversation_id or str(uuid4())
        query_hash = _hash_query( question, conv_id, collection_id )
        cached = await self.redis.get_query_cache(query_hash)
        if cached:
            logger.debug("Cache hit per query RAG")
            cached_data = json.loads(cached)
            message_id = await self._save_messages(
                conv_id=conv_id,
                question=question,
                answer=cached_data.get("answer", ""),
                sources=cached_data.get("sources", []),
                tokens_in=cached_data.get("tokens_in") or 0,
                tokens_out=cached_data.get("tokens_out") or 0,
                latency_ms=cached_data.get("latency_ms") or 0,
                hallucination_score=None,   #cache hit: nessuna chiamata LLM, nessun nuovo check allucinazioni da salvare
            )
            await self.redis.append_message(conv_id, {
                "role": "user", "content": question
            }, settings.memory_short_term_turns)
            await self.redis.append_message(conv_id, {
                "role": "assistant", "content": cached_data.get("answer", "")
            }, settings.memory_short_term_turns)
            await self._increment_usage_stats(
                tokens_in=cached_data.get("tokens_in") or 0,
                tokens_out=cached_data.get("tokens_out") or 0,
            )
            cached_data["conversation_id"] = conv_id
            cached_data["message_id"] = message_id
            return cached_data
        
        session_messages = await self.redis.get_session(conv_id)
        chunks = await retrieve(
            query=question,
            tenant_slug=self.tenant_slug,
            tenant_id=self.tenant_id,
            collection_id=collection_id,
        )
        result = await arun_rag_chain(
            question=question,
            chunks=chunks,
            session_messages=session_messages,
            tenant_name=self.tenant_slug,
        )

        validation = validate_answer(result["answer"], question)
        if validation.was_modified:
            result["answer"] = validation.answer
            logger.debug("Risposta corretta dal validator", issues=validation.issues)

        hall_score = await check_faithfulness(question, result["answer"], result.get("context", ""))
        if is_hallucination(hall_score):
            logger.warning(
                "Potenziale allucinazione rilevata",
                score=hall_score,
                question=question[:80],
            )
        message_id = await self._save_messages(
            conv_id=conv_id,
            question=question,
            answer=result["answer"],
            sources=result["sources"],
            tokens_in=result.get("tokens_in", 0),
            tokens_out=result.get("tokens_out", 0),
            latency_ms=result.get("latency_ms", 0),
            hallucination_score=hall_score,
        )
        await self.redis.append_message( conv_id, {
            "role": "user", "content": question
        }, settings.memory_short_term_turns)

        await self.redis.append_message(conv_id, {
            "role": "assistant", "content": result["answer"]
        }, settings.memory_short_term_turns)

        response = {
            "answer": result["answer"],
            "conversation_id": conv_id,
            "message_id": message_id,
            "sources": result["sources"],
            "tokens_in": result.get("tokens_in"),
            "tokens_out": result.get("tokens_out"),
            "latency_ms": result.get("latency_ms"),
            "hallucination_score": round(hall_score, 3),
        }
        await self.redis.set_query_cache( query_hash, json.dumps(response) )
        await self._increment_usage_stats(
            tokens_in=result.get("tokens_in", 0),
            tokens_out=result.get("tokens_out", 0),
        )
        return response


    async def stream_query(
        self,
        question: str,
        conversation_id: str | None = None,
        collection_id: str | None = None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Yielda tuple (kind, payload): ("token", str) per ogni pezzo di risposta, poi
        esattamente un ("meta", dict) prima di terminare. Canale strutturato — non più un
        carattere sentinella (\\x1e) nel testo, che un token contenente per coincidenza
        quello stesso carattere avrebbe rotto (interpretato come inizio dei metadata)."""
        conv_id = conversation_id or str(uuid4())
        query_hash = _hash_query(question, conv_id, collection_id)
        cached = await self.redis.get_query_cache(query_hash)
        if cached:
            logger.debug("Cache hit per query RAG (streaming)")
            cached_data = json.loads(cached)
            cached_answer = cached_data.get("answer", "")
            yield ("token", cached_answer)

            await self._save_messages(
                conv_id=conv_id,
                question=question,
                answer=cached_answer,
                sources=cached_data.get("sources", []),
                latency_ms=cached_data.get("latency_ms") or 0,
                hallucination_score=None,   #cache hit: nessuna chiamata LLM, nessun nuovo check allucinazioni da salvare
            )
            await self.redis.append_message(conv_id, {"role": "user", "content": question}, settings.memory_short_term_turns)
            await self.redis.append_message(conv_id, {"role": "assistant", "content": cached_answer}, settings.memory_short_term_turns)
            await self._increment_usage_stats(tokens_in=0, tokens_out=0)

            yield ("meta", {
                "sources": cached_data.get("sources", []),
                "conversation_id": conv_id,
                "latency_ms": cached_data.get("latency_ms"),
                "hallucination_score": cached_data.get("hallucination_score"),
                "answer": cached_answer,
            })
            return
        session_messages = await self.redis.get_session(conv_id)
        chunks = await retrieve(
            query=question,
            tenant_slug=self.tenant_slug,
            tenant_id=self.tenant_id,
            collection_id=collection_id,
        )
        start = time.time()
        full_answer = ""
        context_used = ""
        tokens_in = 0
        tokens_out = 0
        async for kind, payload in astream_rag_chain(
            question=question,
            chunks=chunks,
            session_messages=session_messages,
            tenant_name=self.tenant_slug,
        ):
            if kind == "token":
                full_answer += payload
                yield ("token", payload)
            else:  # "final"
                context_used = payload.get("context", "")
                tokens_in = payload.get("tokens_in", 0)
                tokens_out = payload.get("tokens_out", 0)
        latency_ms = round((time.time() - start) * 1000)

        validation = validate_answer(full_answer, question)
        if validation.was_modified:
            full_answer = validation.answer
            logger.debug("Risposta streaming corretta dal validator", issues=validation.issues)

        hall_score = await check_faithfulness(question, full_answer, context_used)
        if is_hallucination(hall_score):
            logger.warning(
                "Potenziale allucinazione rilevata (streaming)",
                score=hall_score,
                question=question[:80],
            )
        await self._save_messages(
            conv_id=conv_id,
            question=question,
            answer=full_answer,
            sources=format_sources_for_response(chunks),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            hallucination_score=hall_score,
        )
        await self.redis.append_message(conv_id, {"role": "user", "content": question}, settings.memory_short_term_turns)
        await self.redis.append_message(conv_id, {"role": "assistant", "content": full_answer}, settings.memory_short_term_turns)
        sources = format_sources_for_response(chunks)
        response_to_cache = {
            "answer": full_answer,
            "conversation_id": conv_id,
            "sources": sources,
            "latency_ms": latency_ms,
            "hallucination_score": round(hall_score, 3),
        }
        await self.redis.set_query_cache(query_hash, json.dumps(response_to_cache))   #ok attualmente non gli passo il ttl il time-to-live
        # tokens_in/tokens_out reali quando il provider li espone in streaming (OpenAI/Google),
        # 0 quando non disponibili (es. Ollama) — comunque non più sempre hardcoded a 0.
        await self._increment_usage_stats(tokens_in=tokens_in, tokens_out=tokens_out)
        yield ("meta", {
            "sources": sources,
            "conversation_id": conv_id,
            "latency_ms": latency_ms,
            "hallucination_score": round(hall_score, 3),
            "answer": full_answer,   #post-validate_answer(): puo differire dai token grezzi gia streammati se il validator ha corretto la risposta
        })


    async def get_history(
        self,
        conversation_id: str | None = None,
        before_id: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        async with tenant_db.aget_session(self.tenant_slug) as session:
            if not conversation_id:
                row = (await session.execute(
                    text("""
                        SELECT TOP 1 id FROM conversations
                        WHERE user_id = :user_id
                        ORDER BY updated_at DESC
                    """),
                    {"user_id": self.user_id}
                )).fetchone()
                if not row:
                    return {"conversation_id": None, "messages": [], "has_more": False}
                conversation_id = str(row[0])

            params: dict[str, Any] = {
                "conv_id": conversation_id,
                "user_id": self.user_id,
                "limit": limit + 1,
            }
            before_clause = ""
            if before_id is not None:
                before_clause = "AND m.id < :before_id"
                params["before_id"] = before_id

            # +1 rispetto al limit richiesto: serve solo a sapere se esiste un'altra pagina
            # (has_more), non viene mai restituito al chiamante.
            result = await session.execute(
                text(f"""
                    SELECT TOP (:limit) m.id, m.role, m.content, m.sources, m.created_at, m.hallucination_score
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.conversation_id = :conv_id AND c.user_id = :user_id {before_clause}
                    ORDER BY m.id DESC
                """),
                params
            )
            rows = result.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            rows.reverse()  # dal piu' vecchio al piu' recente, per il rendering della pagina

            messages = [
                {
                    "id": r.id,
                    "role": r.role,
                    "content": r.content,
                    "sources": json.loads(r.sources) if r.sources else [],
                    "created_at": r.created_at,
                    "hallucination_score": r.hallucination_score,
                }
                for r in rows
            ]
            return {"conversation_id": conversation_id, "messages": messages, "has_more": has_more}


    async def _save_messages(
        self,
        conv_id: str,
        question: str,
        answer: str,
        sources: list[dict],
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        hallucination_score: float | None = None,
    ) -> int:
        # from app.core.settings import get_settings
        # settings = get_settings()
        async with tenant_db.aget_session(self.tenant_slug) as session:
            await session.execute(
                text("""
                    IF NOT EXISTS (SELECT 1 FROM conversations WHERE id = :id)
                    INSERT INTO conversations (id, user_id, mode)
                    VALUES (:id, :user_id, 'rag')
                    ELSE
                    UPDATE conversations SET updated_at = SYSUTCDATETIME() WHERE id = :id
                """),
                {"id": conv_id, "user_id": self.user_id}
            )
            await session.execute(
                text("""
                    INSERT INTO messages (conversation_id, role, content)
                    VALUES (:conv_id, 'user', :content)   
                """),
                {"conv_id": conv_id, "content": question}
            )  #TODO usa di default 'user'! check in future all ok

            result = await session.execute(
                text("""
                    INSERT INTO messages
                        (conversation_id, role, content, sources, tokens_in, tokens_out, latency_ms, hallucination_score)
                    OUTPUT INSERTED.id
                    VALUES (:conv_id, 'assistant', :content, :sources, :tokens_in, :tokens_out, :latency_ms, :hall_score)
                """),
                {
                    "conv_id": conv_id,
                    "content": answer,
                    "sources": json.dumps(sources),
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "latency_ms": latency_ms,
                    "hall_score": hallucination_score,
                }
            )
            row = result.fetchone()
            return row[0] if row else 0


    async def _increment_usage_stats( self, tokens_in: int, tokens_out: int ) -> None:
        from datetime import date
        today = date.today().isoformat()
        base = f"tenant:{self.tenant_id}:stats:{today}"
        pipe = self.redis._redis.pipeline()

        pipe.incrby(f"{base}:tokens_in", tokens_in)
        pipe.incrby(f"{base}:tokens_out", tokens_out)
        pipe.incr(f"{base}:queries")

        pipe.expire(f"{base}:tokens_in", 172800)
        pipe.expire(f"{base}:tokens_out", 172800)
        pipe.expire(f"{base}:queries", 172800)
        await pipe.execute()


def _hash_query(question: str, conv_id: str, collection_id: str | None = None) -> str:
    normalized = question.strip().lower()
    return hashlib.md5(f"{conv_id}:{collection_id or ''}:{normalized}".encode()).hexdigest()  #hasha usando MD5 per ottenere un hash unico della query e della conversazione

