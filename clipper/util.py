"""Small helpers shared by the other modules."""

from __future__ import annotations

import re
import unicodedata


class ClipperError(Exception):
    """An error whose message is meant for the user (shown without a traceback)."""


def parse_time(value: str | float | int) -> float:
    """Read '75', '1:15', '01:01:15' or '75.5' as seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = value.strip().split(":")
    try:
        if not 1 <= len(parts) <= 3:
            raise ValueError
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
    except ValueError:
        raise ClipperError(
            f"Can't read the time '{value}'. Use seconds (75), mm:ss (1:15) or hh:mm:ss."
        ) from None
    if total < 0:
        raise ClipperError(f"Time can't be negative: '{value}'")
    return total


def fmt_time(seconds: float) -> str:
    """83.4 -> '1:23', 3725 -> '1:02:05'."""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def ass_time(seconds: float) -> str:
    """Timestamp format used in .ass subtitle files: H:MM:SS.cc"""
    centis = max(0, int(round(seconds * 100)))
    hours, rest = divmod(centis, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def slugify(text: str, max_len: int = 40, fallback: str = "clip") -> str:
    """ASCII-only, lowercase, hyphenated; falls back when nothing is left (e.g. Hindi text)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    slug = slug[:max_len].rstrip("-")
    return slug or fallback


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoticons, pictographs, transport, flags, symbols
    "\U00002600-\U000027BF"  # misc symbols and dingbats
    "\U00002B00-\U00002BFF"  # arrows, stars
    "\U0000FE0F"  # emoji presentation selector
    "]+"
)


def strip_emoji(text: str) -> str:
    """Remove emoji: the subtitle renderer can't draw colour emoji."""
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", text)).strip()


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """'#FFE600' -> '&H0000E6FF' (ASS colours are &HAABBGGRR)."""
    value = color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ClipperError(f"Colour '{color}' must look like #RRGGBB")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


_YOUTUBE_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def youtube_id(url: str) -> str | None:
    match = _YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def is_url(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://", "www.", "youtu.be/"))


def normalize_url(text: str) -> str:
    text = text.strip()
    if text.lower().startswith(("www.", "youtu.be/")):
        return "https://" + text
    return text
