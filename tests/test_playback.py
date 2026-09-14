import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import web
from app.domain.models import PlaybackState
from app.persistence.database import Database
from app.persistence.library import utc_now
from app.playback import active_unit_at, adjacent_unit, timeline_index_at
from tests.test_library_api import completed_library
from web import PlaybackUpdateRequest


TIMELINE = [
    {"index": 1, "start_seconds": 0.0, "end_seconds": 2.0, "pause_after_ms": 500},
    {"index": 2, "start_seconds": 2.5, "end_seconds": 5.0, "pause_after_ms": 250},
    {"index": 3, "start_seconds": 5.25, "end_seconds": 8.0, "pause_after_ms": 0},
]


def test_timeline_position_and_navigation_helpers():
    assert timeline_index_at(TIMELINE, 0) == 0
    assert timeline_index_at(TIMELINE, 1) == 0
    assert active_unit_at(TIMELINE, 2.25) == 1
    assert active_unit_at(TIMELINE, 4) == 2
    assert active_unit_at(TIMELINE, 99) == 3
    assert adjacent_unit(TIMELINE, 2, -1)["index"] == 1
    assert adjacent_unit(TIMELINE, 2, 1)["index"] == 3
    assert adjacent_unit(TIMELINE, 1, -1) is None
    assert adjacent_unit(TIMELINE, 3, 1) is None
    assert active_unit_at([], 0) is None


def test_playback_default_upsert_one_row_and_reopen(tmp_path):
    library, _, generation = completed_library(tmp_path)
    default = library.playback.get_or_default(generation.generation_id)
    assert default == PlaybackState(generation.generation_id, 0, None, 1, None)

    state = PlaybackState(generation.generation_id, 0.25, 1, 1.5, utc_now())
    library.playback.upsert(state)
    replacement = PlaybackState(generation.generation_id, 0.5, 1, 1.25, utc_now())
    library.playback.upsert(replacement)
    reopened = Database(library.database.path)
    reopened.initialize()
    from app.persistence.repositories import PlaybackRepository
    assert PlaybackRepository(reopened).get(generation.generation_id) == replacement
    with reopened.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM playback_state").fetchone()[0] == 1


def test_playback_repository_updates_position_rate_and_active_unit(tmp_path):
    library, _, generation = completed_library(tmp_path)
    generation_id = generation.generation_id
    first = library.playback.upsert(
        PlaybackState(generation_id, 0.1, 1, 1, utc_now())
    )
    assert first.position_seconds == 0.1
    second = library.playback.upsert(
        PlaybackState(generation_id, 0.5, 1, 1.5, utc_now())
    )
    assert second.playback_rate == 1.5
    third = library.playback.upsert(
        PlaybackState(generation_id, 0.75, None, 1.5, utc_now())
    )
    assert third.active_unit_id is None


def test_playback_foreign_key_and_generation_delete_cascade(tmp_path):
    library, _, generation = completed_library(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        library.playback.upsert(PlaybackState("missing", 0, None, 1, utc_now()))
    library.playback.upsert(PlaybackState(generation.generation_id, 0, 1, 1, utc_now()))
    with library.database.connect() as connection:
        connection.execute("DELETE FROM generations WHERE generation_id = ?", (generation.generation_id,))
    assert library.playback.get(generation.generation_id) is None


def test_migration_v2_to_v3_and_idempotent_reopen(tmp_path):
    path = tmp_path / "library.db"
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.execute("DROP TABLE playback_state")
    database.initialize()
    database.initialize()
    assert database.schema_version == 5
    with database.connect() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'playback_state'"
        ).fetchone()


def test_playback_api_default_put_round_trip_and_clamp(monkeypatch, tmp_path):
    library, _, generation = completed_library(tmp_path)
    monkeypatch.setattr(web, "library_store", library)
    default = web.get_playback(generation.generation_id)
    assert default["position_seconds"] == 0
    assert default["active_unit_id"] == 1
    assert default["playback_rate"] == 1

    saved = web.put_playback(
        generation.generation_id,
        PlaybackUpdateRequest(position_seconds=99, active_unit_id=1, playback_rate=1.5),
    )
    assert saved["position_seconds"] == 1.0
    assert saved["active_unit_id"] == 1
    assert saved["playback_rate"] == 1.5
    assert web.get_playback(generation.generation_id) == saved


def test_playback_api_rejects_unknown_generation_and_unit(monkeypatch, tmp_path):
    library, _, generation = completed_library(tmp_path)
    monkeypatch.setattr(web, "library_store", library)
    with pytest.raises(HTTPException) as missing:
        web.get_playback("missing")
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as invalid_unit:
        web.put_playback(
            generation.generation_id,
            PlaybackUpdateRequest(position_seconds=0, active_unit_id=999, playback_rate=1),
        )
    assert invalid_unit.value.status_code == 422


@pytest.mark.parametrize("payload", [
    {"position_seconds": -1, "active_unit_id": 1, "playback_rate": 1},
    {"position_seconds": 0, "active_unit_id": 1, "playback_rate": 3},
])
def test_playback_payload_validation(payload):
    with pytest.raises(ValidationError):
        PlaybackUpdateRequest(**payload)


@pytest.mark.parametrize("rate", [0.75, 1, 1.25, 1.5, 1.75, 2])
def test_supported_playback_rates(rate):
    assert PlaybackUpdateRequest(
        position_seconds=0, active_unit_id=None, playback_rate=rate
    ).playback_rate == rate


def test_playback_http_api_round_trip_and_validation(monkeypatch, tmp_path):
    library, _, generation = completed_library(tmp_path)
    monkeypatch.setattr(web, "library_store", library)
    client = TestClient(web.app)
    url = f"/api/generations/{generation.generation_id}/playback"
    assert client.get(url).json()["position_seconds"] == 0
    response = client.put(url, json={
        "position_seconds": 0.5, "active_unit_id": 1, "playback_rate": 1.75,
    })
    assert response.status_code == 200
    assert client.get(url).json()["playback_rate"] == 1.75
    assert client.put(url, json={
        "position_seconds": -1, "active_unit_id": 1, "playback_rate": 1,
    }).status_code == 422
    assert client.put(url, json={
        "position_seconds": 0, "active_unit_id": 1, "playback_rate": 1.1,
    }).status_code == 422
    assert client.get("/api/generations/missing/playback").status_code == 404
