from __future__ import annotations

import re
import json
import os
import logging
import threading
import unicodedata
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    FileResponse,
)
from fastapi.staticfiles import (
    StaticFiles,
)
from pydantic import (
    BaseModel,
    Field,
)

from app.config import (
    LIBRARY_DIR,
    OUTPUTS_DIR,
    ROOT,
)

from app.markdown_parser import (
    parse_markdown,
)

from app.speech_plan import (
    build_speech_plan,
)

from app.services.generation import GenerationService
from app.persistence import LibraryStore
from app.jobs import JobRepository, JobWorker
from app.tts import ModelManager
from app.domain.models import PlaybackState
from app.persistence.library import utc_now
from app.playback import active_unit_at, unit_id
from app.markdown_preview import MAX_MARKDOWN_INPUT_BYTES, render_markdown_preview
from app.maintenance.voice_integrity import VoiceIntegrityService
from app.operational import OperationalSettingsStore, PRESETS
from app.system_status import ReadinessError, SystemStatusService
from app.validation.manager import AsrManager
from app.status_stream import StatusStream, safe_snapshot

if TYPE_CHECKING:
    from app.services.generation import TtsEngine


STATIC_DIR = (
    ROOT
    / "static"
)

INDEX_FILE = (
    STATIC_DIR
    / "index.html"
)


@asynccontextmanager
async def lifespan(_app):
    get_voice_integrity().check()
    current_manager = get_model_manager()
    current_manager.start()
    current_worker = get_worker()
    current_worker.start()
    try:
        yield
    finally:
        current_worker.stop()
        current_manager.shutdown()


app = FastAPI(title="Markdown TTS", lifespan=lifespan)


@app.exception_handler(Exception)
async def internal_api_error(request, exc):
    logging.getLogger("uvicorn.error").exception(
        "api_exception path=%s type=%s", request.url.path, type(exc).__name__
    )
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": "request_failed: erro interno do backend."})


app.mount(
    "/static",
    StaticFiles(
        directory=STATIC_DIR,
    ),
    name="static",
)


app.mount(
    "/audio",
    StaticFiles(
        directory=OUTPUTS_DIR,
    ),
    name="audio",
)


class GenerateRequest(
    BaseModel
):
    markdown: str

    filename: str = (
        "narracao"
    )


class PlanRequest(
    BaseModel
):
    markdown: str


class PreviewRequest(BaseModel):
    markdown: str


class PlaybackUpdateRequest(BaseModel):
    position_seconds: float = Field(ge=0)
    active_unit_id: int | None = Field(default=None, ge=0)
    playback_rate: Literal[0.75, 1.0, 1.25, 1.5, 1.75, 2.0]


class PresetRequest(BaseModel):
    preset: Literal["standard", "validated"]


engine: TtsEngine | None = None
model_manager: ModelManager | None = None
library_store: LibraryStore | None = None
job_worker: JobWorker | None = None
voice_integrity_service: VoiceIntegrityService | None = None
asr_manager: AsrManager | None = None
operational_settings_store: OperationalSettingsStore | None = None
system_status_service: SystemStatusService | None = None
status_stream = StatusStream(lambda: stream_snapshot())


def stream_snapshot() -> dict:
    status = get_system_status_service().status(get_operational_settings_store().load())
    with get_library().database.connect() as connection:
        row = connection.execute(
            "SELECT status, phase FROM generation_jobs ORDER BY "
            "CASE WHEN status IN ('running','cancelling') THEN 0 ELSE 1 END, "
            "queue_sequence DESC LIMIT 1"
        ).fetchone()
    return safe_snapshot(status, dict(row) if row else None)


def get_voice_integrity() -> VoiceIntegrityService:
    global voice_integrity_service
    if voice_integrity_service is None:
        voice_integrity_service = VoiceIntegrityService(
            ROOT / "voices" / "narrator_reference.wav",
            ROOT / "voices" / "narrator_reference.manifest.json",
        )
    return voice_integrity_service


def get_asr_manager() -> AsrManager:
    global asr_manager
    if asr_manager is None:
        asr_manager = AsrManager(on_state_change=status_stream.signal)
    return asr_manager


def get_operational_settings_store() -> OperationalSettingsStore:
    global operational_settings_store
    if operational_settings_store is None:
        operational_settings_store = OperationalSettingsStore(LIBRARY_DIR / "operational-settings.json")
    return operational_settings_store


def get_system_status_service() -> SystemStatusService:
    global system_status_service
    if system_status_service is None:
        system_status_service = SystemStatusService(
            model_status=get_model_manager().status,
            asr_status=get_asr_manager().status,
            voice_check=get_voice_integrity().check,
        )
    return system_status_service


