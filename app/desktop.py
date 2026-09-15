from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from app.config import ROOT


HOST = "127.0.0.1"
PORT = 7860
APP_ID = "markdown-tts"
URL = f"http://{HOST}:{PORT}"


class DesktopLaunchError(RuntimeError):
    pass


def probe_identity(url: str = URL, opener=urllib.request.urlopen) -> bool:
    try:
        with opener(f"{url}/api/health", timeout=1) as response:
            return json.loads(response.read().decode("utf-8")).get("application") == APP_ID
    except Exception:
        return False


def port_is_open(host: str = HOST, port: int = PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def browser_candidates(environment=os.environ, which=shutil.which) -> tuple[Path, ...]:
    candidates = []
    for executable in ("msedge.exe", "chrome.exe"):
        found = which(executable)
        if found:
            candidates.append(Path(found))
    locations = (
        ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
        ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    )
    for variable, relative in locations:
        root = environment.get(variable)
        path = Path(root) / relative if root else None
        if path and path.is_file() and path not in candidates:
            candidates.append(path)
    return tuple(candidates)


def open_app_window(url: str = URL, *, candidates=None, process_factory=subprocess.Popen, fallback=webbrowser.open) -> str:
    available = tuple(candidates) if candidates is not None else browser_candidates()
    if available:
        process_factory([str(available[0]), f"--app={url}"])
        return available[0].name
    fallback(url)
    return "default-browser"


def start_server(process_factory=subprocess.Popen) -> subprocess.Popen:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    stream = (logs / "desktop-server.log").open("a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return process_factory(
        [sys.executable, str(ROOT / "web.py")], cwd=str(ROOT),
        stdout=stream, stderr=subprocess.STDOUT, creationflags=flags,
    )


def launch(*, identity_probe=probe_identity, port_probe=port_is_open, server_starter=start_server, window_opener=open_app_window, sleep=time.sleep) -> dict:
    already_running = identity_probe()
    if not already_running:
        if port_probe():
            raise DesktopLaunchError(f"A porta {PORT} está ocupada por outro serviço; nenhum processo foi encerrado.")
        server_starter()
        for _ in range(120):
            if identity_probe():
                break
            sleep(0.25)
        else:
            raise DesktopLaunchError("O servidor Markdown TTS não ficou pronto em 30 segundos. Consulte logs/desktop-server.log.")
    browser = window_opener(URL)
    return {"server_already_running": already_running, "browser": browser, "url": URL}


def main() -> int:
    # Compatibility entrypoint: the supported desktop flow is now the owned GUI.
    from app.launcher import main as launcher_main
    return launcher_main()


if __name__ == "__main__":
    raise SystemExit(main())
