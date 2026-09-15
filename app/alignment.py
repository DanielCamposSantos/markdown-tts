from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Iterable, Mapping

from app.validation.asr import AsrResult, AsrWord


ALIGNMENT_SCHEMA_VERSION = 1
DISPLAY_TOKEN_PATTERN = re.compile(r"C\+\+|[\wÀ-ÿ]+(?:[-/][\wÀ-ÿ]+)*", re.UNICODE | re.IGNORECASE)


def normalized(value: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(character) and character.isalnum())


@dataclass(frozen=True)
class AlignedDisplayToken:
    display_start: int
    display_end: int
    text: str
    start_seconds: float | None
    end_seconds: float | None
    status: str
    source_asr_word_indices: tuple[int, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["source_asr_word_indices"] = list(self.source_asr_word_indices)
        return data


def _rule_for_token(token, rules, used):
    for index, rule in enumerate(rules):
        if index not in used and normalized(rule.original_token) == normalized(token.group(0)):
            used.add(index)
            return rule
    return None


def _matches(actual: list[str], expected: list[str]) -> bool:
    return len(actual) == len(expected) and all(
        left == right or SequenceMatcher(None, left, right).ratio() >= 0.82
        for left, right in zip(actual, expected)
    )


def align_unit(unit_id: int, display_text: str, result: AsrResult, applied_rules: Iterable = ()) -> dict:
    words = tuple(word for word in result.words if word.start_seconds is not None and word.end_seconds is not None)
    if not words:
        return {"unit_id": unit_id, "status": "unavailable", "tokens": []}
    normalized_words = [normalized(word.text) for word in words]
    cursor, aligned, used_rules = 0, [], set()
    rules = tuple(applied_rules)
    for token in DISPLAY_TOKEN_PATTERN.finditer(display_text):
        rule = _rule_for_token(token, rules, used_rules)
        alternatives = [[normalized(token.group(0))]]
        if rule is not None:
            spoken = [normalized(item.group(0)) for item in DISPLAY_TOKEN_PATTERN.finditer(rule.spoken)]
            if spoken:
                alternatives.insert(0, spoken)
        match = None
        for start in range(cursor, len(words)):
            for expected in alternatives:
                actual = normalized_words[start:start + len(expected)]
                if _matches(actual, expected):
                    match = (start, start + len(expected))
                    break
            if match:
                break
        if match:
            start, end = match
            aligned.append(AlignedDisplayToken(token.start(), token.end(), token.group(0), words[start].start_seconds, words[end - 1].end_seconds, "aligned", tuple(range(start, end))).to_dict())
            cursor = end
        else:
            aligned.append(AlignedDisplayToken(token.start(), token.end(), token.group(0), None, None, "unaligned").to_dict())
    count = sum(token["status"] == "aligned" for token in aligned)
    status = "aligned" if aligned and count == len(aligned) else ("partial" if count else "unavailable")
    return {"unit_id": unit_id, "status": status, "tokens": aligned}


def build_global_alignment(units, pronunciation_results: Mapping[int, object], asr_results: Mapping[int, AsrResult], timeline) -> dict:
    timeline_by_id = {int(entry.index if hasattr(entry, "index") else entry["index"]): entry for entry in timeline}
    aligned_units = []
    for unit in units:
        pronunciation = pronunciation_results.get(unit.index)
        local = align_unit(unit.index, unit.display_text, asr_results.get(unit.index, AsrResult("")), getattr(pronunciation, "applied_rules", ()))
        entry = timeline_by_id[unit.index]
        offset = float(entry.start_seconds if hasattr(entry, "start_seconds") else entry["start_seconds"])
        unit_end = float(entry.end_seconds if hasattr(entry, "end_seconds") else entry["end_seconds"])
        for token in local["tokens"]:
            if token["start_seconds"] is not None:
                token["start_seconds"] = max(offset, token["start_seconds"] + offset)
                token["end_seconds"] = min(unit_end, token["end_seconds"] + offset)
        aligned_units.append(local)
    statuses = [unit["status"] for unit in aligned_units]
    status = "complete" if statuses and all(value == "aligned" for value in statuses) else ("partial" if any(value in {"aligned", "partial"} for value in statuses) else "not_available")
    return {"schema_version": ALIGNMENT_SCHEMA_VERSION, "status": status, "units": aligned_units}


def rebase_alignment(previous: dict | None, old_timeline, new_timeline, target_unit_id: int, target_local: dict | None) -> dict:
    previous_units = {int(unit["unit_id"]): unit for unit in (previous or {}).get("units", [])}
    old_offsets = {int(item["index"]): float(item["start_seconds"]) for item in old_timeline}
    new_offsets = {int(item["index"]): float(item["start_seconds"]) for item in new_timeline}
    new_ends = {int(item["index"]): float(item["end_seconds"]) for item in new_timeline}
    output = []
    for unit_id in new_offsets:
        if unit_id == target_unit_id:
            unit = target_local or {"unit_id": unit_id, "status": "unavailable", "tokens": []}
            unit = {**unit, "tokens": [dict(token) for token in unit["tokens"]]}
            shift = new_offsets[unit_id]
        elif unit_id in previous_units:
            unit = {**previous_units[unit_id], "tokens": [dict(token) for token in previous_units[unit_id].get("tokens", [])]}
            shift = new_offsets[unit_id] - old_offsets.get(unit_id, new_offsets[unit_id])
        else:
            unit, shift = {"unit_id": unit_id, "status": "unavailable", "tokens": []}, 0
        for token in unit["tokens"]:
            if token.get("start_seconds") is not None:
                token["start_seconds"] += shift
                token["end_seconds"] = min(new_ends[unit_id], token["end_seconds"] + shift)
        output.append(unit)
    statuses = [unit["status"] for unit in output]
    status = "complete" if statuses and all(value == "aligned" for value in statuses) else ("partial" if any(value in {"aligned", "partial"} for value in statuses) else "not_available")
    return {"schema_version": ALIGNMENT_SCHEMA_VERSION, "status": status, "units": output}
