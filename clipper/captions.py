"""Animated word-by-word captions, hook title and credit line as an .ass subtitle file."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .config import StyleConfig
from .layout import OUT_H, OUT_W, Layout
from .transcribe import Word
from .util import ass_time, has_devanagari, hex_to_ass, strip_emoji

SENTENCE_END = (".", "?", "!", "।", "…")
CAPTION_MAX_WIDTH = 940  # px a caption line may use (frame is 1080 wide)
HOOK_MAX_WIDTH = 900


@lru_cache(maxsize=16)
def _font_metrics(path: str) -> tuple[int, int, int]:
    """(unitsPerEm, usWinAscent, usWinDescent) read straight from the font file."""
    data = Path(path).read_bytes()
    base = struct.unpack(">I", data[12:16])[0] if data[:4] == b"ttcf" else 0
    count = struct.unpack(">H", data[base + 4 : base + 6])[0]
    tables = {}
    for i in range(count):
        tag, _checksum, offset, _length = struct.unpack(">4sIII", data[base + 12 + 16 * i : base + 28 + 16 * i])
        tables[tag] = offset
    upm = struct.unpack(">H", data[tables[b"head"] + 18 : tables[b"head"] + 20])[0]
    os2 = tables.get(b"OS/2")
    if os2 is None:
        return upm, upm, 0
    win_ascent, win_descent = struct.unpack(">HH", data[os2 + 74 : os2 + 78])
    return upm, win_ascent, win_descent


@dataclass
class Font:
    """A font file plus the conversions between real pixel sizes and .ass sizes."""

    path: Path

    @property
    def ass_name(self) -> str:
        family, style = ImageFont.truetype(str(self.path), 20).getname()
        return family if style in ("Regular", "Normal", "") else f"{family} {style}"

    def ass_size(self, px: float) -> int:
        # libass scales a font so that winAscent + winDescent equals the size, so a font with
        # tall metrics (room for Hindi vowel marks) renders smaller than its size number
        upm, ascent, descent = _font_metrics(str(self.path))
        return max(1, int(round(px * (ascent + descent) / upm))) if ascent + descent else int(px)

    def width(self, text: str, px: float) -> float:
        return _pil_font(str(self.path), int(round(px))).getlength(text)


@lru_cache(maxsize=32)
def _pil_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def escape(text: str) -> str:
    """Make user/transcript text safe inside an .ass line."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ").replace("\r", " ")


def clean_word(text: str, uppercase: bool) -> str:
    t = text.strip().strip("\"'“”‘’«»()[]")
    t = re.sub(r"[,.;:।…]+$", "", t)
    t = re.sub(r"^[,.;:।…¿¡]+", "", t)
    if uppercase and not has_devanagari(t):
        t = t.upper()
    return t


@dataclass
class CaptionWord:
    start: float
    end: float
    text: str  # original text, punctuation kept (used to find sentence breaks)
    display: str  # what is shown on screen


def chunk_words(words: list, max_words: int, fits) -> list[list]:
    """Group words into short caption lines. Items need .start/.end/.text;
    `fits(list_of_items)` says if a line is narrow enough."""
    chunks: list[list] = []
    current: list = []
    for w in words:
        if current:
            gap = w.start - current[-1].end
            prev = current[-1].text.strip()
            if (
                len(current) >= max_words
                or gap > 0.6
                or prev.endswith(SENTENCE_END)
                or (prev.endswith(",") and len(current) >= 2)
                or not fits(current + [w])
            ):
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


def _styles(style: StyleConfig, layout: Layout, font: Font, credit_font: Font) -> str:
    base = hex_to_ass(style.caption_color)
    fmt = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding"
    )
    caption = (
        f"Style: Caption,{font.ass_name},{font.ass_size(style.caption_size)},{base},{base},&H00000000,&H99000000,"
        f"0,0,0,0,100,100,0,0,1,{max(2, round(style.caption_size * 0.085))},{max(1, round(style.caption_size * 0.04))},"
        f"2,60,60,{OUT_H - layout.caption_bottom},1"
    )
    hook_pad = max(8, round(style.hook_size * 0.3))
    hook = (
        f"Style: Hook,{font.ass_name},{font.ass_size(style.hook_size)},&H00101010,&H00101010,&H00FFFFFF,&H00FFFFFF,"
        f"0,0,0,0,100,100,0,0,3,{hook_pad},0,8,80,80,{layout.hook_top + hook_pad},1"
    )
    credit = (
        f"Style: Credit,{credit_font.ass_name},{credit_font.ass_size(style.credit_size)},&H33FFFFFF,&H33FFFFFF,"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,40,40,{layout.credit_top},1"
    )
    return f"[V4+ Styles]\n{fmt}\n{caption}\n{hook}\n{credit}\n"


