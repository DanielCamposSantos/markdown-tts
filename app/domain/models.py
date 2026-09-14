from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class TimelineEntry:
    index: int
    kind: str
    text: str
    start_seconds: float
    end_seconds: float
    pause_after_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlaybackState:
    generation_id: str
    position_seconds: float
    active_unit_id: int | None
    playback_rate: float
    updated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationProgress:
    phase: str
    current: int
    total: int
    message: str
    progress: float
    eta_seconds: float | None = None
    elapsed_seconds: float = 0.0
    phase_progress: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationResult:
    output_file: Path
    duration_seconds: float
    generation_seconds: float
    decode_seconds: float
    timeline: tuple[TimelineEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_file"] = str(self.output_file)
        return data
