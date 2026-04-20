"""Built-in personas + loader.

Each persona has a short label, a brief tagline, and a backing
``.md`` file under ``clawmes/data/personas/<name>.md`` that the prompt
builder hook injects into the LLM's per-turn user message context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_PERSONAS_DIR = Path(__file__).parent.parent / "data" / "personas"


@dataclass(frozen=True)
class Persona:
    name: str
    tagline: str
    snippet_path: Path

    def load_snippet(self) -> str:
        if not self.snippet_path.exists():
            return ""
        return self.snippet_path.read_text(encoding="utf-8")


PERSONAS: dict[str, Persona] = {
    "professional": Persona(
        name="professional",
        tagline="Clear, concise, business-like. Stick to facts and figures.",
        snippet_path=_PERSONAS_DIR / "professional.md",
    ),
    "degen": Persona(
        name="degen",
        tagline="CT native. Crypto twitter energy. Use the vernacular.",
        snippet_path=_PERSONAS_DIR / "degen.md",
    ),
    "chill": Persona(
        name="chill",
        tagline="Relaxed, friendly. Like texting a knowledgeable friend.",
        snippet_path=_PERSONAS_DIR / "chill.md",
    ),
    "technical": Persona(
        name="technical",
        tagline="Data-heavy. RSI, TVL, gas costs, pool details.",
        snippet_path=_PERSONAS_DIR / "technical.md",
    ),
    "mentor": Persona(
        name="mentor",
        tagline="Educational. Explain DeFi concepts as you go.",
        snippet_path=_PERSONAS_DIR / "mentor.md",
    ),
}


def get_persona(name: str | None) -> Persona | None:
    if not name:
        return None
    return PERSONAS.get(name.lower())
