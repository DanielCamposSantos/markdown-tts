from __future__ import annotations

from pathlib import Path

import pytest

from app.desktop import APP_ID, DesktopLaunchError, HOST, URL, browser_candidates, launch, open_app_window


def test_existing_server_opens_window_without_starting_or_killing():
    calls = []
    result = launch(identity_probe=lambda: True, port_probe=lambda: True, server_starter=lambda: calls.append("server"), window_opener=lambda url: calls.append(url) or "edge")
    assert result["server_already_running"] and calls == [URL]


def test_missing_server_starts_once_and_waits_until_identity():
    states = iter((False, False, True)); calls = []
    result = launch(identity_probe=lambda: next(states), port_probe=lambda: False, server_starter=lambda: calls.append("server"), window_opener=lambda url: "edge", sleep=lambda _: None)
    assert not result["server_already_running"] and calls == ["server"]


def test_foreign_service_on_port_fails_without_killing():
    with pytest.raises(DesktopLaunchError, match="outro serviço"):
        launch(identity_probe=lambda: False, port_probe=lambda: True, server_starter=lambda: pytest.fail("must not start"))


def test_edge_then_chrome_candidates_and_app_mode(tmp_path):
    edge = tmp_path / "msedge.exe"; chrome = tmp_path / "chrome.exe"; edge.touch(); chrome.touch()
    found = browser_candidates({}, lambda name: str(edge) if name == "msedge.exe" else str(chrome))
    calls = []
    assert [path.name for path in found] == ["msedge.exe", "chrome.exe"]
    assert open_app_window(candidates=found, process_factory=lambda command: calls.append(command)) == "msedge.exe"
    assert calls == [[str(edge), f"--app={URL}"]]


def test_default_browser_fallback():
    calls = []
    assert open_app_window(candidates=(), fallback=lambda url: calls.append(url)) == "default-browser"
    assert calls == [URL]


def test_launcher_is_localhost_only_and_safe():
    assert HOST == "127.0.0.1" and "0.0.0.0" not in URL
    combined = (Path("scripts/start_desktop.ps1").read_text(encoding="utf-8") + Path("app/desktop.py").read_text(encoding="utf-8")).lower()
    assert "setx" not in combined and "$env:path" not in combined and "runas" not in combined
    assert "taskkill" not in combined and "terminate(" not in combined
    assert "c:\\users" not in combined and "mig e dan" not in combined


def test_health_identity_is_stable():
    import web
    assert web.health() == {"application": APP_ID, "status": "ok"}
