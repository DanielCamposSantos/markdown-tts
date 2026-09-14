from __future__ import annotations

import re
import threading
import unicodedata
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
)
from fastapi.responses import (
    FileResponse,
)
from fastapi.staticfiles import (
    StaticFiles,
)
from pydantic import (
    BaseModel,
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


engine: TtsEngine | None = None
model_manager: ModelManager | None = None
library_store: LibraryStore | None = None
job_worker: JobWorker | None = None


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
        )
    return model_manager


def get_library() -> LibraryStore:
    global library_store
    if library_store is None:
        library_store = LibraryStore(LIBRARY_DIR)
    return library_store


def get_worker() -> JobWorker:
    global job_worker
    library = get_library()
    if job_worker is None or job_worker.library is not library:
        job_worker = JobWorker(
            library,
            service_factory=lambda: GenerationService(get_engine()),
        )
    return job_worker


@app.get("/api/model/status")
def model_status():
    return get_model_manager().status()


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
    library = get_library()
    document, generation_record, _, _ = library.create(
        stem,
        markdown,
    )
    job = JobRepository(library.database).enqueue(
        generation_record.generation_id, document.document_id
    )
    get_worker().wake()

    return {
        "job_id": job.job_id,
    }


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
        data["metadata"] = library.metadata(generation)
        data["audio_url"] = f"/api/generations/{generation_id}/audio"
        data["download_url"] = f"/api/generations/{generation_id}/download"
    return data


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
    payload["id"] = job.job_id
    payload["total_units"] = job.total
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
    repository = JobRepository(get_library().database)
    try:
        job = repository.request_cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Geração não encontrada.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job.status == "cancelled":
        get_library().mark_failed(job.generation_id, "Geração cancelada antes do início.")
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