def _hook_text(hook: str, style: StyleConfig, font: Font) -> tuple[str, str]:
    """Returns (ass text, size override tag)."""
    text = strip_emoji(hook).strip()
    if style.hook_uppercase and not has_devanagari(text):
        text = text.upper()
    parts = [escape(p.strip()) for p in text.split("|") if p.strip()]  # '|' = manual line break
    width = max((font.width(p, style.hook_size) for p in parts), default=0)
    lines_needed = sum(max(1, -(-int(font.width(p, style.hook_size)) // HOOK_MAX_WIDTH)) for p in parts)
    override = ""
    if lines_needed > 2 and width:
        px = max(34, style.hook_size * (2 * HOOK_MAX_WIDTH * 0.92) / sum(font.width(p, style.hook_size) for p in parts))
        override = rf"{{\fs{font.ass_size(px)}}}"
    return r"\N".join(parts), override


def build_ass(
    words: list[Word],
    *,
    layout: Layout,
    style: StyleConfig,
    fonts_dir: Path,
    duration: float,
    hook: str | None = None,
    credit: str | None = None,
) -> str:
    """Words must already be relative to the start of the clip."""
    font = Font(fonts_dir / style.font_file)
    credit_font = Font(fonts_dir / style.credit_font_file)
    base = hex_to_ass(style.caption_color)
    highlight = hex_to_ass(style.highlight_color)
    events: list[str] = []

    if hook and hook.strip():
        text, override = _hook_text(hook, style, font)
        if text:
            end = min(duration, style.hook_seconds) if style.hook_seconds > 0 else duration
            events.append(f"Dialogue: 2,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,{override}{text}")

    if credit and credit.strip():
        events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Credit,,0,0,0,,{escape(strip_emoji(credit))}")

    shown: list[CaptionWord] = []
    for w in words:
        display = clean_word(w.text, style.caption_uppercase)
        if display and w.end > 0 and w.start < duration:
            shown.append(CaptionWord(max(0.0, w.start), min(duration, w.end), w.text, display))

    def line_width(chunk: list[CaptionWord], px: float) -> float:
        return font.width(" ".join(w.display for w in chunk), px) * 1.06  # room for the highlighted word's pop

    def fits(chunk: list[CaptionWord]) -> bool:
        return len(chunk) == 1 or line_width(chunk, style.caption_size) <= CAPTION_MAX_WIDTH

    chunks = chunk_words(shown, max(1, style.caption_max_words), fits)
    for ci, chunk in enumerate(chunks):
        chunk_start = chunk[0].start
        chunk_end = chunk[-1].end + 0.4
        if ci + 1 < len(chunks):
            chunk_end = min(chunk_end, chunks[ci + 1][0].start)
        chunk_end = min(chunk_end, duration)
        width = line_width(chunk, style.caption_size)
        size_tag = ""
        if width > CAPTION_MAX_WIDTH:  # a single very long word: shrink it to fit
            size_tag = rf"\fs{font.ass_size(style.caption_size * CAPTION_MAX_WIDTH / width)}"
        for wi, word in enumerate(chunk):
            start = chunk_start if wi == 0 else word.start
            end = chunk[wi + 1].start if wi + 1 < len(chunk) else chunk_end
            if end - start < 0.05:
                continue
            pieces = []
            for xi, other in enumerate(chunk):
                t = escape(other.display)
                pieces.append(rf"{{\c{highlight}\fscx110\fscy110}}{t}{{\c{base}\fscx100\fscy100}}" if xi == wi else t)
            fade = r"\fad(80,0)" if wi == 0 else ""
            prefix = f"{{{fade}{size_tag}}}" if fade or size_tag else ""
            events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{prefix}{' '.join(pieces)}")

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {OUT_W}\nPlayResY: {OUT_H}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
    )
    return (
        header
        + _styles(style, layout, font, credit_font)
        + "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events)
        + "\n"
    )
