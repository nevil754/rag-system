from __future__ import annotations
import json
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import CurrentDB, CurrentRedis, CurrentTenant
from app.schemas.chat import ChatRequest, ChatResponse, FeedbackRequest
from app.services.chat_service import ChatService


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse)
async def chat_query(
    request: ChatRequest,
    tenant: CurrentTenant,
    #db: CurrentDB,
    redis: CurrentRedis,
) -> ChatResponse:
    logger.info(
        "Chat query ricevuta",
        tenant_id=tenant.tenant_id,
        user_id=tenant.user_id,
        conversation_id=request.conversation_id,
        collection_id=request.collection_id,
        question_len=len(request.question),
    )
    service = ChatService(
        #db=db,
        redis=redis,
        tenant_id=tenant.tenant_id,
        tenant_slug=tenant.tenant_slug,
        user_id=tenant.user_id,
    )
    try:
        result = await service.query(
            question=request.question,
            conversation_id=request.conversation_id,
            collection_id=request.collection_id,
        )
    except Exception as e:
        logger.error(
            "Chat query fallita",
            tenant_id=tenant.tenant_id, user_id=tenant.user_id, error=str(e),
        )
        raise
    logger.info(
        "Chat query completata",
        tenant_id=tenant.tenant_id,
        conversation_id=result.get("conversation_id"),
        message_id=result.get("message_id"),
        sources=len(result.get("sources") or []),
        latency_ms=result.get("latency_ms"),
    )    
    return ChatResponse(**result)

    

@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    tenant: CurrentTenant,
    #db: CurrentDB,
    redis: CurrentRedis,
) -> StreamingResponse:
    
    logger.info(
        "Chat stream ricevuta",
        tenant_id=tenant.tenant_id,
        user_id=tenant.user_id,
        conversation_id=request.conversation_id,
        collection_id=request.collection_id,
        question_len=len(request.question),
    )
    service = ChatService(
        #db=db,
        redis=redis,
        tenant_id=tenant.tenant_id,
        tenant_slug=tenant.tenant_slug,
        user_id=tenant.user_id,
    )
    async def event_generator():
        meta: dict = {}
        try:
            async for kind, payload in service.stream_query(
                question=request.question,
                conversation_id=request.conversation_id,
                collection_id=request.collection_id,
            ):
                if kind == "meta":
                    meta = payload
                else:
                    yield f"data: {json.dumps({'token': payload})}\n\n"
            yield f"data: {json.dumps({'done': True, **meta})}\n\n"
            logger.info(
                "Chat stream completata",
                tenant_id=tenant.tenant_id,
                conversation_id=meta.get("conversation_id"),
                sources=len(meta.get("sources") or []),
                latency_ms=meta.get("latency_ms"),
            )

        except Exception as e:
            logger.error(
                "Errore streaming",
                tenant_id=tenant.tenant_id, user_id=tenant.user_id, error=str(e),
            )
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/feedback")
async def submit_feedback(
    request: FeedbackRequest,
    tenant: CurrentTenant,
    db: CurrentDB,
) -> dict:
    from sqlalchemy import text
    await db.execute(
        text("""
            INSERT INTO message_feedback (message_id, user_id, rating, comment)
            VALUES (:msg_id, :user_id, :rating, :comment)
        """),
        {
            "msg_id": request.message_id,
            "user_id": tenant.user_id,
            "rating": request.rating,
            "comment": request.comment,
        }
    )
    logger.info(
        "Feedback salvato",
        tenant_id=tenant.tenant_id, user_id=tenant.user_id,
        message_id=request.message_id, rating=request.rating,
    )
    return {"message": "Feedback salvato"}

