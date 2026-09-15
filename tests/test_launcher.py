from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from app.desktop import URL
from app.launcher import LauncherController, LauncherError, WindowsJobObject, launcher_icon


class FakeProcess:
    _handle = 42

    def __init__(self):
        self.returncode = None
        self.terminated = self.killed = False

    def poll(self): return self.returncode
    def terminate(self): self.terminated = True; self.returncode = 0
    def kill(self): self.killed = True; self.returncode = -1
    def wait(self, timeout=None): return self.returncode


class FakeJob:
    def __init__(self): self.assigned = self.closed = False
    def assign(self, process): self.assigned = process
    def close(self): self.closed = True


def controller(identity_states, *, port=False):
    states = iter(identity_states)
    process, job, opened = FakeProcess(), FakeJob(), []
    item = LauncherController(
        identity_probe=lambda: next(states), port_probe=lambda: port,
        process_starter=lambda: (process, None), job_factory=lambda: job,
        window_opener=lambda url: opened.append(url), sleep=lambda _: None,
    )
    return item, process, job, opened


def test_initial_start_health_active_and_toggle_stop():
    item, process, job, opened = controller((False, False, True))
    assert item.state == "inactive"
    assert item.start() == "active" and item.owns_server
    assert job.assigned is process and opened == [URL]
    assert item.stop() == "inactive"
    assert process.terminated and job.closed and not item.owns_server


def test_close_owned_backend_stops_it():
    item, process, job, _ = controller((False, False, True))
    item.start(); item.close()
    assert process.terminated and job.closed


def test_foreign_service_is_error_and_never_started_or_killed():
    item, process, job, _ = controller((False, False), port=True)
    with pytest.raises(LauncherError, match="outro serviço"):
        item.start()
    assert item.state == "error" and not process.terminated and not job.closed


def test_external_markdown_tts_is_not_owned_or_stopped():
    item, process, job, opened = controller((True, True))
    assert item.state == "external" and item.start() == "external"
    item.close()
    assert not item.owns_server and not process.terminated and not job.closed
    assert opened == [URL]


def test_start_failure_becomes_error_and_closes_job():
    item, process, job, _ = controller((False, False))
    process.returncode = 4
    with pytest.raises(LauncherError, match="inicialização"):
        item.start()
    assert item.state == "error" and job.closed


def test_monitor_detects_owned_crash_and_external_shutdown():
    item, process, job, _ = controller((False, False, True))
    item.start(); process.returncode = 9
    assert item.refresh_state() == "error" and job.closed and not item.owns_server
    external, _, _, _ = controller((True, False))
    assert external.refresh_state() == "inactive"


def test_windows_job_object_sets_kill_on_close_and_assigns_child():
    class Kernel:
        def __init__(self): self.info = self.assigned = self.closed = None
        def CreateJobObjectW(self, *_): return 99
        def SetInformationJobObject(self, handle, info_class, pointer, size):
            self.info = (handle, info_class, ctypes.string_at(pointer, size)); return 1
        def AssignProcessToJobObject(self, handle, process): self.assigned = (handle, process); return 1
        def CloseHandle(self, handle): self.closed = handle; return 1
    kernel = Kernel(); job = WindowsJobObject(kernel)
    process = FakeProcess(); job.assign(process); job.close()
    assert kernel.info[0:2] == (99, 9)
    assert (WindowsJobObject.KILL_ON_JOB_CLOSE).to_bytes(4, "little") in kernel.info[2]
    assert kernel.assigned == (99, 42) and kernel.closed == 99


def test_scripts_are_safe_dynamic_and_use_pythonw():
    content = "\n".join(Path(path).read_text(encoding="utf-8") for path in (
        "app/launcher.py", "app/desktop.py", "scripts/start_launcher.ps1", "scripts/create_launcher_shortcut.ps1",
    )).lower()
    assert "127.0.0.1" in content and "0.0.0.0" not in content
    assert "pythonw.exe" in content and "getfolderpath" in content
    assert "taskkill /im python.exe" not in content and "taskkill" not in content
    assert "edge /im" not in content and "chrome /im" not in content
    assert "runas" not in content and "c:\\users\\" not in content


def test_icon_has_safe_absent_fallback(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert launcher_icon() is None
