from __future__ import annotations
import asyncio
from functools import lru_cache
from typing import Any
from loguru import logger


@lru_cache(maxsize=1)
def get_embedding_model() -> Any:
    from app.core.settings import get_settings
    settings = get_settings()
    from fastembed import TextEmbedding
    logger.info(
        "Caricamento modello embedding",
        model=settings.embeddings_model,
        cache_dir=settings.embeddings_cache_dir,
    )
    model = TextEmbedding(
        model_name=settings.embeddings_model,
        cache_dir=settings.embeddings_cache_dir,
        max_length=512,   #ma xlenght context window
        threads=4,   #threads per processare i testi
    )
    logger.info("Modello embedding caricato", model=settings.embeddings_model)
    return model

def embed_texts( texts: list[str] ) -> list[list[float]]:
    from app.core.settings import get_settings
    settings = get_settings()
    model = get_embedding_model()
    batch_size = settings.embeddings_batch_size
    logger.debug(f"Embedding {len(texts)} testi in batch da {batch_size}")

    vectors = list( model.embed(texts, batch_size=batch_size) )

    return [ v.tolist() for v in vectors ]

def embed_query(text: str) -> list[float]:
    model = get_embedding_model()
    vectors = list( model.query_embed([text]) )
    return vectors[0].tolist()   #intanto con 'text' in input hai solo 1 , quindi crea solo 1 vettore

async def aembed_texts(texts: list[str]) -> list[list[float]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_texts, texts)

async def aembed_query(text: str) -> list[float]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_query, text)


@lru_cache(maxsize=1)
def get_sparse_model() -> Any:
    from fastembed import SparseTextEmbedding
    logger.info("Caricamento modello sparse SPLADE", model="prithivida/Splade_PP_en_v1")
    return SparseTextEmbedding(model_name="prithivida/Splade_PP_en_v1")

def embed_sparse_texts(texts: list[str]) -> list[dict]:
    model = get_sparse_model()
    vectors = list(model.embed(texts))
    return [{"indices": v.indices.tolist(), "values": v.values.tolist()} for v in vectors]


def embed_sparse_query(text: str) -> dict:
    return embed_sparse_texts([text])[0]

async def aembed_sparse_query(text: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_sparse_query, text)



def get_embedding_dimension() -> int:
    model = get_embedding_model()
    test_vector = list(model.embed(["test"]))[0]   #dummy x ottenere la dimension del vettore
    return len(test_vector)


@lru_cache(maxsize=1)
def get_reranker_model() -> Any:
    from app.core.settings import get_settings
    settings = get_settings()
    if not settings.reranker_enabled:
        return None
    from sentence_transformers import CrossEncoder  #better than (è un'altra lib) from fastembed.rerank.cross_encoder import CrossEncoder (è offerta da fastembed)
    logger.info("Caricamento reranker", model=settings.reranker_model)
    reranker = CrossEncoder(
        settings.reranker_model,
        max_length=512,   #ma xlenght context window
    )
    #assicurati che cerchi il model 'models--BAAI--bge-reranker-base' in /cache/fastembed/, invece di default /cache/fastembed/.cache/huggingface/hub/ !!
    logger.info("Reranker caricato", model=settings.reranker_model)
    return reranker


