"""Turn a rough window into a clean cut: start/end on scene cuts or pauses, never mid-word."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .analysis import loudest_time
from .config import ClipConfig
from .transcribe import Word

SENTENCE_END = (".", "?", "!", "।", "…")


@dataclass
class Refined:
    start: float
    end: float
    payoff: float  # most intense moment in the clip (used for the thumbnail)
    notes: list[str] = field(default_factory=list)


def inside_word(t: float, words: list[Word], margin: float = 0.04) -> Word | None:
    for w in words:
        if w.start + margin < t < w.end - margin:
            return w
    return None


def _pauses(words: list[Word]) -> list[tuple[float, float, bool]]:
    """(end of word, start of next word, is a sentence break) for every pause >= 0.25 s."""
    out = []
    for a, b in zip(words, words[1:]):
        gap = b.start - a.end
        if gap >= 0.25:
            out.append((a.end, b.start, a.text.strip().endswith(SENTENCE_END) or gap >= 0.7))
    return out


def _snap_start(s: float, words: list[Word], cuts: list[float], before: float = 2.5, after: float = 1.5) -> tuple[float, str]:
    lo, hi = s - before, s + after
    options: list[tuple[float, float, str]] = []  # (score, time, reason) lower score wins
    if not inside_word(s, words):
        options.append((0.0, s, "as planned"))
    for c in cuts:
        if lo <= c <= hi and not inside_word(c, words):
            options.append((abs(c - s) - 1.0, c, "on a scene cut"))
    for a_end, b_start, sentence in _pauses(words):
        t = max(a_end, b_start - 0.2)
        if lo <= t <= hi:
            options.append((abs(t - s) - (0.8 if sentence else 0.3), t, "at the start of a sentence" if sentence else "in a pause"))
    if words and lo <= words[0].start - 0.2 <= hi:
        t = words[0].start - 0.2
        options.append((abs(t - s) - 0.5, t, "just before the talking starts"))
    if options:
        _, t, why = min(options)
        return t, why
    w = inside_word(s, words)
    return ((w.start - 0.1), "moved to the start of a word") if w else (s, "as planned")


def _snap_end(e: float, words: list[Word], cuts: list[float], before: float = 1.5, after: float = 3.0) -> tuple[float, str]:
    lo, hi = e - before, e + after
    options: list[tuple[float, float, str]] = []
    if not inside_word(e, words):
        options.append((0.0, e, "as planned"))
    for a_end, b_start, sentence in _pauses(words):
        t = min(a_end + 0.25, b_start - 0.05)
        if lo <= t <= hi:
            options.append((abs(t - e) - (1.0 if sentence else 0.3), t, "at the end of a sentence" if sentence else "in a pause"))
    if words and lo <= words[-1].end + 0.3 <= hi:
        t = words[-1].end + 0.3
        options.append((abs(t - e) - 0.8, t, "after the last words"))
    for c in cuts:
        t = c - 0.05  # end just before the cut so the next shot doesn't flash
        if lo <= t <= hi and not inside_word(t, words):
            options.append((abs(t - e) - 0.6, t, "on a scene cut"))
    if options:
        _, t, why = min(options)
        return t, why
    w = inside_word(e, words)
    return ((w.end + 0.15), "moved to the end of a word") if w else (e, "as planned")


def skip_dead_air(s: float, e: float, words: list[Word], env_times: np.ndarray, env_db: np.ndarray,
                  look: float = 1.5, most: float = 3.0) -> tuple[float, str]:
    """Shorts are swiped away in the first second: if the clip opens on a quiet stretch with nobody talking,
    start just before the first words or the first burst of sound (at most `most` seconds later)."""
    if len(env_times) == 0 or e - s < 2 * look:
        return s, ""
    if any(s - 0.05 <= w.start < s + look for w in words):
        return s, ""
    inside = (env_times >= s) & (env_times <= e)
    if inside.sum() < 5:
        return s, ""
    busy = float(np.percentile(env_db[inside], 75))
    opening = (env_times >= s) & (env_times < s + look)
    if not opening.any() or float(env_db[opening].mean()) > busy - 8.0:
        return s, ""  # the opening isn't quiet compared with the rest of the clip
    events = [w.start - 0.25 for w in words if s < w.start <= s + most + 0.25]
    loud = np.flatnonzero((env_times > s) & (env_times <= s + most) & (env_db >= busy - 4.0))
    if len(loud):
        events.append(float(env_times[loud[0]]) - 0.3)
    target = min([t for t in events if t > s + 0.3] or [s + most])
    target = min(target, s + most)
    if inside_word(target, words):
        return s, ""
    return round(target, 3), f"skipped {target - s:.1f}s of quiet at the start"


def refine_window(
    start: float,
    end: float,
    *,
    words: list[Word],
    cuts: list[float],
    env_times: np.ndarray,
    env_db: np.ndarray,
    seg_duration: float,
    cfg: ClipConfig,
    wide: bool = False,
    manual: bool = False,
) -> Refined:
    """All times are seconds inside the downloaded segment. manual=True (times you typed): only nudge the cut by
    about a second so no word is cut in half; never stretch or trim to the usual lengths."""
    notes: list[str] = []
    start, end = max(0.0, start), min(seg_duration, end)
    if wide:
        length = min(cfg.target_seconds, cfg.max_seconds)
        focus = loudest_time(env_times, env_db, start, end, smooth=5.0)
        new_start = min(max(start, focus - 0.6 * length), max(start, end - length))
        start, end = new_start, min(seg_duration, new_start + length)
        notes.append(f"replay spike was wider than a Short: centred on the most intense part ({focus:.1f}s)")

    payoff = loudest_time(env_times, env_db, start, end, smooth=1.0)
    if manual:
        s, why_s = _snap_start(start, words, cuts, before=1.0, after=0.5)
        e, why_e = _snap_end(end, words, cuts, before=0.5, after=1.0)
        s, e = max(0.0, s), min(seg_duration, e)
        notes.append(f"your times; start {why_s}, end {why_e}")
        return Refined(start=round(s, 3), end=round(e, 3), payoff=round(min(max(payoff, s), e), 3), notes=notes)
    s, why_s = _snap_start(start, words, cuts)
    e, why_e = _snap_end(end, words, cuts)
    notes.append(f"start {why_s}, end {why_e}")
    s, e = max(0.0, s), min(seg_duration, e)
    s2, why = skip_dead_air(s, e, words, env_times, env_db)
    if why and e - s2 >= cfg.min_seconds - 3:  # keep it from shrinking far below the minimum length
        s = s2
        notes.append(why)

    # keep at least a second of reaction after the most intense moment
    if payoff > e - 1.0:
        e = min(seg_duration, payoff + 1.5, s + cfg.max_seconds)

    if e - s > cfg.max_seconds:
        e = s + cfg.max_seconds
        w = inside_word(e, words)
        if w:
            e = w.start - 0.05
        notes.append("trimmed to the maximum length")
    if e - s < cfg.min_seconds:
        e = min(seg_duration, s + cfg.min_seconds)
        if e - s < cfg.min_seconds:
            s = max(0.0, e - cfg.min_seconds)
        w = inside_word(e, words)
        if w and w.end + 0.15 - s <= cfg.max_seconds:
            e = min(seg_duration, w.end + 0.15)
        notes.append("extended to the minimum length")

    payoff = min(max(payoff, s), e)
    return Refined(start=round(s, 3), end=round(e, 3), payoff=round(payoff, 3), notes=notes)
