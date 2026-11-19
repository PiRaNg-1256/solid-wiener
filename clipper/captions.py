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


@dataclass
class CaptionLine:
    """One caption on screen: 1-3 words shown together, each word highlighted while it's spoken."""

    start: float
    end: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def make_lines(words: list[Word], style: StyleConfig, fonts_dir: Path, duration: float) -> list[CaptionLine]:
    """Group timed words into caption lines (text kept in normal case; the renderer uppercases)."""
    font = Font(fonts_dir / style.font_file)
    shown: list[CaptionWord] = []
    for w in words:
        display = clean_word(w.text, uppercase=False)
        if display and w.end > 0 and w.start < duration:
            shown.append(CaptionWord(max(0.0, w.start), min(duration, w.end), w.text, display))

    def fits(chunk: list[CaptionWord]) -> bool:
        text = " ".join(clean_word(w.display, style.caption_uppercase) for w in chunk)
        return len(chunk) == 1 or font.width(text, style.caption_size) * 1.06 <= CAPTION_MAX_WIDTH

    chunks = chunk_words(shown, max(1, style.caption_max_words), fits)
    lines = []
    for ci, chunk in enumerate(chunks):
        end = chunk[-1].end + 0.4
        if ci + 1 < len(chunks):
            end = min(end, chunks[ci + 1][0].start)
        end = max(min(end, duration), chunk[-1].end)
        lines.append(CaptionLine(chunk[0].start, end, [Word(w.start, w.end, w.display) for w in chunk]))
    return lines


# ---------- editable captions file (.srt) ----------

def _srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    sec, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def write_srt(lines: list[CaptionLine], path: Path) -> None:
    blocks = [f"{i}\n{_srt_time(line.start)} --> {_srt_time(line.end)}\n{line.text}\n" for i, line in enumerate(lines, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


_SRT_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def _parse_srt_time(text: str) -> float:
    m = _SRT_TIME.fullmatch(text.strip())
    if not m:
        raise ValueError(text)
    h, mi, sec, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000


def read_srt(path: Path) -> list[tuple[float, float, str]]:
    """(start, end, text) for every caption; tolerant of Notepad quirks (BOM, Windows line endings, no numbers)."""
    from .util import ClipperError

    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    entries = []
    for number, block in enumerate(re.split(r"\n\s*\n", text.strip()), 1):
        rows = [r for r in block.split("\n") if r.strip()]
        if rows and rows[0].strip().isdigit():
            rows = rows[1:]
        if not rows:
            continue
        if "-->" not in rows[0]:
            raise ClipperError(f"{path.name}, caption {number}: the first line must be like 00:00:01,200 --> 00:00:02,500")
        left, right = rows[0].split("-->", 1)
        try:
            start, end = _parse_srt_time(left), _parse_srt_time(right)
        except ValueError:
            raise ClipperError(f"{path.name}, caption {number}: can't read the time '{rows[0].strip()}'") from None
        if end <= start:
            raise ClipperError(f"{path.name}, caption {number}: the end time must be after the start time")
        words = " ".join(r.strip() for r in rows[1:]).strip()
        if words:
            entries.append((start, end, words))
    return sorted(entries)


def lines_from_srt(entries: list[tuple[float, float, str]], previous: list[CaptionLine]) -> list[CaptionLine]:
    """Turn edited captions back into timed words. A line with the same number of words as the original line
    it overlaps keeps each word's timing (stretched if you moved the line); otherwise words share the time."""
    lines = []
    same_count = len(entries) == len(previous)  # no lines added or removed: match each line to its original
    for index, (start, end, text) in enumerate(entries):
        tokens = text.split()
        if same_count:
            best = previous[index]
        else:  # lines were added or removed: match by time instead
            best, best_overlap = None, 0.0
            for old in previous:
                overlap = min(end, old.end) - max(start, old.start)
                if overlap > best_overlap:
                    best, best_overlap = old, overlap
            if best_overlap < 0.3 * (end - start):
                best = None
        words: list[Word] = []
        if best is not None and len(best.words) == len(tokens) and best.end > best.start:
            scale = (end - start) / (best.end - best.start)
            for w, token in zip(best.words, tokens):
                a = start + (w.start - best.start) * scale
                b = start + (w.end - best.start) * scale
                words.append(Word(min(max(a, start), end), min(max(b, start), end), token))
        else:
            weights = [len(t) + 2 for t in tokens]
            total, t = sum(weights), start
            for token, weight in zip(tokens, weights):
                span = (end - start) * weight / total
                words.append(Word(t, t + span, token))
                t += span
        lines.append(CaptionLine(start, end, words))
    return lines


def build_ass(
    captions: list,
    *,
    layout: Layout,
    style: StyleConfig,
    fonts_dir: Path,
    duration: float,
    hook: str | None = None,
    credit: str | None = None,
) -> str:
    """captions: caption lines, or plain timed words (grouped into lines here). Times are relative to the clip."""
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

    lines = captions if captions and isinstance(captions[0], CaptionLine) else make_lines(captions, style, fonts_dir, duration)
    for line in lines:
        if line.start >= duration or not line.words:
            continue
        shown = [clean_word(w.text, style.caption_uppercase) or w.text for w in line.words]
        width = font.width(" ".join(shown), style.caption_size) * 1.06
        size_tag = ""
        if width > CAPTION_MAX_WIDTH and len(shown) == 1:  # a single very long word: shrink it to fit
            size_tag = rf"\fs{font.ass_size(style.caption_size * CAPTION_MAX_WIDTH / width)}"
        line_end = min(line.end, duration)
        for wi, word in enumerate(line.words):
            start = line.start if wi == 0 else max(line.start, word.start)
            end = line.words[wi + 1].start if wi + 1 < len(line.words) else line_end
            end = min(end, line_end)
            if end - start < 0.05:
                continue
            pieces = []
            for xi, text in enumerate(shown):
                t = escape(text)
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