def get_engine() -> TtsEngine:
    global engine

    if engine is not None:
        return engine

    if engine is None:
        engine = get_model_manager()

    return engine


def get_model_manager() -> ModelManager:
    global model_manager
    if model_manager is None:
        def engine_factory():
            from app.moss_engine import MossEngine
            return MossEngine()

        def cuda_available() -> bool:
            import torch
            return torch.cuda.is_available()

        model_manager = ModelManager(
            engine_factory,
            cuda_available=cuda_available,
            on_state_change=status_stream.signal,
        )
    return model_manager


def get_library() -> LibraryStore:
    global library_store
    if library_store is None:
        library_store = LibraryStore(LIBRARY_DIR, voice_integrity=get_voice_integrity())
    return library_store


def get_worker() -> JobWorker:
    global job_worker
    library = get_library()
    if job_worker is None or job_worker.library is not library:
        job_worker = JobWorker(
            library,
            service_factory=lambda: GenerationService(
                get_engine(), asr_manager=get_asr_manager(),
                asr_enabled=get_operational_settings_store().load().asr_enabled,
            ),
            on_change=status_stream.signal,
        )
    return job_worker


@app.get("/api/model/status")
def model_status():
    return {**get_model_manager().status(), "voice_integrity": get_voice_integrity().check().to_dict()}


@app.get("/api/system-status")
def system_status():
    return get_system_status_service().status(get_operational_settings_store().load())


@app.websocket("/ws/system-status")
async def system_status_socket(socket: WebSocket):
    origin = socket.headers.get("origin", "")
    host = socket.headers.get("host", "")
    if host not in {"127.0.0.1:7860", "localhost:7860"} or origin not in {
        "", "http://127.0.0.1:7860", "http://localhost:7860",
    }:
        await socket.close(code=1008)
        return
    await status_stream.connect(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        status_stream.disconnect(socket)


@app.get("/api/health")
def health():
    return {"application": "markdown-tts", "status": "ok"}


@app.get("/api/operational-settings")
def operational_settings():
    current = get_operational_settings_store().load()
    return {"current": current.to_dict(), "presets": [{"id": key, **value} for key, value in PRESETS.items()]}


@app.put("/api/operational-settings")
def update_operational_settings(request: PresetRequest):
    result = get_operational_settings_store().save(request.preset).to_dict()
    status_stream.signal()
    return result


def safe_filename(
    name: str,
) -> str:
    name = (
        Path(name)
        .stem
        .strip()
    )

    normalized = (
        unicodedata.normalize(
            "NFKD",
            name,
        )
        .encode(
            "ascii",
            "ignore",
        )
        .decode("ascii")
    )

    normalized = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        normalized,
    )

    normalized = (
        normalized
        .strip("_")
    )

    if not normalized:
        normalized = (
            "narracao"
        )

    return normalized[:80]


@app.get("/")
def index():
    return FileResponse(
        INDEX_FILE
    )


@app.post(
    "/api/plan"
)
def plan_markdown(
    request: PlanRequest,
):
    markdown = (
        request.markdown
        .strip()
    )

    if not markdown:
        return {
            "blocks": 0,
            "units": [],
        }

    blocks = parse_markdown(
        markdown
    )

    plan = build_speech_plan(
        blocks
    )

    return {
        "blocks": len(
            blocks
        ),

        "units": [
            {
                "id": unit.index,
                "kind": unit.kind,
                "text": (
                    unit.display_text
                ),
                "previous_id": (
                    unit.previous_id
                ),
                "next_id": (
                    unit.next_id
                ),
            }
            for unit in plan
        ],
    }


@app.post(
    "/api/generate"
)
def generate(
    request: GenerateRequest,
):
    markdown = (
        request.markdown
        .strip()
    )

    if not markdown:
        raise HTTPException(
            status_code=400,
            detail=(
                "Digite ou cole um "
                "Markdown primeiro."
            ),
        )

    stem = safe_filename(
        request.filename
    )
    settings = get_operational_settings_store().load()
    try:
        get_system_status_service().require_ready(settings)
    except ReadinessError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    library = get_library()
    document, generation_record, _, _ = library.create(
        stem,
        markdown,
    )
    job = JobRepository(library.database, on_change=status_stream.signal).enqueue(
        generation_record.generation_id, document.document_id
    )
    get_worker().wake()

    return {
        "job_id": job.job_id,
    }


@app.post("/api/preview")
def preview_markdown(request: PreviewRequest):
    if len(request.markdown.encode("utf-8")) > MAX_MARKDOWN_INPUT_BYTES:
        raise HTTPException(status_code=413, detail="Markdown excede o limite de 5 MiB.")
    return {"html": render_markdown_preview(request.markdown)}


