from __future__ import annotations

import re
import threading
import time
import unicodedata
import webbrowser
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

from app.domain.models import GenerationProgress, JobStatus
from app.services.generation import GenerationService
from app.persistence import LibraryStore

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


app = FastAPI(
    title="Markdown TTS",
)


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


jobs: dict[
    str,
    dict,
] = {}

jobs_lock = (
    threading.RLock()
)

generation_lock = (
    threading.Lock()
)

engine_lock = (
    threading.Lock()
)

engine: TtsEngine | None = None
library_store: LibraryStore | None = None


def get_engine() -> TtsEngine:
    global engine

    if engine is not None:
        return engine

    with engine_lock:
        if engine is None:
            from app.moss_engine import MossEngine

            engine = MossEngine()

    return engine


def get_library() -> LibraryStore:
    global library_store
    if library_store is None:
        library_store = LibraryStore(LIBRARY_DIR)
    return library_store


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


def update_job(
    job_id: str,
    **values,
) -> None:
    with jobs_lock:
        job = jobs.get(
            job_id
        )

        if job is None:
            return

        job.update(
            values
        )


def run_generation(
    job_id: str,
    markdown: str,
    staging_file: Path,
    final_file: Path,
    document,
    generation,
    library: LibraryStore,
) -> None:
    try:
        with generation_lock:
            update_job(
                job_id,
                status=JobStatus.RUNNING.value,
                message=(
                    "Analisando Markdown..."
                ),
                progress=1.0,
            )

            def plan_callback(plan) -> None:
                update_job(
                    job_id,
                    total_units=len(plan),
                    message=f"{len(plan)} unidades de leitura.",
                    progress=2.0,
                )

            def progress_callback(
                progress: GenerationProgress,
            ) -> None:
                update_job(
                    job_id,
                    phase=progress.phase,
                    current=progress.current,
                    total_units=progress.total,
                    message=progress.message,
                    progress=progress.progress,
                )

            service = GenerationService(get_engine())
            result, stored = service.generate_persisted(
                markdown=markdown,
                staging_file=staging_file,
                final_file=final_file,
                document=document,
                generation=generation,
                store=library,
                progress_callback=progress_callback,
                plan_callback=plan_callback,
            )

            timeline = [
                entry.to_dict()
                for entry
                in result.timeline
            ]

            update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                phase="completed",
                progress=100.0,
                message="Áudio pronto.",
                timeline=timeline,
                duration_seconds=(
                    result.duration_seconds
                ),
                generation_seconds=(
                    result.generation_seconds
                ),
                decode_seconds=(
                    result.decode_seconds
                ),
                audio_url=(
                    f"/api/generations/"
                    f"{generation.generation_id}/audio"
                ),
                generation_id=generation.generation_id,
                document_id=document.document_id,
                output_file=str(library.resolve(stored.audio_path)),
                completed_at=time.time(),
            )

    except Exception as exc:
        print()
        print(
            "ERRO NA GERAÇÃO:"
        )

        print(
            repr(exc)
        )

        update_job(
            job_id,
            status=JobStatus.ERROR.value,
            phase="error",
            message=str(exc),
            error=str(exc),
        )


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

    with jobs_lock:
        has_active_job = any(
            job.get("status")
            in {
                "queued",
                "running",
            }
            for job
            in jobs.values()
        )

    if has_active_job:
        raise HTTPException(
            status_code=409,
            detail=(
                "Já existe uma geração "
                "em andamento."
            ),
        )

    stem = safe_filename(
        request.filename
    )
    library = get_library()
    document, generation_record, staging_file, final_file = library.create(
        stem,
        markdown,
    )
    job_id = generation_record.generation_id

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": JobStatus.QUEUED.value,
            "phase": "queued",
            "progress": 0.0,
            "message": (
                "Aguardando início..."
            ),
            "current": 0,
            "total_units": 0,
            "timeline": [],
            "audio_url": None,
            "output_file": (
                str(final_file)
            ),
            "download_name": (
                f"{stem}.mp3"
            ),
            "created_at": (
                time.time()
            ),
        }

    thread = threading.Thread(
        target=run_generation,
        args=(
            job_id,
            markdown,
            staging_file,
            final_file,
            document,
            generation_record,
            library,
        ),
        daemon=True,
    )

    thread.start()

    return {
        "job_id": job_id,
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
    with jobs_lock:
        job = jobs.get(
            job_id
        )

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Geração não encontrada."
                ),
            )

        return {
            key: value
            for key, value
            in job.items()
            if key
            != "output_file"
        }


@app.get(
    "/api/download/{job_id}"
)
def download(
    job_id: str,
):
    with jobs_lock:
        job = jobs.get(
            job_id
        )

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Geração não encontrada."
                ),
            )

        if (
            job.get("status")
            != "completed"
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "O áudio ainda não "
                    "está pronto."
                ),
            )

        path = Path(
            job[
                "output_file"
            ]
        )

        filename = (
            job[
                "download_name"
            ]
        )

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Arquivo de áudio "
                "não encontrado."
            ),
        )

    return FileResponse(
        path=path,
        media_type=(
            "audio/mpeg"
        ),
        filename=filename,
    )


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
