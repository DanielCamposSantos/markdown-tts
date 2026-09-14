from __future__ import annotations

from typing import Any, Sequence


SUPPORTED_PLAYBACK_RATES = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


def unit_id(entry: dict[str, Any]) -> int:
    return int(entry.get("unit_id", entry.get("index")))


def timeline_index_at(timeline: Sequence[dict[str, Any]], position: float) -> int | None:
    if not timeline:
        return None
    position = max(0.0, float(position))
    for index, entry in enumerate(timeline):
        next_start = (
            float(timeline[index + 1]["start_seconds"])
            if index + 1 < len(timeline)
            else float("inf")
        )
        if position < next_start:
            return index
    return len(timeline) - 1


def active_unit_at(timeline: Sequence[dict[str, Any]], position: float) -> int | None:
    index = timeline_index_at(timeline, position)
    return None if index is None else unit_id(timeline[index])


def adjacent_unit(
    timeline: Sequence[dict[str, Any]], active_unit_id: int | None, direction: int
) -> dict[str, Any] | None:
    if not timeline or direction not in {-1, 1}:
        return None
    ids = [unit_id(entry) for entry in timeline]
    try:
        index = ids.index(active_unit_id)
    except ValueError:
        return None
    target = index + direction
    return timeline[target] if 0 <= target < len(timeline) else None
