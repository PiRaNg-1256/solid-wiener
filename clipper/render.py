"""Final encode: layout + subtitles + audio into an H.264 MP4 for Shorts, on the GPU when possible."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import media
from .config import RenderConfig
from .layout import Layout
from .util import ClipperError

HARDWARE_ENCODERS = ("h264_nvenc", "h264_amf", "h264_qsv")  # NVIDIA, AMD, Intel


def encoder_args(encoder: str, cfg: RenderConfig) -> list[str]:
    """Quality settings per encoder, roughly matched to x264's crf."""
    q = cfg.crf
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(q + 2), "-b:v", "0",
                "-profile:v", "high"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", str(q + 2), "-qp_p", str(q + 4)]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "slow", "-global_quality", str(q + 4)]
    return ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(q), "-profile:v", "high"]


def _pix_fmt(encoder: str) -> str:
    return "nv12" if encoder == "h264_qsv" else "yuv420p"


_WORKS: dict[str, bool] = {}  # remembered for this run


def encoder_works(encoder: str) -> bool:
    """Try a tiny encode: an encoder can be built into FFmpeg without the matching GPU being present."""
    if encoder == "libx264":
        return True
    if encoder not in _WORKS:
        ok = False
        if media.ffmpeg_has("encoders", encoder):
            cmd = [media.ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi",
                   "-i", "color=c=black:s=1080x1920:r=30:d=0.2", "-frames:v", "5", "-pix_fmt", _pix_fmt(encoder),
                   *encoder_args(encoder, RenderConfig())[:2], "-f", "null", "-"]
            try:
                ok = subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
        _WORKS[encoder] = ok
    return _WORKS[encoder]


def pick_encoder(cfg: RenderConfig) -> str:
    if cfg.encoder != "auto":
        return cfg.encoder
    return next((e for e in HARDWARE_ENCODERS if encoder_works(e)), "libx264")


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
) -> str:
    """Returns the encoder used. workdir must contain the .ass file and a fonts/ folder. FFmpeg runs inside
    workdir and refers to them by relative name: Windows paths (C:\\...) break FFmpeg's subtitle filter otherwise."""
    encoder = pick_encoder(cfg)
    try:
        _encode(encoder, segment, clip_start, clip_end, layout, workdir, audio_wav, out_path, cfg, src_fps, ass_name)
    except ClipperError:
        if encoder == "libx264":
            raise
        _WORKS[encoder] = False  # don't try this encoder again in this run
        encoder = "libx264"
        _encode(encoder, segment, clip_start, clip_end, layout, workdir, audio_wav, out_path, cfg, src_fps, ass_name)
    return encoder


def _encode(encoder, segment, clip_start, clip_end, layout, workdir, audio_wav, out_path, cfg, src_fps, ass_name) -> None:
    filters = f"[0:v]{layout.filter},ass={ass_name}:fontsdir=fonts"
    if src_fps and src_fps > cfg.max_fps + 0.5:
        filters += f",fps={cfg.max_fps}"
    filters += f",format={_pix_fmt(encoder)}[v]"
    media.run_ffmpeg(
        [
            "-y",
            "-ss", f"{clip_start:.3f}", "-t", f"{clip_end - clip_start:.3f}", "-i", str(Path(segment).resolve()),
            "-i", str(Path(audio_wav).resolve()),
            "-filter_complex", filters,
            "-map", "[v]", "-map", "1:a",
            *encoder_args(encoder, cfg), "-pix_fmt", _pix_fmt(encoder),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-shortest", "-movflags", "+faststart",
            str(Path(out_path).resolve()),
        ],
        cwd=workdir,
        what=f"Rendering the Short ({encoder})",
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
