from __future__ import annotations

import csv
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from app.validation.asr import AsrEngine
from app.validation.validator import ValidationConfig, validate_audio


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    audio_path: Path
    expected_text: str
    duration_seconds: float
    category: str = "general"


@dataclass(frozen=True)
class BenchmarkEntry:
    case_id: str
    category: str
    status: str
    similarity_score: float
    token_coverage: float
    audio_duration_seconds: float
    processing_seconds: float | None
    real_time_factor: float | None
    backend: str
    model: str | None
    ram_mb: float | None = None
    vram_mb: float | None = None


@dataclass(frozen=True)
class BenchmarkReport:
    entries: tuple[BenchmarkEntry, ...]
    summary: dict

    def to_dict(self) -> dict:
        return {"entries": [asdict(entry) for entry in self.entries], "summary": self.summary}


def load_corpus(path: Path) -> tuple[BenchmarkCase, ...]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    cases = []
    for item in payload["cases"]:
        audio = Path(item["audio_path"])
        cases.append(BenchmarkCase(
            case_id=item["id"],
            audio_path=audio if audio.is_absolute() else source.parent / audio,
            expected_text=item["expected_text"],
            duration_seconds=float(item["duration_seconds"]),
            category=item.get("category", "general"),
        ))
    return tuple(cases)


def run_benchmark(
    cases: Iterable[BenchmarkCase],
    engine: AsrEngine,
    *,
    config: ValidationConfig | None = None,
    metrics: Callable[[], dict] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkReport:
    entries = []
    for case in cases:
        started = clock()
        validation = validate_audio(case.expected_text, case.audio_path, engine, config)
        measured = max(0.0, clock() - started)
        processing = validation.processing_seconds
        if processing is None or not math.isfinite(processing) or processing < 0:
            processing = measured
        rtf = processing / case.duration_seconds if processing is not None and processing >= 0 and case.duration_seconds > 0 else None
        resource = metrics() if metrics is not None else {}
        entries.append(BenchmarkEntry(
            case_id=case.case_id,
            category=case.category,
            status=validation.status,
            similarity_score=validation.similarity_score,
            token_coverage=validation.token_coverage,
            audio_duration_seconds=case.duration_seconds,
            processing_seconds=processing,
            real_time_factor=rtf if rtf is None or math.isfinite(rtf) else None,
            backend=validation.backend,
            model=validation.model,
            ram_mb=resource.get("ram_mb"),
            vram_mb=resource.get("vram_mb"),
        ))
    counts = {status: sum(entry.status == status for entry in entries) for status in ("pass", "warn", "fail")}
    valid_rtfs = [entry.real_time_factor for entry in entries if entry.real_time_factor is not None]
    summary = {
        "cases": len(entries),
        "status_counts": counts,
        "mean_rtf": sum(valid_rtfs) / len(valid_rtfs) if valid_rtfs else None,
    }
    return BenchmarkReport(tuple(entries), summary)


def _atomic_text(path: Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    os.replace(temporary, target)


def write_json_report(report: BenchmarkReport, path: Path) -> None:
    _atomic_text(path, json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


def write_csv_report(report: BenchmarkReport, path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    fields = tuple(BenchmarkEntry.__dataclass_fields__)
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(entry) for entry in report.entries)
    os.replace(temporary, target)
