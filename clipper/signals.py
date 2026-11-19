"""Where viewers point at the best moments when there is no "Most replayed" graph yet:
- comments with timestamps ("4:32 💀💀"), weighted by likes;
- for finished livestreams, bursts in the live-chat replay (more messages, more "LOL / 😂 / 💀")."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import numpy as np

from .config import Config
from .metadata import explain_ytdlp_error, ytdlp_options
from .util import ClipperError

TIMESTAMP_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)(?![\d:])")
HYPE_RE = re.compile(r"(\b(lol|lmao|lmfao|rofl|kekw?|omg|wtf|bruh|xd|pog\w*|w{1,4}|(ha){2,})\b|😂|🤣|💀|🔥|😭|😱|🤯|!{2,}|\?{2,})", re.I)
CACHE_HOURS = 6


def comment_points(comments: list[dict], duration: float) -> list[tuple[float, float]]:
    """(time, weight) for every timestamp in the comments. Likes count; long timestamp lists (chapters) count less."""
    points = []
    for c in comments:
        text = str(c.get("text") or "")
        found = []
        for h, m, s in TIMESTAMP_RE.findall(text):
            t = int(h or 0) * 3600 + int(m) * 60 + int(s)
            if 0 < t < (duration or float("inf")):
                found.append(float(t))
        if not found:
            continue
        weight = 1.0 + math.log1p(max(0, int(c.get("like_count") or 0)))
        if len(found) > 3:  # a list of chapters points everywhere, so it says little about one moment
            weight /= len(found)
        points.extend((t, weight) for t in found)
    return points


def chat_points(lines) -> list[tuple[float, float]]:
    """(time, weight) for each live-chat replay message (yt-dlp's .live_chat.json, one JSON object per line)."""
    points = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        replay = item.get("replayChatItemAction") or {}
        try:
            t = int(replay.get("videoOffsetTimeMsec", item.get("videoOffsetTimeMsec"))) / 1000
        except (TypeError, ValueError):
            continue
        for action in replay.get("actions") or []:
            renderer = ((action.get("addChatItemAction") or {}).get("item") or {}).get("liveChatTextMessageRenderer")
            if not renderer:
                continue
            text = ""
            for run in (renderer.get("message") or {}).get("runs") or []:
                text += run.get("text") or ((run.get("emoji") or {}).get("shortcuts") or [""])[0]
            points.append((max(0.0, t), 2.0 if HYPE_RE.search(text) else 1.0))
    return points


def points_to_heatmap(points: list[tuple[float, float]], duration: float, bucket: float = 5.0, sigma: float = 6.0,
                      baseline: bool = False) -> list[dict]:
    """Spread (time, weight) points into a smooth 0-1 graph like YouTube's heatmap.
    baseline=True subtracts the usual activity level (for chat, which is never quiet)."""
    if not points or duration <= 0:
        return []
    edges = np.arange(0.0, duration + bucket, bucket)
    counts = np.zeros(len(edges) - 1)
    for t, w in points:
        i = min(len(counts) - 1, int(t // bucket))
        counts[i] += w
    radius = max(1, int(3 * sigma / bucket))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) * bucket / sigma) ** 2)
    smooth = np.convolve(counts, kernel / kernel.sum(), mode="same")
    if baseline:
        window = max(3, int(300 / bucket))  # 5-minute rolling median = normal chat speed
        pad = np.pad(smooth, window // 2, mode="edge")
        base = np.array([np.median(pad[i : i + window]) for i in range(len(smooth))])
        smooth = np.clip(smooth - base, 0, None)
    top = smooth.max()
    if top <= 0:
        return []
    values = smooth / top
    return [{"start_time": float(a), "end_time": float(min(b, duration)), "value": float(v)}
            for a, b, v in zip(edges[:-1], edges[1:], values)]


def _cache_dir(cfg: Config, video_id: str) -> Path:
    folder = cfg.path(cfg.cache_dir) / "videos" / video_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def fetch_comments(url: str, video_id: str, cfg: Config, limit: int = 400) -> list[dict]:
    """Top comments (text + likes). Cached for a few hours; new comments keep arriving on new videos."""
    cache = _cache_dir(cfg, video_id) / "comments.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_HOURS * 3600:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    import yt_dlp

    opts = ytdlp_options(cfg, skip_download=True, getcomments=True, extractor_args={
        "youtube": {"max_comments": [str(limit), str(limit), "0", "0"], "comment_sort": ["top"]},
    })
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except yt_dlp.utils.DownloadError as exc:
        raise ClipperError(f"Couldn't read the comments: {explain_ytdlp_error(exc)}") from None
    comments = [{"text": c.get("text") or "", "like_count": c.get("like_count") or 0} for c in info.get("comments") or []]
    cache.write_text(json.dumps(comments, ensure_ascii=False), encoding="utf-8")
    return comments


def fetch_chat_replay(url: str, video_id: str, cfg: Config) -> Path | None:
    """Download the live-chat replay of a finished livestream (None if the stream had no chat)."""
    folder = _cache_dir(cfg, video_id)
    existing = folder / "chat.live_chat.json"
    if existing.exists() and existing.stat().st_size > 0:
        return existing
    import yt_dlp

    opts = ytdlp_options(cfg, skip_download=True, writesubtitles=True, subtitleslangs=["live_chat"],
                         outtmpl=str(folder / "chat.%(ext)s"))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise ClipperError(f"Couldn't read the live chat: {explain_ytdlp_error(exc)}") from None
    return existing if existing.exists() and existing.stat().st_size > 0 else None


def comments_heatmap(comments: list[dict], duration: float) -> list[dict]:
    points = comment_points(comments, duration)
    if len(points) < 3:  # too little to go on
        return []
    return points_to_heatmap(points, duration, bucket=5.0, sigma=6.0)


def chat_heatmap(chat_file: Path, duration: float) -> list[dict]:
    with chat_file.open(encoding="utf-8", errors="replace") as fh:
        points = chat_points(fh)
    if len(points) < 50:
        return []
    return points_to_heatmap(points, duration, bucket=5.0, sigma=8.0, baseline=True)
