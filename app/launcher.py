from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from app.config import ROOT
from app.desktop import PORT, URL, open_app_window, port_is_open, probe_identity


STATES = {
    "inactive": ("Inativo", "INICIAR"),
    "starting": ("Iniciando", "INICIANDO..."),
    "active": ("Ativo", "ENCERRAR"),
    "external": ("Ativo externamente", "ABRIR"),
    "stopping": ("Encerrando", "ENCERRANDO..."),
    "error": ("Erro", "INICIAR"),
}


class LauncherError(RuntimeError):
    pass


class WindowsJobObject:
    """Owns child processes and kills them if the launcher handle disappears."""

    KILL_ON_JOB_CLOSE = 0x00002000
    EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, kernel32=None) -> None:
        if os.name != "nt" and kernel32 is None:
            raise LauncherError("Windows Job Object indisponível nesta plataforma.")
        native = kernel32 is None
        self._kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
        if native:
            self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
            self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            self._kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self._kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = self._extended_info()
        info.BasicLimitInformation.LimitFlags = self.KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            self.handle, self.EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _extended_info():
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        return EXTENDED_LIMIT()

    def assign(self, process) -> None:
        if not self._kernel32.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


def start_owned_server(process_factory=subprocess.Popen):
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    stream = (logs / "server.log").open("a", encoding="utf-8")
    environment = os.environ.copy()
    environment["MARKDOWN_TTS_NO_BROWSER"] = "1"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = process_factory(
        [sys.executable, str(ROOT / "web.py")], cwd=str(ROOT), env=environment,
        stdout=stream, stderr=subprocess.STDOUT, creationflags=flags,
    )
    return process, stream


def launcher_icon() -> Path | None:
    icon = ROOT / "assets" / "markdown_tts.ico"
    return icon if icon.is_file() else None


class LauncherController:
    def __init__(
        self, *, identity_probe: Callable[[], bool] = probe_identity,
        port_probe: Callable[[], bool] = port_is_open,
        process_starter=start_owned_server, job_factory=WindowsJobObject,
        window_opener=open_app_window, sleep=time.sleep,
    ) -> None:
        self.identity_probe, self.port_probe = identity_probe, port_probe
        self.process_starter, self.job_factory = process_starter, job_factory
        self.window_opener, self.sleep = window_opener, sleep
        self.process = self.log_stream = self.job = None
        self.state = "external" if identity_probe() else "inactive"
        self.error: str | None = None

    @property
    def owns_server(self) -> bool:
        return self.process is not None

    def start(self) -> str:
        self.error = None
        if self.identity_probe():
            self.state = "external"
            self.window_opener(URL)
            return self.state
        if self.port_probe():
            self.state, self.error = "error", f"A porta {PORT} está sendo usada por outro serviço."
            raise LauncherError(self.error)
        self.state = "starting"
        try:
            self.job = self.job_factory()
            self.process, self.log_stream = self.process_starter()
            self.job.assign(self.process)
            for _ in range(120):
                if self.process.poll() is not None:
                    raise LauncherError("O backend encerrou durante a inicialização.")
                if self.identity_probe():
                    self.state = "active"
                    self.window_opener(URL)
                    return self.state
                self.sleep(0.25)
            raise LauncherError("O backend não ficou pronto em 30 segundos. Consulte logs/server.log.")
        except Exception as exc:
            self._stop_owned()
            self.state, self.error = "error", str(exc)
            raise

    def stop(self) -> str:
        if not self.owns_server:
            self.state = "external" if self.identity_probe() else "inactive"
            return self.state
        self.state = "stopping"
        self._stop_owned()
        for _ in range(40):
            if not self.port_probe():
                break
            self.sleep(0.1)
        self.state = "inactive"
        return self.state

    def close(self) -> None:
        if self.owns_server:
            self.stop()
        elif self.job is not None:
            self.job.close()
            self.job = None

    def refresh_state(self) -> str:
        if self.owns_server and self.process.poll() is not None:
            self._stop_owned()
            self.state, self.error = "error", "O backend foi encerrado inesperadamente."
        elif not self.owns_server and self.state == "external" and not self.identity_probe():
            self.state = "inactive"
        elif not self.owns_server and self.state == "inactive" and self.identity_probe():
            self.state = "external"
        return self.state

    def _stop_owned(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.log_stream is not None:
            self.log_stream.close()
            self.log_stream = None


class LauncherWindow:
    COLORS = {"inactive": "#6b7280", "starting": "#d97706", "active": "#16803a", "external": "#16803a", "stopping": "#d97706", "error": "#b42318"}

    def __init__(self, root, controller: LauncherController) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.root, self.controller = root, controller
        self.closing = False
        root.title("Markdown TTS")
        root.geometry("360x220")
        root.resizable(False, False)
        icon = launcher_icon()
        if icon is not None:
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        frame = ttk.Frame(root, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Markdown TTS", font=("Segoe UI", 18, "bold")).pack(pady=(0, 25))
        self.status = tk.Label(frame, font=("Segoe UI", 11))
        self.status.pack(pady=(0, 22))
        self.button = ttk.Button(frame, command=self.toggle, width=22)
        self.button.pack()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.render()
        root.after(1000, self.monitor)

    def render(self) -> None:
        label, action = STATES[self.controller.state]
        self.status.configure(text=f"●  Status: {label}", fg=self.COLORS[self.controller.state])
        self.button.configure(text=action, state="disabled" if self.controller.state in {"starting", "stopping"} else "normal")

    def monitor(self) -> None:
        if self.closing:
            return
        self.controller.refresh_state()
        self.render()
        self.root.after(1000, self.monitor)

    def toggle(self) -> None:
        if self.controller.state == "external":
            self.controller.window_opener(URL)
            return
        action = self.controller.stop if self.controller.owns_server else self.controller.start
        self.controller.state = "stopping" if self.controller.owns_server else "starting"
        self.render()
        threading.Thread(target=self._run, args=(action,), daemon=True).start()

    def _run(self, action) -> None:
        try:
            action()
        except Exception:
            pass
        self.root.after(0, self.render)

    def on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.controller.state = "stopping" if self.controller.owns_server else self.controller.state
        self.render()
        threading.Thread(target=self._close, daemon=True).start()

    def _close(self) -> None:
        self.controller.close()
        self.root.after(0, self.root.destroy)


def main() -> int:
    import tkinter as tk

    root = tk.Tk()
    LauncherWindow(root, LauncherController())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
