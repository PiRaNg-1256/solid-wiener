"""FFmpeg / FFprobe helpers: finding the binaries, probing, decoding audio, scene cuts, loudness."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .util import ClipperError

_TOOLS: dict[str, str] = {}


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def _candidates(configured: str) -> list[Path]:
    found: list[Path] = []
    if configured:
        p = Path(configured).expanduser()
        found.append(p / _exe("ffmpeg") if p.is_dir() else p)
    which = shutil.which("ffmpeg")
    if which:
        found.append(Path(which))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            # winget installs FFmpeg here; this works even before PATH is refreshed
            pattern = os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe")
            found.extend(Path(p) for p in sorted(glob.glob(pattern), reverse=True))
            found.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        found.extend([Path(r"C:\ffmpeg\bin\ffmpeg.exe"), Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe")])
    return found


def find_ffmpeg(configured: str = "") -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) paths or raise a readable error."""
    for candidate in _candidates(configured):
        if candidate.is_file():
            probe = candidate.with_name(_exe("ffprobe"))
            if probe.is_file():
                return str(candidate), str(probe)
            probe_on_path = shutil.which("ffprobe")
            if probe_on_path:
                return str(candidate), probe_on_path
    raise ClipperError(
        "FFmpeg was not found.\n"
        "On Windows install it with:  winget install Gyan.FFmpeg\n"
        "or set 'ffmpeg:' in config.yaml to the full path of ffmpeg.exe."
    )


def init(configured: str = "") -> None:
    ffmpeg, ffprobe = find_ffmpeg(configured)
    _TOOLS["ffmpeg"], _TOOLS["ffprobe"] = ffmpeg, ffprobe


def ffmpeg_bin() -> str:
    if "ffmpeg" not in _TOOLS:
        init()
    return _TOOLS["ffmpeg"]


def ffprobe_bin() -> str:
    if "ffprobe" not in _TOOLS:
        init()
    return _TOOLS["ffprobe"]


def _tail(text: str, lines: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def run_ffmpeg(args: list[str], cwd: str | Path | None = None, what: str = "FFmpeg") -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-nostats", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ClipperError(f"{what} failed:\n{_tail(proc.stderr)}")
    return proc


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_video: bool
    has_audio: bool


def _rate(text: str | None) -> float:
    if not text or text in ("0/0", "N/A"):
        return 0.0
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ClipperError(f"Can't read the media file {path}:\n{_tail(proc.stderr, 4)}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(data.get("format", {}).get("duration") or (video or {}).get("duration") or 0.0)
    width = height = 0
    fps = 0.0
    if video:
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
        rotation = 0
        for side in video.get("side_data_list") or []:
            if "rotation" in side:
                rotation = int(float(side["rotation"]))
        rotation = int(float((video.get("tags") or {}).get("rotate", rotation)))
        if abs(rotation) % 180 == 90:
            width, height = height, width
    return MediaInfo(duration, width, height, fps, video is not None, has_audio)


def decode_audio(
    path: str | Path,
    sr: int = 16000,
    channels: int = 1,
    start: float | None = None,
    duration: float | None = None,
) -> np.ndarray:
    """Decode audio to float32 samples (shape (n,) for mono, (n, channels) otherwise)."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    if duration is not None:
        cmd += ["-t", f"{max(0.0, duration):.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")
        if "does not contain any stream" in err or "matches no streams" in err:
            n = int(round((duration or 0.0) * sr))
            return np.zeros((n, channels) if channels > 1 else n, dtype=np.float32)
        raise ClipperError(f"Couldn't decode audio from {path}:\n{_tail(err, 4)}")
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels)
    return samples.copy()


def write_wav(path: str | Path, samples: np.ndarray, sr: int) -> None:
    from scipy.io import wavfile

    wavfile.write(str(path), sr, np.asarray(samples, dtype=np.float32))


def frame_jpeg(path: str | Path, at: float, width: int = 512) -> bytes:
    """One frame as a small JPEG (for Gemini to see what happens). Empty bytes if it fails."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(path),
           "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    return proc.stdout if proc.returncode == 0 else b""


_SCENE_RE = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),?\s*lavfi\.scd\.time:\s*([0-9.]+)")


def scene_cuts(path: str | Path, threshold: float = 10.0) -> list[float]:
    """Times (seconds) where the picture changes sharply. Empty list if detection fails."""
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-nostats", "-i", str(path), "-an",
        "-vf", f"scale=320:-2,scdet=threshold={threshold}", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return []
    return sorted({round(float(t), 3) for _score, t in _SCENE_RE.findall(proc.stderr)})


def ffmpeg_has(kind: str, name: str) -> bool:
    """kind: 'filters' or 'encoders'."""
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", f"-{kind}"], capture_output=True, text=True, errors="replace")
    return any(line.split()[1:2] == [name] for line in proc.stdout.splitlines() if len(line.split()) > 1)


def ffmpeg_version() -> str:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-version"], capture_output=True, text=True, errors="replace")
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    return first.replace("ffmpeg version ", "").split(" Copyright")[0]
