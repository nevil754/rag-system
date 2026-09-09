from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from typing import Any
import yaml
from loguru import logger
from app.core.settings import get_settings


def get_rag_system_prompt(tenant_name: str = "Compet-e Compliance AI") -> str:
    prompts = _load_prompts()
    template = prompts.get("system", {}).get("base", _DEFAULT_SYSTEM)
    return template.format( tenant_name=tenant_name )


def get_rag_user_prompt(
    context: str,
    history: str,
    question: str,
) -> str:
    prompts = _load_prompts()
    template = prompts.get("rag", {}).get("main", _DEFAULT_RAG)
    return template.format(   #replace i segnaposto ({...}) presenti in una stringa con i valori che gli passi
        context=context,
        history=history,
        question=question,
    )


def get_no_context_message() -> str:
    prompts = _load_prompts()
    return prompts.get("rag", {}).get("no_context", _DEFAULT_NO_CONTEXT)


@lru_cache(maxsize=1)
def _load_prompts() -> dict:
    prompt_file = Path(get_settings().prompts_config_file)
    if not prompt_file.exists():
        logger.warning("prompts.yaml non trovato, uso prompt hardcodati")
        return {}
    with open(prompt_file) as f:
        return yaml.safe_load(f) or {}


_DEFAULT_SYSTEM = """Sei un assistente legale AI per {tenant_name}.
Rispondi sempre in italiano a meno che l'utente non scriva in un'altra lingua.
Sei preciso, professionale e citi sempre le fonti dei documenti.
Non inventare mai informazioni che non sono nei documenti forniti.
Se non trovi la risposta nei documenti, dillo esplicitamente.

Scrivi risposte curate, organizzate e professionali, come farebbe un consulente esperto:
- Formatta sempre in Markdown: titoli (##), elenchi puntati o numerati, **grassetto** sui concetti chiave. Non scrivere mai un unico blocco di testo non formattato.
- Apri con la risposta diretta alla domanda in 1-2 frasi, poi approfondisci con i dettagli organizzati per punti o sezioni.
- Usa una tabella quando devi confrontare più voci (articoli, scadenze, importi, requisiti).
- Cita le fonti in modo sintetico con [N] (es. [1], [2]), dove N è il numero della fonte indicata nei documenti forniti: non ripetere nome file e pagina nel testo, sono già mostrati separatamente.
- Evita frasi di riempimento, ripetizioni e formule di cortesia superflue ("Certamente!", "Spero sia utile", "Fammi sapere se hai altre domande")."""

_DEFAULT_RAG = """Usa ESCLUSIVAMENTE le seguenti sezioni di documenti per rispondere.
Cita ogni informazione con il numero della fonte corrispondente tra parentesi quadre (es. [1], [2]), riferendoti alla numerazione "Fonte N" indicata sotto.
Se la risposta non è nei documenti, dì: "Non ho trovato questa informazione nei documenti."

Struttura la risposta in Markdown, chiara e professionale: apri con la risposta diretta, poi organizza i dettagli con titoli, elenchi o tabelle quando utile.

DOCUMENTI:
{context}

STORICO CONVERSAZIONE:
{history}

DOMANDA: {question}

RISPOSTA:"""

_DEFAULT_NO_CONTEXT = ("Non ho trovato documenti rilevanti per rispondere. "  "Prova a riformulare la domanda o carica i documenti pertinenti.")
