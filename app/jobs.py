from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from app.domain.models import GenerationProgress
from app.persistence.database import Database
from app.persistence.library import LibraryStore, utc_now
from app.services.generation import GenerationCancelled, GenerationService
from app.services.regeneration import RegenerationService
from app.progress import ProgressPhase, ProgressTracker


@dataclass(frozen=True)
class GenerationJob:
    queue_sequence: int | None
    job_id: str
    generation_id: str
    document_id: str
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    heartbeat_at: str | None = None
    error: str | None = None
    progress: float = 0.0
    phase: str = "queued"
    current: int = 0
    total: int = 0
    message: str = "Aguardando início..."
    operation: str = "generate"
    unit_id: int | None = None
    eta_seconds: float | None = None
    elapsed_seconds: float = 0.0
    phase_progress: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ALLOWED_TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"completed", "failed", "cancelling", "interrupted"},
    "cancelling": {"cancelled", "completed", "interrupted"},
}


class JobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def enqueue(self, generation_id: str, document_id: str) -> GenerationJob:
        job_id, now = uuid.uuid4().hex, utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO generation_jobs (job_id, generation_id, document_id, "
                "status, created_at, updated_at, message) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (job_id, generation_id, document_id, now, now, "Aguardando início..."),
            )
        return self.get(job_id)

    def enqueue_regeneration(self, generation_id: str, document_id: str, unit_id: int, revision: int) -> GenerationJob:
        job_id, regeneration_id, now = uuid.uuid4().hex, uuid.uuid4().hex, utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO generation_jobs (job_id,generation_id,document_id,operation,unit_id,status,created_at,updated_at,message) VALUES (?,?,?,?,?,'queued',?,?,?)",
                (job_id, generation_id, document_id, "regenerate_unit", unit_id, now, now, "Regeneração aguardando início..."),
            )
            connection.execute(
                "INSERT INTO unit_regenerations (regeneration_id,job_id,generation_id,unit_id,previous_revision,new_revision,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (regeneration_id, job_id, generation_id, unit_id, revision, revision + 1, "queued", now),
            )
        return self.get(job_id)

    def _row(self, row) -> GenerationJob | None:
        return None if row is None else GenerationJob(**dict(row))

    def get(self, job_id: str) -> GenerationJob | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row(row)

    def list(self) -> list[GenerationJob]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM generation_jobs ORDER BY queue_sequence"
            ).fetchall()
        return [self._row(row) for row in rows]

    def queue_position(self, job_id: str) -> int | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 + COUNT(*) AS position FROM generation_jobs "
                "WHERE status='queued' AND queue_sequence < "
                "(SELECT queue_sequence FROM generation_jobs WHERE job_id=? AND status='queued')",
                (job_id,),
            ).fetchone()
            job = connection.execute(
                "SELECT status FROM generation_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return int(row["position"]) if job and job["status"] == "queued" else None

    def claim_next(self) -> GenerationJob | None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT job_id FROM generation_jobs WHERE status = 'queued' "
                "ORDER BY queue_sequence LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                "UPDATE generation_jobs SET status = 'running', phase = 'preparing', "
                "updated_at = ?, started_at = ?, heartbeat_at = ?, message = ? "
                "WHERE job_id = ? AND status = 'queued'",
                (now, now, now, "Preparando geração...", row[0]),
            )
            if updated.rowcount != 1:
                return None
        return self.get(row[0])

    def transition(self, job_id: str, status: str, *, error: str | None = None) -> GenerationJob:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if status not in ALLOWED_TRANSITIONS.get(current.status, set()):
            raise ValueError(f"Transição inválida: {current.status} -> {status}")
        now = utc_now()
        terminal = now if status in {"completed", "failed", "cancelled", "interrupted"} else None
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE generation_jobs SET status = ?, phase = ?, updated_at = ?, "
                "completed_at = COALESCE(?, completed_at), error = ?, message = ?, "
                "progress = CASE WHEN ? = 'completed' THEN 100 ELSE progress END, "
                "phase_progress = CASE WHEN ? = 'completed' THEN 100 ELSE phase_progress END, "
                "eta_seconds = NULL "
                "WHERE job_id = ?",
                (status, status, now, terminal, error,
                 error or {
                     "completed": "Áudio pronto.",
                     "cancelling": "Cancelamento solicitado. Finalizando operação atual...",
                     "cancelled": "Geração cancelada.",
                     "interrupted": "Execução interrompida.",
                 }.get(status, current.message),
                 status, status, job_id),
            )
        return self.get(job_id)

    def update_progress(self, job_id: str, progress: GenerationProgress) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE generation_jobs SET phase = ?, current = ?, total = ?, "
                "message = ?, progress = MAX(progress, ?), eta_seconds = ?, "
                "elapsed_seconds = ?, phase_progress = ?, updated_at = ?, heartbeat_at = ? "
                "WHERE job_id = ? AND status IN ('running', 'cancelling')",
                (progress.phase, progress.current, progress.total, progress.message,
                 progress.progress, progress.eta_seconds, progress.elapsed_seconds,
                 progress.phase_progress, now, now, job_id),
            )

    def request_cancel(self, job_id: str) -> GenerationJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        target = "cancelled" if job.status == "queued" else "cancelling"
        if job.status not in {"queued", "running"}:
            raise ValueError(f"Job {job.status} não pode ser cancelado")
        return self.transition(job_id, target)

    def recover_abandoned(self) -> int:
        now = utc_now()
        with self.database.connect() as connection:
            active = connection.execute(
                "SELECT generation_id, operation, job_id FROM generation_jobs "
                "WHERE status IN ('running', 'cancelling')"
            ).fetchall()
            result = connection.execute(
                "UPDATE generation_jobs SET status = 'interrupted', phase = 'interrupted', "
                "updated_at = ?, completed_at = ?, message = ?, eta_seconds = NULL "
                "WHERE status IN ('running', 'cancelling')",
                (now, now, "Execução interrompida pelo reinício."),
            )
            for row in active:
                if row["operation"] == "generate":
                    connection.execute(
                        "UPDATE generations SET status = 'failed', updated_at = ?, error = ? WHERE generation_id = ? AND status = 'running'",
                        (now, "Execução interrompida pelo reinício.", row["generation_id"]),
                    )
                else:
                    history = connection.execute(
                        "SELECT status FROM unit_regenerations WHERE job_id=?", (row["job_id"],)
                    ).fetchone()
                    if history and history["status"] == "completed":
                        connection.execute(
                            "UPDATE generation_jobs SET status='completed', phase='completed', progress=100, completed_at=?, message='Áudio pronto.' WHERE job_id=?",
                            (now, row["job_id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE unit_regenerations SET status='interrupted', completed_at=?, error=? WHERE job_id=?",
                            (now, "Execução interrompida pelo reinício.", row["job_id"]),
                        )
        return result.rowcount


class JobWorker:
    def __init__(self, library: LibraryStore, service_factory: Callable[[], GenerationService], poll_interval: float = 0.2) -> None:
        self.library = library
        self.repository = JobRepository(library.database)
        self.service_factory = service_factory
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.repository.recover_abandoned()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="generation-worker", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._wake.wait(self.poll_interval)
                self._wake.clear()

    def run_once(self) -> bool:
        job = self.repository.claim_next()
        if job is None:
            return False
        generation = self.library.generations.get(job.generation_id)
        document = self.library.documents.get(job.document_id)
        try:
            self.library.require_voice_integrity()
            tracker = ProgressTracker(
                lambda progress: self.repository.update_progress(job.job_id, progress),
                operation=job.operation,
                unit_id=job.unit_id,
            )
            tracker.report(ProgressPhase.PREPARING, message="Preparando geração...")
            if job.operation == "regenerate_unit":
                with self.library.database.connect() as connection:
                    connection.execute(
                        "UPDATE unit_regenerations SET status='running' WHERE job_id=?",
                        (job.job_id,),
                    )
                service = self.service_factory()
                self._report_model_loading_if_needed(service, tracker)
                RegenerationService(
                    service.engine,
                    asr_manager=service.asr_manager,
                    asr_enabled=service.asr_enabled,
                ).regenerate(
                    self.library, job.generation_id, int(job.unit_id), job.job_id,
                    progress_callback=tracker.report,
                    should_cancel=lambda: self.repository.get(job.job_id).status == "cancelling",
                )
                self.repository.transition(job.job_id, "completed")
                return True
            markdown = self.library.resolve(document.markdown_path).read_text(encoding="utf-8")
            generation_dir = self.library.root / document.document_id / generation.generation_id
            staging, final = generation_dir / ".audio.staging.mp3", generation_dir / "audio.mp3"
            service = self.service_factory()
            self._report_model_loading_if_needed(service, tracker)
            result, _ = service.generate_persisted(
                markdown, staging, final, document, generation, self.library,
                progress_callback=lambda progress: tracker.report(
                    progress.phase, progress.current, progress.total, progress.message
                ),
                plan_callback=tracker.set_units,
                should_cancel=lambda: self.repository.get(job.job_id).status == "cancelling",
            )
            self.repository.transition(job.job_id, "completed")
        except GenerationCancelled:
            self.repository.transition(job.job_id, "cancelled")
            if job.operation == "regenerate_unit":
                self._finish_regeneration(job, "cancelled")
        except Exception as exc:
            current = self.repository.get(job.job_id)
            if current.status in {"running", "cancelling"}:
                self.repository.transition(
                    job.job_id, "failed", error=self.library.sanitize_error(str(exc))
                )
            if job.operation == "regenerate_unit":
                self._finish_regeneration(job, "failed", self.library.sanitize_error(str(exc)))
        return True

    @staticmethod
    def _report_model_loading_if_needed(service, tracker: ProgressTracker) -> None:
        status_method = getattr(service.engine, "status", None)
        if not callable(status_method):
            return
        state = status_method().get("state")
        tracker.expect_model_loading(state not in {"ready", "generating"})
        if state not in {"ready", "generating"}:
            tracker.report(ProgressPhase.MODEL_LOADING, message="Carregando modelo...")

    def _finish_regeneration(self, job, status: str, error: str | None = None) -> None:
        with self.library.database.connect() as connection:
            connection.execute(
                "UPDATE unit_regenerations SET status=?, completed_at=?, error=? WHERE job_id=?",
                (status, utc_now(), error, job.job_id),
            )
