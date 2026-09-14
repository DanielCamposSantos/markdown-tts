import pytest
from fastapi import HTTPException

import web
from app.jobs import JobRepository
from app.persistence import LibraryStore
from web import GenerateRequest


@pytest.fixture
def configured_web(monkeypatch, tmp_path):
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")
    library = LibraryStore(tmp_path / "library", voice)
    monkeypatch.setattr(web, "library_store", library)
    monkeypatch.setattr(web, "job_worker", None)
    return library


def test_generate_get_list_and_cancel_queued(configured_web):
    created = web.generate(GenerateRequest(markdown="Texto.", filename="Aula"))
    job_id = created["job_id"]
    payload = web.job_status(job_id)
    listing = web.list_jobs()
    assert payload["status"] == "queued"
    assert payload["phase"] == "queued"
    assert payload["total_units"] == 0
    assert payload["timeline"] == []
    assert listing["jobs"][0]["job_id"] == job_id
    assert web.cancel_job(job_id)["status"] == "cancelled"
    generation_id = payload["generation_id"]
    assert configured_web.generations.get(generation_id).status == "failed"


def test_cancel_running_is_cancelling(configured_web):
    job_id = web.generate(GenerateRequest(markdown="Texto."))["job_id"]
    JobRepository(configured_web.database).claim_next()
    assert web.cancel_job(job_id)["status"] == "cancelling"


def test_job_api_unknown_and_terminal_cancel_errors(configured_web):
    with pytest.raises(HTTPException) as missing:
        web.job_status("missing")
    with pytest.raises(HTTPException) as missing_cancel:
        web.cancel_job("missing")
    assert missing.value.status_code == 404
    assert missing_cancel.value.status_code == 404

    job_id = web.generate(GenerateRequest(markdown="Texto."))["job_id"]
    repository = JobRepository(configured_web.database)
    repository.request_cancel(job_id)
    with pytest.raises(HTTPException) as conflict:
        web.cancel_job(job_id)
    assert conflict.value.status_code == 409
