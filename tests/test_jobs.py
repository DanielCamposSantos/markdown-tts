import threading

import pytest

from app.domain.models import GenerationProgress, GenerationResult, TimelineEntry
from app.jobs import JobRepository, JobWorker
from app.persistence import LibraryStore
from app.persistence.database import Database
from app.persistence.migrations import MIGRATIONS
from app.services.generation import GenerationCancelled, GenerationService


@pytest.fixture
def library(tmp_path):
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")
    return LibraryStore(tmp_path / "library", voice)


def enqueue(library, text="Texto."):
    document, generation, _, _ = library.create(text, text)
    job = JobRepository(library.database).enqueue(generation.generation_id, document.document_id)
    return document, generation, job


class FakeEngine:
    def __init__(self, fail=False, cancel_during=None):
        self.fail = fail
        self.cancel_during = cancel_during
        self.active = 0
        self.max_active = 0

    def generate(self, units, output_file, progress_callback=None, should_cancel=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.fail:
                raise RuntimeError("fake failure")
            for index, _ in enumerate(units, 1):
                progress_callback("generation", index, len(units), f"Gerando {index}")
                if self.cancel_during:
                    self.cancel_during()
                    self.cancel_during = None
                if should_cancel and should_cancel():
                    raise GenerationCancelled("cancelled")
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"audio")
            return GenerationResult(
                output_file, 1.0, 0.5, 0.2,
                (TimelineEntry(1, "paragraph", "Texto.", 0, 0.75, 250),),
            )
        finally:
            self.active -= 1


def test_migration_v1_to_v2(tmp_path):
    path = tmp_path / "library.db"
    database = Database(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executescript(MIGRATIONS[0][1])
        connection.execute("INSERT INTO schema_migrations VALUES (1, 'now')")
    database.initialize()
    assert database.schema_version == 2


def test_enqueue_fifo_progress_and_reopen(library):
    repository = JobRepository(library.database)
    jobs = [enqueue(library, f"Texto {i}.")[2] for i in range(3)]
    assert [job.job_id for job in repository.list()] == [job.job_id for job in jobs]
    claimed = repository.claim_next()
    repository.update_progress(
        claimed.job_id,
        GenerationProgress("generation", 1, 3, "Gerando", 29.0),
    )
    reopened = JobRepository(Database(library.database.path)).get(claimed.job_id)
    assert reopened.status == "running"
    assert reopened.progress == 29.0
    assert reopened.heartbeat_at


def test_claim_is_atomic_across_threads(library):
    repository = JobRepository(library.database)
    enqueue(library)
    claimed = []
    threads = [threading.Thread(target=lambda: claimed.append(repository.claim_next())) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(job is not None for job in claimed) == 1


def test_concurrent_enqueue_does_not_lose_jobs(library):
    repository = JobRepository(library.database)
    records = [library.create(f"Doc {i}", f"Texto {i}.")[:2] for i in range(4)]
    threads = [
        threading.Thread(
            target=repository.enqueue,
            args=(generation.generation_id, document.document_id),
        )
        for document, generation in records
    ]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(repository.list()) == 4
    assert [job.queue_sequence for job in repository.list()] == sorted(
        job.queue_sequence for job in repository.list()
    )


def test_valid_and_invalid_transitions_and_cancel_queued(library):
    repository = JobRepository(library.database)
    _, _, job = enqueue(library)
    cancelled = repository.request_cancel(job.job_id)
    assert cancelled.status == "cancelled"
    with pytest.raises(ValueError):
        repository.transition(job.job_id, "running")
    with pytest.raises(ValueError):
        repository.request_cancel(job.job_id)


def test_completed_job_cannot_be_cancelled_or_reopened(library):
    repository = JobRepository(library.database)
    job = enqueue(library)[2]
    repository.claim_next()
    repository.transition(job.job_id, "completed")
    assert repository.get(job.job_id).progress == 100
    with pytest.raises(ValueError):
        repository.request_cancel(job.job_id)
    with pytest.raises(ValueError):
        repository.transition(job.job_id, "running")


def test_completed_wins_when_cancel_arrives_after_artifact_publication(library):
    repository = JobRepository(library.database)
    job = enqueue(library)[2]
    repository.claim_next()
    repository.request_cancel(job.job_id)
    completed = repository.transition(job.job_id, "completed")
    assert completed.status == "completed"
    assert completed.progress == 100


def test_recovery_interrupts_active_and_preserves_queued(library):
    repository = JobRepository(library.database)
    first = enqueue(library, "Um.")[2]
    second = enqueue(library, "Dois.")[2]
    third = enqueue(library, "Três.")[2]
    repository.claim_next()
    library.mark_running(first.generation_id)
    repository.request_cancel(first.job_id)
    repository.claim_next()
    library.mark_running(second.generation_id)
    assert repository.recover_abandoned() == 2
    assert repository.get(first.job_id).status == "interrupted"
    assert repository.get(second.job_id).status == "interrupted"
    assert repository.get(third.job_id).status == "queued"
    assert library.generations.get(first.generation_id).status == "failed"


def test_worker_processes_fifo_one_at_a_time(library):
    repository = JobRepository(library.database)
    jobs = [enqueue(library, text)[2] for text in ("Um.", "Dois.")]
    engine = FakeEngine()
    worker = JobWorker(library, lambda: GenerationService(engine))
    assert worker.run_once() and worker.run_once()
    assert [repository.get(job.job_id).status for job in jobs] == ["completed", "completed"]
    assert engine.max_active == 1
    assert worker.run_once() is False


def test_worker_continues_after_failure(library):
    repository = JobRepository(library.database)
    first, second = enqueue(library, "Um.")[2], enqueue(library, "Dois.")[2]
    engines = iter((FakeEngine(fail=True), FakeEngine()))
    worker = JobWorker(library, lambda: GenerationService(next(engines)))
    worker.run_once(); worker.run_once()
    assert repository.get(first.job_id).status == "failed"
    assert repository.get(second.job_id).status == "completed"


def test_worker_cooperatively_cancels_without_final_audio(library):
    repository = JobRepository(library.database)
    document, generation, job = enqueue(library, "Um. Dois.")
    engine = FakeEngine(cancel_during=lambda: repository.request_cancel(job.job_id))
    worker = JobWorker(library, lambda: GenerationService(engine))
    worker.run_once()
    assert repository.get(job.job_id).status == "cancelled"
    assert library.generations.get(generation.generation_id).status == "failed"
    final = library.root / document.document_id / generation.generation_id / "audio.mp3"
    assert not final.exists()


def test_worker_start_is_singleton_and_shutdowns(library):
    worker = JobWorker(library, lambda: GenerationService(FakeEngine()), poll_interval=0.01)
    worker.start()
    thread = worker._thread
    worker.start()
    assert worker._thread is thread
    worker.stop()
    assert not thread.is_alive()
