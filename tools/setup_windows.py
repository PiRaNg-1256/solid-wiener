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


def run(cmd: list, check: bool = True, cwd: Path | None = None) -> int:
    print("> " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=check, cwd=cwd).returncode


def nvidia_gpu_name() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        default = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(default) if default.exists() else None
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def ffmpeg_found() -> bool:
    probe = [str(PY), "-c", "from clipper.media import find_ffmpeg; find_ffmpeg()"]
    return subprocess.run(probe, capture_output=True).returncode == 0


OLD_DEFAULTS = (
    "  model: small              # tiny / base / small / medium / large-v3: bigger = more accurate but slower.\n"
    "                            # 'medium' is noticeably better for Hindi.\n"
    "  device: cpu               # cpu, or cuda if you have an NVIDIA GPU with CUDA 12 + cuDNN 9 installed\n"
)
NEW_DEFAULTS = (
    "  model: auto               # auto = large-v3 on an NVIDIA GPU, small on the CPU.\n"
    "                            # Or pick: tiny / base / small / medium / large-v3 (bigger = more accurate, slower)\n"
    "  device: auto              # auto = use the NVIDIA GPU if it passes a one-time test, else the CPU. Or: cpu / cuda\n"
)


def upgrade_config(path: Path) -> bool:
    """Older setups copied 'model: small / device: cpu'. Switch those untouched defaults to auto (GPU when
    possible) and add the Gemini key line. Lines the user changed are left alone."""
    text = path.read_text(encoding="utf-8")
    if OLD_DEFAULTS not in text:
        return False
    path.write_text(text.replace(OLD_DEFAULTS, NEW_DEFAULTS), encoding="utf-8")
    return True


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
    gpu = nvidia_gpu_name()
    if gpu:
        print(f"NVIDIA GPU found: {gpu}. Also installing NVIDIA's GPU library for speech recognition "
              "(about 550 MB, one time).")
        run([PY, "-m", "pip", "install", "-e", ".[gpu]"], cwd=ROOT)
    else:
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
    elif upgrade_config(config):
        print("Updated config.yaml: speech recognition now uses your GPU when possible (captions.device: auto).")
    (ROOT / "assets" / "music").mkdir(parents=True, exist_ok=True)
    if not (ROOT / "clips.xlsx").exists():
        run([PY, "-m", "clipper", "run"], check=False)  # creates clips.xlsx (with a 'How to use' tab)
    (ROOT / "output").mkdir(parents=True, exist_ok=True)

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
    print("\nSetup finished. Next:")
    print("  1. Optional: paste your free Gemini key into gemini_api_key at the top of config.yaml")
    print("  2. Put some music in assets\\music")
    print("  3. Double-click menu.cmd, or put video links in clips.xlsx, save and close it, then double-click run.cmd")
    print("  The full manual is README.md (it opens nicely on the project's GitHub page).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
