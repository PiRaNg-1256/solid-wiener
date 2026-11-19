"""Downloading: only the needed section of a YouTube video, or its audio for loudness analysis."""

from __future__ import annotations

from pathlib import Path

from . import media
from .config import Config
from .metadata import explain_ytdlp_error, ytdlp_options
from .util import ClipperError

def video_format(height: int = 1080) -> str:
    """yt-dlp format choice. Up to 1080p: H.264 first (fast to decode). Above: whatever is sharpest, but never HDR:
    YouTube's HDR streams would look washed out in a normal (SDR) Short."""
    if height <= 1080:
        return (f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={height}][dynamic_range=SDR]+ba/"
                f"b[height<={height}]/bv*+ba/b")
    return (f"bv*[height<={height}][dynamic_range=SDR]+ba[ext=m4a]/bv*[height<={height}][dynamic_range=SDR]+ba/"
            "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*+ba/b")


VIDEO_FORMAT = video_format(1080)
AUDIO_FORMAT = "ba[ext=m4a]/ba/b"


def _download(url: str, opts: dict) -> Path:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise ClipperError(f"Download failed: {explain_ytdlp_error(exc)}") from None
    downloads = (info or {}).get("requested_downloads") or []
    path = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
    if not path or not path.exists():
        raise ClipperError("The download finished but the file wasn't found.")
    return path


def _usable(path: Path, need_video: bool) -> bool:
    try:
        info = media.probe(path)
    except ClipperError:
        return False
    return info.duration > 0.5 and (info.has_video if need_video else info.has_audio)


def _checked(path: Path, need_video: bool) -> Path:
    """yt-dlp can 'succeed' with an empty file; never keep (or reuse) a broken download."""
    if _usable(path, need_video):
        return path
    path.unlink(missing_ok=True)
    raise ClipperError(
        "The download came back empty. YouTube may have blocked it for a moment.\n"
        "Try again in a minute; if it keeps happening run  clipper update"
    )


def _cached(folder: Path, pattern: str, need_video: bool) -> Path | None:
    for p in folder.glob(pattern):
        if p.suffix in (".part", ".ytdl", ".json"):
            continue
        if _usable(p, need_video):
            return p
        p.unlink(missing_ok=True)
    return None


def download_segment(url: str, video_id: str, start: float, end: float, cfg: Config, height: int = 1080) -> Path:
    """Download [start, end] seconds, at most `height` pixels tall. The file's time 0 equals `start` in the
    original video."""
    from yt_dlp.utils import download_range_func

    folder = cfg.path(cfg.cache_dir) / "videos" / video_id
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"seg_{start:.1f}-{end:.1f}" + (f"_h{height}" if height != 1080 else "")
    cached = _cached(folder, stem + ".*", need_video=True)
    if cached:
        return cached
    opts = ytdlp_options(
        cfg,
        format=video_format(height),
        outtmpl=str(folder / (stem + ".%(ext)s")),
        merge_output_format="mp4/mkv",
        download_ranges=download_range_func(None, [(start, end)]),
        force_keyframes_at_cuts=False,
    )
    return _checked(_download(url, opts), need_video=True)


def download_audio(url: str, video_id: str, cfg: Config) -> Path:
    folder = cfg.path(cfg.cache_dir) / "videos" / video_id
    folder.mkdir(parents=True, exist_ok=True)
    cached = _cached(folder, "audio.*", need_video=False)
    if cached:
        return cached
    path = _download(url, ytdlp_options(cfg, format=AUDIO_FORMAT, outtmpl=str(folder / "audio.%(ext)s")))
    return _checked(path, need_video=False)


def cut_local(path: Path, start: float, end: float, folder: Path) -> Path:
    """Cut a section of a local file. Re-encoded at near-lossless quality so the cut is frame-exact
    (a stream copy would start at the previous keyframe and shift every timestamp)."""
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"local_{start:.1f}-{end:.1f}.mkv"
    media.run_ffmpeg(
        ["-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", "-c:a", "pcm_s16le", str(out)],
        what="Cutting the local video",
    )
    return out
