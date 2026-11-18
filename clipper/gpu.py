"""NVIDIA GPU support for speech recognition, and hardware video encoders (NVIDIA / AMD / Intel)."""

from __future__ import annotations

import ctypes
import glob
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NvidiaGpu:
    name: str
    memory_mb: int


def _nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.name == "nt":
        default = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        if default.exists():
            return str(default)
    return None


def nvidia_gpus() -> list[NvidiaGpu]:
    """NVIDIA GPUs reported by the driver (empty list if none, or no NVIDIA driver)."""
    exe = _nvidia_smi()
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    gpus = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            gpus.append(NvidiaGpu(parts[0], int(parts[1])))
    return gpus


def cuda_library_dirs() -> list[str]:
    """Folders of NVIDIA libraries installed with pip (nvidia-cublas-cu12), if any."""
    roots = {sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]}
    found = []
    for root in sorted(roots):
        for sub in ("bin", "lib"):
            for folder in sorted(glob.glob(os.path.join(root, "nvidia", "*", sub))):
                if folder not in found:
                    found.append(folder)
    return found


_PREPARED = False


def prepare_cuda_libraries() -> list[str]:
    """Make the pip-installed CUDA libraries findable before the speech model loads them."""
    global _PREPARED
    folders = cuda_library_dirs()
    if _PREPARED or not folders:
        return folders
    if os.name == "nt":
        for folder in folders:
            try:
                os.add_dll_directory(folder)
            except OSError:
                pass
        # libraries loaded later on demand only look at PATH, so add them there too
        os.environ["PATH"] = os.pathsep.join(folders + [os.environ.get("PATH", "")])
    else:
        for folder in folders:
            for lib in sorted(glob.glob(os.path.join(folder, "libcublas*.so*"))):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
    _PREPARED = True
    return folders


# Runs in a separate Python process: a missing or broken CUDA library can crash the process outright,
# and that must never take the clipper down with it.
_PROBE = """
import sys
import numpy as np
from clipper.gpu import prepare_cuda_libraries
prepare_cuda_libraries()
from faster_whisper import WhisperModel
model = WhisperModel(sys.argv[1], device="cuda", compute_type=sys.argv[2])
audio = np.random.default_rng(0).normal(0, 0.05, 16000 * 3).astype("float32")
segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=False)
list(segments)
print("GPU_OK")
"""


def _ct2_version() -> str:
    try:
        import ctranslate2

        return ctranslate2.__version__
    except Exception:
        return "?"


def probe_whisper(model_path: str, compute_type: str, cache_dir: Path, timeout: float = 900) -> tuple[bool, str]:
    """Can this model run on the GPU with this precision? Tested once, then remembered."""
    cache_file = cache_dir / "gpu_check.json"
    key = json.dumps([str(model_path), compute_type, _ct2_version(), [g.name for g in nvidia_gpus()], cuda_library_dirs()])
    try:
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if key in saved:
        return bool(saved[key]["ok"]), str(saved[key]["detail"])
    try:
        proc = subprocess.run([sys.executable, "-c", _PROBE, str(model_path), compute_type],
                              capture_output=True, text=True, timeout=timeout)
        ok = proc.returncode == 0 and "GPU_OK" in proc.stdout
        lines = [line for line in proc.stderr.strip().splitlines() if line.strip()]
        detail = "ok" if ok else (lines[-1][:300] if lines else f"exit code {proc.returncode}")
    except subprocess.TimeoutExpired:
        ok, detail = False, "the GPU test timed out"
    except OSError as exc:
        ok, detail = False, str(exc)
    saved[key] = {"ok": ok, "detail": detail}
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    return ok, detail


def forget_gpu_checks(cache_dir: Path) -> None:
    (cache_dir / "gpu_check.json").unlink(missing_ok=True)
