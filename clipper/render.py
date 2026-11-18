"""Final encode: layout + subtitles + audio into an H.264 MP4 for Shorts."""

from __future__ import annotations

from pathlib import Path

from . import media
from .config import RenderConfig
from .layout import Layout


def render_video(
    segment: Path,
    clip_start: float,
    clip_end: float,
    layout: Layout,
    workdir: Path,
    audio_wav: Path,
    out_path: Path,
    cfg: RenderConfig,
    src_fps: float,
    ass_name: str = "captions.ass",
) -> None:
    """workdir must contain the .ass file and a fonts/ folder. FFmpeg runs inside workdir and refers
    to them by relative name: Windows paths (C:\\...) break FFmpeg's subtitle filter otherwise."""
    filters = f"[0:v]{layout.filter},ass={ass_name}:fontsdir=fonts"
    if src_fps and src_fps > cfg.max_fps + 0.5:
        filters += f",fps={cfg.max_fps}"
    filters += ",format=yuv420p[v]"
    media.run_ffmpeg(
        [
            "-y",
            "-ss", f"{clip_start:.3f}", "-t", f"{clip_end - clip_start:.3f}", "-i", str(Path(segment).resolve()),
            "-i", str(Path(audio_wav).resolve()),
            "-filter_complex", filters,
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-shortest", "-movflags", "+faststart",
            str(Path(out_path).resolve()),
        ],
        cwd=workdir,
        what="Rendering the Short",
    )


def thumbnail(video: Path, at: float, out_path: Path) -> None:
    media.run_ffmpeg(
        ["-y", "-ss", f"{max(0.0, at):.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", "-update", "1", str(out_path)],
        what="Saving the thumbnail",
    )


def replace_audio(video: Path, audio_wav: Path, out_path: Path) -> None:
    """Same video, different audio track (used for the no-music version). No re-encode of the picture."""
    media.run_ffmpeg(
        ["-y", "-i", str(video), "-i", str(audio_wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out_path)],
        what="Making the no-music version",
    )
