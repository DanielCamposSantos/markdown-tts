from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


TOKEN_PATTERN = re.compile(r"c\+\+|[^\W_]+(?:-[^\W_]+)*", re.IGNORECASE | re.UNICODE)
PAUSE_PATTERN = re.compile(r"\[pause\s+[0-9]+(?:\.[0-9]+)?s\]", re.IGNORECASE)


@dataclass(frozen=True)
class NormalizedText:
    text: str
    tokens: tuple[str, ...]


def normalize_for_comparison(text: str) -> NormalizedText:
    canonical = unicodedata.normalize("NFKC", text or "").casefold()
    canonical = PAUSE_PATTERN.sub(" ", canonical)
    tokens = tuple(TOKEN_PATTERN.findall(canonical))
    return NormalizedText(text=" ".join(tokens), tokens=tokens)
