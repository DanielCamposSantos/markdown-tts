from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.persistence.database import Database


ACTIVE = {"queued", "running", "cancelling"}


@dataclass(frozen=True)
class RetentionCandidate:
    path: str
    kind: str
    age_seconds: float
    reason: str


@dataclass(frozen=True)
class RetentionReport:
    candidates: tuple[RetentionCandidate, ...]
    removed: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"candidates": [asdict(x) for x in self.candidates], "removed": list(self.removed)}


class RetentionService:
    def __init__(self, library_root: Path, *, grace_hours: float = 24, now=None) -> None:
        self.root = Path(library_root).resolve()
        self.database = Database(self.root / "library.db")
        self.grace = timedelta(hours=grace_hours)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _safe(self, path: Path) -> bool:
        if path.is_symlink():
            return False
        try:
            path.resolve().relative_to(self.root)
        except ValueError:
            return False
        return all(not parent.is_symlink() for parent in path.parents if parent != self.root.parent)

    def _inactive(self, generation_id: str, job_id: str | None = None) -> bool:
        with self.database.connect() as connection:
            if job_id:
                row = connection.execute("SELECT status FROM generation_jobs WHERE job_id=?", (job_id,)).fetchone()
                return bool(row and row[0] not in ACTIVE)
            row = connection.execute(
                "SELECT COUNT(*) FROM generation_jobs WHERE generation_id=? AND status IN ('queued','running','cancelling')",
                (generation_id,),
            ).fetchone()
        return row[0] == 0

    def scan(self) -> RetentionReport:
        candidates: list[RetentionCandidate] = []
        if not (self.root / "library.db").is_file():
            return RetentionReport(())
        cutoff = self.now().timestamp() - self.grace.total_seconds()
        for path in self.root.glob("*/*/.audio.staging.mp3"):
            self._consider(candidates, path, "generation_staging", path.parent.name, None, cutoff)
        for path in self.root.glob("*/*/.units.staging"):
            self._consider(candidates, path, "unit_staging", path.parent.name, None, cutoff)
        for path in self.root.glob("*/*/.regeneration-*"):
            self._consider(candidates, path, "regeneration_staging", path.parent.name, path.name.removeprefix(".regeneration-"), cutoff)
        return RetentionReport(tuple(sorted(candidates, key=lambda x: x.path)))

    def _consider(self, output, path, kind, generation_id, job_id, cutoff):
        if self._safe(path) and path.stat().st_mtime <= cutoff and self._inactive(generation_id, job_id):
            output.append(RetentionCandidate(path.relative_to(self.root).as_posix(), kind, self.now().timestamp() - path.stat().st_mtime, "staging conhecido, antigo e sem job ativo"))

    def cleanup_safe(self, *, apply: bool = False) -> RetentionReport:
        report = self.scan()
        if not apply:
            return report
        removed = []
        for candidate in report.candidates:
            path = self.root / candidate.path
            if not self._safe(path) or not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(candidate.path)
        self._audit("cleanup_safe", {"removed": removed})
        return RetentionReport(report.candidates, tuple(removed))

    def _audit(self, action: str, details: dict) -> None:
        target = self.root / ".maintenance" / "audit.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": self.now().isoformat(), "action": action, **details}
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
