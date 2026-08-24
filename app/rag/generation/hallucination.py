from __future__ import annotations
from loguru import logger


async def check_faithfulness(
    question: str,
    answer: str,
    context: str,
) -> float:
    # `context` deve essere lo stesso testo effettivamente mostrato al LLM in generazione
    # (build_rag_context()["context"]), non una selezione indipendente dei chunk: altrimenti
    # il check valuta contro un contesto diverso da quello che ha davvero prodotto la risposta.
    if not context or not answer:
        return 1.0
    try:
        from app.core.llm_factory import get_llm
        from langchain_core.messages import HumanMessage

        llm = get_llm()
        response = await llm.ainvoke([
            HumanMessage(content=f"""Valuta se la RISPOSTA è completamente supportata dal CONTESTO.
                Rispondi SOLO con un numero tra 0.0 e 1.0.
                1.0 = completamente supportata, 0.0 = per niente supportata.
                CONTESTO:
                {context}
                RISPOSTA:
                {answer}
                SCORE (solo il numero):""")
        ])
        score_text = response.content.strip()
        score = float(score_text)
        score = max(0.0, min(1.0, score))
        logger.debug(f"Hallucination score: {score:.2f}")
        return score
    except Exception as e:
        # Default prudente: un check anti-allucinazione che fallisce non deve dichiarare
        # "massima fedeltà" (1.0), altrimenti disattiva silenziosamente la protezione
        # proprio quando è meno affidabile (es. LLM che non risponde con un numero puro).
        logger.warning(f"Hallucination check fallito: {e}")
        return 0.0


def is_hallucination(score: float, threshold: float = 0.5) -> bool:
    return score < threshold

