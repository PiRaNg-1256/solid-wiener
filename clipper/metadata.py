"""Video info from YouTube via yt-dlp: title, channel, duration, and the "Most replayed" heatmap."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import media
from .config import Config
from .util import ClipperError, youtube_id

CACHE_HOURS = 6  # the graph changes as views come in, so don't keep it too long


@dataclass
class VideoInfo:
    id: str
    url: str
    title: str
    channel: str
    handle: str  # "@creator" when YouTube gives one
    duration: float
    view_count: int | None = None
    upload_date: str | None = None
    heatmap: list[dict] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    fetched_at: float = 0.0
    local_path: str = ""  # set for local files instead of YouTube links

    @property
    def creator(self) -> str:
        return self.handle or self.channel

    @property
    def is_local(self) -> bool:
        return bool(self.local_path)

    def link_at(self, seconds: float) -> str:
        if self.is_local or not self.id:
            return ""
        return f"https://www.youtube.com/watch?v={self.id}&t={int(seconds)}s"


def ytdlp_options(cfg: Config, **extra) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "ffmpeg_location": media.ffmpeg_bin(),
    }
    if cfg.youtube.sleep_between_requests > 0:
        opts["sleep_interval_requests"] = cfg.youtube.sleep_between_requests
    if cfg.youtube.cookies_file:
        opts["cookiefile"] = str(cfg.path(cfg.youtube.cookies_file))
    if cfg.youtube.cookies_from_browser:
        opts["cookiesfrombrowser"] = (cfg.youtube.cookies_from_browser.strip().lower(),)
    opts.update(extra)
    return opts


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def explain_ytdlp_error(exc: Exception) -> str:
    msg = _ANSI_RE.sub("", str(exc)).replace("ERROR: ", "").strip()
    low = msg.lower()
    if "not a bot" in low or "sign in to confirm" in low:
        hint = (
            "YouTube wants a sign-in check. In config.yaml set youtube.cookies_from_browser: firefox "
            "(log into a throwaway Google account in Firefox first), then try again."
        )
    elif "private video" in low or "members-only" in low or "join this channel" in low:
        hint = "This video isn't public, so it can't be clipped."
    elif "video unavailable" in low or "has been removed" in low:
        hint = "The video is unavailable (deleted, blocked in your country, or the link is wrong)."
    elif "429" in low or "too many requests" in low:
        hint = "YouTube is rate-limiting you. Wait a while, or set youtube.sleep_between_requests: 2 in config.yaml."
    else:
        hint = "If this keeps happening run  clipper update  (YouTube changes often break the downloader)."
    return f"{msg}\n{hint}"


def _handle(raw: dict) -> str:
    uploader_id = raw.get("uploader_id") or ""
    if uploader_id.startswith("@"):
        return uploader_id
    for key in ("uploader_url", "channel_url"):
        match = re.search(r"/(@[\w.\-]+)", raw.get(key) or "")
        if match:
            return match.group(1)
    return ""


def _from_raw(raw: dict, url: str) -> VideoInfo:
    return VideoInfo(
        id=raw.get("id") or "",
        url=raw.get("webpage_url") or url,
        title=raw.get("title") or "",
        channel=raw.get("channel") or raw.get("uploader") or "",
        handle=_handle(raw),
        duration=float(raw.get("duration") or 0.0),
        view_count=raw.get("view_count"),
        upload_date=raw.get("upload_date"),
        heatmap=[
            {"start_time": float(p["start_time"]), "end_time": float(p["end_time"]), "value": float(p.get("value") or 0)}
            for p in raw.get("heatmap") or []
            if p.get("start_time") is not None and p.get("end_time") is not None
        ],
        chapters=[
            {"start_time": float(c.get("start_time") or 0), "end_time": float(c.get("end_time") or 0), "title": c.get("title") or ""}
            for c in raw.get("chapters") or []
        ],
        fetched_at=time.time(),
    )


def _cache_file(cfg: Config, video_id: str) -> Path:
    return cfg.path(cfg.cache_dir) / "videos" / video_id / "info.json"


def load_cached(cfg: Config, video_id: str, max_age_hours: float = CACHE_HOURS) -> VideoInfo | None:
    path = _cache_file(cfg, video_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        info = VideoInfo(**data)
    except (ValueError, TypeError):
        return None
    if time.time() - info.fetched_at > max_age_hours * 3600:
        return None
    return info


def fetch_info(url: str, cfg: Config, refresh: bool = False) -> VideoInfo:
    video_id = youtube_id(url)
    if video_id and not refresh:
        cached = load_cached(cfg, video_id)
        if cached:
            return cached
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(ytdlp_options(cfg, skip_download=True)) as ydl:
            raw = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise ClipperError(f"Couldn't read the video info: {explain_ytdlp_error(exc)}") from None
    if not raw:
        raise ClipperError("YouTube returned no information for that link.")
    if raw.get("_type") in ("playlist", "multi_video"):
        raise ClipperError("That's a playlist link. Use the link of a single video.")
    if raw.get("is_live") or raw.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise ClipperError("This video is live, upcoming or still processing. Try again once the stream has finished.")
    info = _from_raw(raw, url)
    path = _cache_file(cfg, info.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(info), ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def chapter_at(info: VideoInfo, seconds: float) -> str:
    for chapter in info.chapters:
        if chapter["start_time"] <= seconds < (chapter["end_time"] or float("inf")):
            return chapter["title"]
    return ""
