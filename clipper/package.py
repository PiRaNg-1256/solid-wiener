"""Output folder, ready-to-paste post text, and a meta.json with everything about the clip."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .config import Config, GameConfig
from .metadata import VideoInfo
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
) -> str:
    lines = []
    if hook:
        lines += [re.sub(r"\s*\|\s*", " ", hook).strip(), ""]
    if credit_on and not video.is_local:
        lines.append(f"🎮 Footage: {video.creator} → {video.link_at(source_start)}")
    if music_credit:
        lines.append(f"🎵 Music: {music_credit}")
    lines += ["", " ".join(tags)]
    return "\n".join(lines).strip() + "\n"


def new_clip_folder(output_root: Path, slug: str, when: datetime | None = None) -> Path:
    day = output_root / "review" / (when or datetime.now()).strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    numbers = [int(p.name[:3]) for p in day.iterdir() if p.is_dir() and p.name[:3].isdigit()]
    folder = day / f"{max(numbers, default=0) + 1:03d}_{slug}"
    folder.mkdir()
    return folder


def write_post(folder: Path, titles: list[str], desc: str, video: VideoInfo, start: float, end: float, extra: list[str]) -> Path:
    parts = ["TITLE OPTIONS (pick one, edit freely)"]
    parts += [f"{i}. {t}" for i, t in enumerate(titles, 1)] or ["(add a hook to get title ideas)"]
    parts += ["", "DESCRIPTION (paste as is)", desc.rstrip(), ""]
    parts += ["CHECK BEFORE POSTING"]
    parts += [
        "- No graphic violence in the first ~7 seconds (otherwise no ads).",
        "- Not focused on violence against civilians (YouTube age-restricts that).",
        "- No story spoilers in the hook or title.",
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
