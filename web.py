from __future__ import annotations

import re
import threading
import time
import unicodedata
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path

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
    OUTPUTS_DIR,
    ROOT,
)

from app.markdown_parser import (
    parse_markdown,
)

from app.moss_engine import (
    MossEngine,
)

from app.speech_plan import (
    build_speech_plan,
)


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

engine: MossEngine | None = None


def get_engine() -> MossEngine:
    global engine

    if engine is not None:
        return engine

    with engine_lock:
        if engine is None:
            engine = MossEngine()

    return engine


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


def progress_percentage(
    phase: str,
    current: int,
    total: int,
) -> float:
    total = max(
        total,
        1,
    )

    ratio = (
        current
        / total
    )

    if phase == "model":
        return 3.0

    if phase == "generation":
        return (
            5.0
            + ratio * 72.0
        )

    if phase == "decode":
        return (
            77.0
            + ratio * 18.0
        )

    if phase == "export":
        return 98.0

    return 0.0


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
    output_file: Path,
) -> None:
    try:
        with generation_lock:
            update_job(
                job_id,
                status="running",
                message=(
                    "Analisando Markdown..."
                ),
                progress=1.0,
            )

            blocks = parse_markdown(
                markdown
            )

            plan = build_speech_plan(
                blocks
            )

            if not plan:
                raise RuntimeError(
                    "O Markdown não contém "
                    "texto narrável."
                )

            update_job(
                job_id,
                total_units=len(
                    plan
                ),
                message=(
                    f"{len(plan)} "
                    "unidades de leitura."
                ),
                progress=2.0,
            )

            def progress_callback(
                phase: str,
                current: int,
                total: int,
                message: str,
            ) -> None:
                update_job(
                    job_id,
                    phase=phase,
                    current=current,
                    total_units=total,
                    message=message,
                    progress=(
                        progress_percentage(
                            phase,
                            current,
                            total,
                        )
                    ),
                )

            tts = get_engine()

            result = tts.generate(
                units=plan,
                output_file=output_file,
                progress_callback=(
                    progress_callback
                ),
            )

            timeline = [
                asdict(entry)
                for entry
                in result.timeline
            ]

            update_job(
                job_id,
                status="completed",
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
                    f"/audio/"
                    f"{output_file.name}"
                ),
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
            status="error",
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

    OUTPUTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    job_id = (
        uuid.uuid4()
        .hex[:12]
    )

    stem = safe_filename(
        request.filename
    )

    output_file = (
        OUTPUTS_DIR
        / (
            f"{stem}_"
            f"{job_id}.mp3"
        )
    )

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
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
                str(output_file)
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
            output_file,
        ),
        daemon=True,
    )

    thread.start()

    return {
        "job_id": job_id,
    }


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