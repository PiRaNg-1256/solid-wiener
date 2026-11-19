"""Output folder, ready-to-paste post text, and a meta.json with everything about the clip."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config, GameConfig
from .metadata import VideoInfo
from .profanity import has_swear, mask_text
from .util import fmt_time, strip_emoji

TITLE_LIMIT = 100  # YouTube's maximum title length


def detect_game(title: str, games: dict[str, GameConfig], forced: str | None = None) -> tuple[str, GameConfig] | None:
    if forced:
        key = forced.lower()
        return (key, games[key]) if key in games else None
    low = title.lower()
    for key, game in games.items():  # first match wins, so more specific games go first in config
        for keyword in game.keywords:
            if re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", low):
                return key, game
    return None


def hashtags_for(game: GameConfig | None, defaults: list[str]) -> list[str]:
    tags: list[str] = []
    for tag in (game.hashtags if game else []) + defaults:
        tag = tag if tag.startswith("#") else f"#{tag}"
        if tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    return tags


def _fit_title(text: str) -> str:
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text if len(text) <= TITLE_LIMIT else text[: TITLE_LIMIT - 1].rstrip() + "…"


def title_options(hook: str | None, game: GameConfig | None, templates: list[str], tags: list[str]) -> list[str]:
    tag = tags[0] if tags else ""
    game_name = game.name if game else ""
    clean_hook = re.sub(r"\s*\|\s*", " ", hook or "").strip()
    if has_swear(clean_hook):  # no swear words in titles, so skip the templates that repeat the hook
        clean_hook = ""
    titles = []
    for template in templates:
        if ("{hook}" in template and not clean_hook) or ("{game}" in template and not game):
            continue
        title = _fit_title(template.format(hook=clean_hook, game=game_name, tag=tag))
        if title not in titles:
            titles.append(title)
    return titles


def description(
    hook: str | None,
    video: VideoInfo,
    source_start: float,
    music_credit: str | None,
    tags: list[str],
    credit_on: bool,
    summary: str = "",
) -> str:
    lines = []
    if summary:
        lines += [mask_text(summary.strip(), "strict"), ""]
    elif hook:
        lines += [mask_text(re.sub(r"\s*\|\s*", " ", hook).strip(), "strict"), ""]
    if credit_on and not video.is_local:
        lines.append(f"🎮 Footage: {video.creator} → {video.link_at(source_start)}")
    if music_credit:
        lines.append(f"🎵 Music: {music_credit}")
    lines += ["", " ".join(tags)]
    return "\n".join(lines).strip() + "\n"


_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_name(text: str, max_len: int = 70) -> str:
    """A folder name that works on Windows: no <>:"/\\|?*, no emoji, no trailing dots, not too long."""
    name = strip_emoji(re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text))
    name = re.sub(r"\s+", " ", name).strip(" .")[:max_len].rstrip(" .")
    if name.lower() in _RESERVED:
        name += "_"
    return name


def video_folder(output_root: Path, video: VideoInfo) -> Path:
    """output/<YouTube video title>/ (the id is added only if another video already has that title)."""
    name = safe_name(video.title) or safe_name(video.id) or "video"
    folder = output_root / name
    marker = folder / ".video-id"
    if folder.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() != video.id:
        folder = output_root / f"{name} [{safe_name(video.id, 20)}]"
        marker = folder / ".video-id"
    folder.mkdir(parents=True, exist_ok=True)
    marker.write_text(video.id, encoding="utf-8")
    return folder


def clip_folder(video_dir: Path, source_start: float, source_end: float) -> Path:
    """'clip 12.30-12.58' inside the video's folder. Making the same moment again reuses (empties) it."""
    name = f"clip {fmt_time(source_start).replace(':', '.')}-{fmt_time(source_end).replace(':', '.')}"
    folder = video_dir / name
    if folder.exists():
        for item in folder.iterdir():
            if item.is_file():
                item.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_post(folder: Path, titles: list[str], desc: str, video: VideoInfo, start: float, end: float, extra: list[str],
               top_note: str = "") -> Path:
    parts = ([top_note, ""] if top_note else []) + ["TITLE OPTIONS (pick one, edit freely)"]
    parts += [f"{i}. {t}" for i, t in enumerate(titles, 1)] or ["(add a hook to get title ideas)"]
    parts += ["", "DESCRIPTION (paste as is)", desc.rstrip(), ""]
    parts += ["CHECK BEFORE POSTING"]
    parts += [
        "- No graphic violence in the first ~7 seconds (otherwise no ads).",
        "- Not focused on violence against civilians (YouTube age-restricts that).",
        "- No story spoilers in the hook or title.",
        "- No swear words in the title or the cover picture (otherwise no ads). The tool keeps them out of the "
        "titles above and hides them as f**k in the captions.",
    ]
    if extra:
        parts += ["", "NOTES"] + [f"- {x}" for x in extra]
    if not video.is_local:
        parts += ["", f"Source: {video.title} ({video.url}), {fmt_time(start)}-{fmt_time(end)}"]
    path = folder / "post.txt"
    path.write_text("\n".join(p.rstrip() for p in parts) + "\n", encoding="utf-8")
    return path


def write_meta(folder: Path, meta: dict) -> Path:
    path = folder / "meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def hook_suggestions(video: VideoInfo, chapter: str, transcript: str) -> list[str]:
    ideas = []
    title = strip_emoji(re.sub(r"[\[(][^\])]*[\])]|#\w+", "", video.title)).strip(" -|:")
    if title:
        ideas.append(" ".join(title.split()[:7]))
    if chapter:
        ideas.append(chapter)
    if transcript:
        ideas.append(" ".join(transcript.split()[:6]))
    seen, unique = set(), []
    for idea in ideas:
        if idea and idea.lower() not in seen:
            seen.add(idea.lower())
            unique.append(idea)
    return unique


def credit_line(cfg: Config, video: VideoInfo) -> str | None:
    if not cfg.style.credit or video.is_local or not video.creator:
        return None
    return cfg.style.credit_text.format(creator=video.creator)
