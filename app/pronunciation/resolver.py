from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Iterable, Literal

from app.speech_plan import SpeechUnit


@dataclass(frozen=True)
class PronunciationEntry:
    rule_id: str
    token: str
    mode: Literal["replace", "spell_letters"]
    spoken: str


@dataclass(frozen=True)
class AppliedPronunciationRule:
    rule_id: str
    original_token: str
    spoken: str
    start: int
    end: int

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "original_token": self.original_token,
            "spoken": self.spoken,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True)
class PronunciationResult:
    original_text: str
    synthesis_text: str
    applied_rules: tuple[AppliedPronunciationRule, ...]


@dataclass(frozen=True)
class ResolvedSpeechPlan:
    units: list[SpeechUnit]
    results: dict[int, PronunciationResult]
    metadata: dict


class PronunciationResolver:
    def __init__(self, profile: str, entries: Iterable[PronunciationEntry]) -> None:
        self.profile = profile
        ordered = sorted(tuple(entries), key=lambda entry: len(entry.token), reverse=True)
        self._entries = {entry.token.casefold(): entry for entry in ordered}
        alternatives = "|".join(re.escape(entry.token) for entry in ordered)
        # Word-character boundaries prevent matches inside identifiers such as HTTPServer.
        self._pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)

    def resolve(self, text: str) -> PronunciationResult:
        applied: list[AppliedPronunciationRule] = []

        def replacement(match: re.Match[str]) -> str:
            entry = self._entries[match.group(0).casefold()]
            applied.append(AppliedPronunciationRule(
                rule_id=entry.rule_id,
                original_token=match.group(0),
                spoken=entry.spoken,
                start=match.start(),
                end=match.end(),
            ))
            return entry.spoken

        synthesis = self._pattern.sub(replacement, text)
        return PronunciationResult(text, synthesis, tuple(applied))


def resolve_speech_plan(
    units: list[SpeechUnit], resolver: PronunciationResolver,
) -> ResolvedSpeechPlan:
    resolved_units = []
    results = {}
    applied_units = {}
    for unit in units:
        result = resolver.resolve(unit.synthesis_text)
        results[unit.index] = result
        resolved_units.append(replace(unit, synthesis_text=result.synthesis_text))
        if result.applied_rules:
            applied_units[str(unit.index)] = {
                "rules": [rule.to_dict() for rule in result.applied_rules],
            }
    return ResolvedSpeechPlan(
        units=resolved_units,
        results=results,
        metadata={"profile": resolver.profile, "applied_units": applied_units},
    )
