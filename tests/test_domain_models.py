from pathlib import Path

from app.domain.models import (
    GenerationProgress,
    GenerationResult,
    JobStatus,
    TimelineEntry,
)


def test_job_status_values_match_public_runtime_states():
    assert [status.value for status in JobStatus] == [
        "queued",
        "running",
        "completed",
        "error",
    ]


def test_progress_and_generation_result_serialize():
    progress = GenerationProgress(
        phase="generation",
        current=1,
        total=2,
        message="Gerando",
        progress=41.0,
    )
    entry = TimelineEntry(1, "paragraph", "Texto.", 0.0, 1.0, 250)
    result = GenerationResult(
        output_file=Path("audio.mp3"),
        duration_seconds=1.25,
        generation_seconds=1.0,
        decode_seconds=0.1,
        timeline=(entry,),
    )

    assert progress.to_dict() == {
        "phase": "generation",
        "current": 1,
        "total": 2,
        "message": "Gerando",
        "progress": 41.0,
    }
    assert entry.to_dict()["pause_after_ms"] == 250
    assert result.to_dict()["output_file"] == "audio.mp3"
    assert result.to_dict()["timeline"][0]["text"] == "Texto."
