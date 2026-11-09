"""One-time setup. setup.cmd runs this on Windows; on macOS/Linux run:  python3 tools/setup_windows.py"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd: list, check: bool = True) -> int:
    print("> " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=check).returncode


def ffmpeg_found() -> bool:
    probe = [str(PY), "-c", "from clipper.media import find_ffmpeg; find_ffmpeg()"]
    return subprocess.run(probe, capture_output=True).returncode == 0


def main() -> int:
    print("== Gaming Shorts Clipper: setup ==\n")
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is needed. Install it with:  winget install Python.Python.3.12")
        return 1
    if sys.version_info >= (3, 14):
        print("Note: Python 3.12 is the safest choice; some packages may not support newer versions yet.\n")

    if not PY.exists():
        print("Creating a private Python environment in .venv (one time)...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    run([PY, "-m", "pip", "install", "--upgrade", "pip"])
    run([PY, "-m", "pip", "install", "-e", ROOT])

    if os.name == "nt":
        # PowerShell hands arguments to .cmd files unquoted, so a link containing '&' (e.g. ...&t=30s)
        # gets split by cmd.exe. An .exe receives it intact, and both shells prefer clipper.exe over
        # clipper.cmd. The launcher points at .venv by absolute path, so it works from the project root.
        launcher = VENV / "Scripts" / "clipper.exe"
        if launcher.exists():
            shutil.copy2(launcher, ROOT / "clipper.exe")

    config = ROOT / "config.yaml"
    if not config.exists():
        shutil.copy(ROOT / "config.example.yaml", config)
        print("Created config.yaml: your settings live there.")
    (ROOT / "assets" / "music").mkdir(parents=True, exist_ok=True)
    (ROOT / "output" / "ready").mkdir(parents=True, exist_ok=True)

    if not ffmpeg_found():
        print("\nFFmpeg (the video engine) isn't installed yet.")
        if os.name == "nt" and shutil.which("winget"):
            answer = input("Install it now with winget? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                run(["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                     "--accept-source-agreements", "--accept-package-agreements"], check=False)
        else:
            print("Install it with:  winget install Gyan.FFmpeg   (macOS: brew install ffmpeg, Linux: sudo apt install ffmpeg)")

    print()
    run([PY, "-m", "clipper", "doctor"], check=False)
    print("\nSetup finished. Next: put some music in assets\\music, then try:")
    print('  clipper peaks "https://www.youtube.com/watch?v=..."')
    return 0


if __name__ == "__main__":
    sys.exit(main())