@app.get("/api/library")
def list_library():
    library = get_library()
    return {
        "documents": [
            {
                **document.to_dict(),
                "generations": [
                    generation.to_dict()
                    for generation in library.generations.list_for_document(
                        document.document_id
                    )
                ],
            }
            for document in library.documents.list()
        ]
    }


@app.get("/api/library/{document_id}")
def library_document(document_id: str):
    library = get_library()
    document = library.documents.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    markdown = library.resolve(document.markdown_path).read_text(encoding="utf-8")
    return {
        **document.to_dict(),
        "markdown": markdown,
        "generations": [
            generation.to_dict()
            for generation in library.generations.list_for_document(document_id)
        ],
    }


@app.get("/api/generations/{generation_id}")
def persisted_generation(generation_id: str):
    library = get_library()
    generation = library.generations.get(generation_id)
    if generation is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    data = generation.to_dict()
    if generation.status == "completed":
        metadata = library.metadata(generation)
        artifacts = metadata.get("unit_artifacts") or []
        available = bool(metadata.get("individual_regeneration_available")) and bool(artifacts) and all(
            library.resolve(path).is_file() and library.resolve(path).stat().st_size > 0 for path in artifacts
        )
        data["metadata"] = metadata
        data["pronunciation_status"] = (
            metadata.get("pronunciation", {}).get("profile") or "legacy"
        )
        data["regeneration_available"] = available
        data["artifact_revision"] = generation.artifact_revision
        data["audio_url"] = f"/api/generations/{generation_id}/audio?revision={generation.artifact_revision}"
        data["download_url"] = f"/api/generations/{generation_id}/download?revision={generation.artifact_revision}"
    return data


@app.get("/api/generations/{generation_id}/alignment")
def generation_alignment(generation_id: str):
    library = get_library()
    generation = library.generations.get(generation_id)
    if generation is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    if generation.status != "completed" or not generation.metadata_path:
        return {"schema_version": 1, "status": "not_available", "units": []}
    descriptor = library.metadata(generation).get("alignment") or {}
    if not descriptor.get("artifact"):
        return {"schema_version": 1, "status": "not_available", "units": []}
    try:
        return json.loads(library.resolve(descriptor["artifact"]).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {"schema_version": 1, "status": "not_available", "units": []}


@app.post("/api/generations/{generation_id}/units/{unit_id}/regenerate")
def regenerate_unit(generation_id: str, unit_id: int):
    try:
        get_system_status_service().require_ready(get_operational_settings_store().load())
    except ReadinessError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    library = get_library()
    generation = library.generations.get(generation_id)
    if generation is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    if generation.status != "completed":
        raise HTTPException(status_code=409, detail="Geração ainda não está concluída.")
    metadata = library.metadata(generation)
    artifacts = metadata.get("unit_artifacts") or []
    if not metadata.get("individual_regeneration_available") or not artifacts:
        raise HTTPException(status_code=409, detail="Esta geração foi criada antes do suporte a regeneração individual.")
    ids = {int(unit["index"]) for unit in metadata.get("units", [])}
    if unit_id not in ids:
        raise HTTPException(status_code=404, detail="SpeechUnit não encontrada.")
    if not all(library.resolve(path).is_file() and library.resolve(path).stat().st_size > 0 for path in artifacts):
        raise HTTPException(status_code=409, detail="Artefatos de unidade incompletos.")
    with library.database.connect() as connection:
        pending = connection.execute(
            "SELECT 1 FROM generation_jobs WHERE generation_id=? AND operation='regenerate_unit' AND status IN ('queued','running','cancelling')",
            (generation_id,),
        ).fetchone()
    if pending:
        raise HTTPException(status_code=409, detail="Já existe uma regeneração pendente para esta geração.")
    job = JobRepository(library.database, on_change=status_stream.signal).enqueue_regeneration(
        generation_id, generation.document_id, unit_id, generation.artifact_revision
    )
    get_worker().wake()
    return {"job_id": job.job_id, "status": job.status}


@app.get("/api/generations/{generation_id}/regenerations")
def regeneration_history(generation_id: str):
    library = get_library()
    if library.generations.get(generation_id) is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    with library.database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM unit_regenerations WHERE generation_id=? ORDER BY created_at",
            (generation_id,),
        ).fetchall()
    return {"regenerations": [dict(row) for row in rows]}


def _playback_context(generation_id: str):
    library = get_library()
    generation = library.generations.get(generation_id)
    if generation is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    metadata = library.metadata(generation) if generation.status == "completed" else {}
    timeline = metadata.get("timeline", [])
    duration = max(0.0, float(generation.duration_seconds or 0.0))
    return library, generation, timeline, duration


