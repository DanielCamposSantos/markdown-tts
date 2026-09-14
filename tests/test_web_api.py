import sys
from pathlib import Path

import web
from app.domain.models import GenerationResult
from app.persistence import LibraryStore
from web import GenerateRequest, PlanRequest


def test_safe_filename_sanitizes_special_characters():
    assert web.safe_filename(
        "Aula de redes: SYN-ACK!.md"
    ) == "Aula_de_redes_SYN-ACK"


def test_safe_filename_empty_value_uses_default():
    assert web.safe_filename("") == "narracao"
    assert web.safe_filename("....") == "narracao"


def test_plan_route_accepts_empty_markdown_without_loading_engine():
    web.engine = None

    result = web.plan_markdown(
        PlanRequest(markdown="   ")
    )

    assert result == {
        "blocks": 0,
        "units": [],
    }
    assert web.engine is None


def test_plan_route_returns_units_and_links_without_engine():
    web.engine = None

    result = web.plan_markdown(
        PlanRequest(
            markdown="# Titulo\n\nPrimeira. Segunda."
        )
    )

    assert result["blocks"] == 2
    assert [unit["text"] for unit in result["units"]] == [
        "Titulo",
        "Primeira.",
        "Segunda.",
    ]
    assert result["units"][0]["previous_id"] is None
    assert result["units"][0]["next_id"] == 2
    assert result["units"][1]["previous_id"] == 1
    assert result["units"][2]["next_id"] is None
    assert web.engine is None


def test_generate_route_rejects_empty_markdown_before_engine_access():
    web.engine = None

    try:
        web.generate(
            GenerateRequest(markdown="   ")
        )
    except Exception as error:
        assert getattr(error, "status_code", None) == 400
    else:
        raise AssertionError("empty Markdown should be rejected")

    assert web.engine is None


def test_web_import_does_not_import_moss_engine():
    assert "app.moss_engine" not in sys.modules


def test_generate_enqueues_persistent_job(monkeypatch, tmp_path):
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")
    library = LibraryStore(tmp_path / "library", voice)
    monkeypatch.setattr(web, "library_store", library)
    monkeypatch.setattr(web, "job_worker", None)
    result = web.generate(GenerateRequest(markdown="Texto.", filename="aula"))
    payload = web.job_status(result["job_id"])
    assert payload["status"] == "queued"
    assert payload["generation_id"] != payload["job_id"]
    assert payload["document_id"]
