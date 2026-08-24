from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any
from langchain.text_splitter import MarkdownTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger
from app.core.settings import get_settings

settings = get_settings()


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_document(
    text: str,
    pages: list[str] | None = None,
    base_metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    strategy = settings.ingestion_chunk_strategy
    chunk_size = settings.ingestion_chunk_size
    overlap = settings.ingestion_chunk_overlap
    if strategy == "markdown":
        splitter = MarkdownTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
        )
    else:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
    raw_chunks = splitter.split_text(text)

    min_size = max(50, chunk_size // 20)
    filtered_chunks = [ c for c in raw_chunks if len(c.strip()) >= min_size ]
    discarded = len(raw_chunks) - len(filtered_chunks)
    raw_chunks = filtered_chunks
    chunks: list[Chunk] = []
    for i, chunk_text in enumerate(raw_chunks):
        page_num = _find_page_number(chunk_text, pages) if pages else None
        chunks.append( Chunk(
            text=chunk_text.strip(),
            chunk_index=i,
            page_number=page_num,
            metadata={ **(base_metadata or {}), "chunk_index": i },
        ))
    logger.debug(
        "Chunking completato",
        strategy=strategy,
        chunk_size=chunk_size,
        overlap=overlap,
        chunk_count=len(chunks),
        discarded_too_short=discarded,
    )
    return chunks


_MARKDOWN_NOISE_RE = re.compile(r"[#*_`|>-]")


def _normalize_for_page_match(text: str) -> str:
    # text va cucito dal markdown export di Docling (usato per i chunk), pages dal testo
    # grezzo per-elemento (usato per il lookup pagina): sintassi markdown (#, **, |, ecc.)
    # presente solo nel primo e assente nel secondo faceva fallire quasi sempre il substring
    # match. Rimuovendo questi caratteri e normalizzando gli spazi su entrambi i lati, il
    # confronto avviene sullo stesso "testo nudo".
    text = _MARKDOWN_NOISE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _find_page_number( chunk_text: str, pages: list[str] ) -> int | None:
    probe = _normalize_for_page_match(chunk_text)[:80]
    if not probe:
        return None
    for i, page_text in enumerate(pages, 1):
        if probe in _normalize_for_page_match(page_text):
            return i
    return None