def _normalized_playback(state, timeline, duration):
    position = min(max(0.0, float(state.position_seconds)), duration)
    active = active_unit_at(timeline, position)
    return {
        "generation_id": state.generation_id,
        "position_seconds": position,
        "active_unit_id": active,
        "playback_rate": state.playback_rate,
        "updated_at": state.updated_at,
    }


@app.get("/api/generations/{generation_id}/playback")
def get_playback(generation_id: str):
    library, _, timeline, duration = _playback_context(generation_id)
    state = library.playback.get_or_default(generation_id)
    return _normalized_playback(state, timeline, duration)


@app.put("/api/generations/{generation_id}/playback")
def put_playback(generation_id: str, request: PlaybackUpdateRequest):
    library, _, timeline, duration = _playback_context(generation_id)
    valid_ids = {unit_id(entry) for entry in timeline}
    if request.active_unit_id is not None and request.active_unit_id not in valid_ids:
        raise HTTPException(status_code=422, detail="SpeechUnit não pertence à geração.")
    position = min(request.position_seconds, duration)
    state = PlaybackState(
        generation_id=generation_id,
        position_seconds=position,
        active_unit_id=active_unit_at(timeline, position),
        playback_rate=float(request.playback_rate),
        updated_at=utc_now(),
    )
    return _normalized_playback(library.playback.upsert(state), timeline, duration)


def generation_audio_file(generation_id: str):
    library = get_library()
    generation = library.generations.get(generation_id)
    if generation is None or generation.status != "completed" or not generation.audio_path:
        raise HTTPException(status_code=404, detail="Áudio não encontrado.")
    try:
        path = library.resolve(generation.audio_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Áudio não encontrado.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Áudio não encontrado.")
    return path, generation


@app.get("/api/generations/{generation_id}/audio")
def persisted_audio(generation_id: str):
    path, _ = generation_audio_file(generation_id)
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/generations/{generation_id}/download")
def persisted_download(generation_id: str):
    path, generation = generation_audio_file(generation_id)
    document = get_library().documents.get(generation.document_id)
    filename = f"{document.title if document else 'narracao'}.mp3"
    return FileResponse(path, media_type="audio/mpeg", filename=filename)


@app.get(
    "/api/jobs/{job_id}"
)
def job_status(
    job_id: str,
):
    job = JobRepository(get_library().database).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    payload = job.to_dict()
    now = datetime.now(timezone.utc)
    if job.started_at:
        started = datetime.fromisoformat(job.started_at.replace("Z", "+00:00"))
        end = (
            datetime.fromisoformat(job.completed_at.replace("Z", "+00:00"))
            if job.completed_at else now
        )
        payload["elapsed_seconds"] = max(job.elapsed_seconds, (end - started).total_seconds())
    if job.eta_seconds is not None and job.status == "running":
        updated = datetime.fromisoformat(job.updated_at.replace("Z", "+00:00"))
        payload["eta_seconds"] = max(0.0, job.eta_seconds - (now - updated).total_seconds())
    payload["id"] = job.job_id
    payload["total_units"] = job.total
    payload["queue_position"] = JobRepository(get_library().database).queue_position(job_id)
    payload["timeline"] = []
    payload["audio_url"] = None
    generation = get_library().generations.get(job.generation_id)
    if job.status == "completed" and generation:
        metadata = get_library().metadata(generation)
        payload["timeline"] = metadata["timeline"]
        payload["audio_url"] = f"/api/generations/{job.generation_id}/audio"
        payload["duration_seconds"] = generation.duration_seconds
        payload["generation_seconds"] = generation.generation_seconds
        payload["decode_seconds"] = generation.decode_seconds
    return payload


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [job_status(job.job_id) for job in JobRepository(get_library().database).list()]}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    repository = JobRepository(get_library().database, on_change=status_stream.signal)
    try:
        job = repository.request_cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Geração não encontrada.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job.status == "cancelled":
        if job.operation == "generate":
            get_library().mark_failed(job.generation_id, "Geração cancelada antes do início.")
        else:
            with get_library().database.connect() as connection:
                connection.execute(
                    "UPDATE unit_regenerations SET status='cancelled', completed_at=? WHERE job_id=?",
                    (utc_now(), job.job_id),
                )
    get_worker().wake()
    return job_status(job.job_id)


@app.get(
    "/api/download/{job_id}"
)
def download(
    job_id: str,
):
    job = JobRepository(get_library().database).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="O áudio ainda não está pronto.")
    return persisted_download(job.generation_id)


if __name__ == "__main__":
    def open_browser() -> None:
        webbrowser.open(
            "http://127.0.0.1:7860"
        )

    if os.environ.get("MARKDOWN_TTS_NO_BROWSER") != "1":
        threading.Timer(
            1.5,
            open_browser,
        ).start()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=7860,
        log_level="info",
    )
