"""Put better words (from Gemini) onto the local model's word timings.

Gemini hears words more accurately; the local model knows to a tenth of a second when each word is said.
Matching works on simplified English-letter spellings, so Devanagari, Hinglish and English all compare."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .hinglish import to_latin
from .transcribe import Word

MIN_MATCHED = 0.3  # below this share of matched words the two transcripts disagree too much to trust Gemini


def key(text: str) -> str:
    """'Bhaago!' / 'भागो' / 'bhago' -> 'bhago': lowercase letters and digits, no doubled letters."""
    simple = re.sub(r"[^a-z0-9]", "", to_latin(text).lower())
    return re.sub(r"(.)\1+", r"\1", simple)


def _similar(a: str, b: str) -> bool:
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.6


def align(new_words: list[str], keys: list[str], timed: list[Word]) -> list[Word] | None:
    """new_words: the words to show; keys: their matching spellings; timed: local words with timings.
    Returns the new words with timings, or None if the transcripts don't agree enough."""
    if not new_words or not timed:
        return None
    timed_keys = [key(w.text) for w in timed]
    times: list[tuple[float, float] | None] = [None] * len(new_words)
    matched = 0
    ops = SequenceMatcher(None, keys, timed_keys, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                times[i] = (timed[j].start, timed[j].end)
            matched += i2 - i1
        elif tag == "replace":
            if i2 - i1 == j2 - j1:  # same number of words: a one-for-one correction
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    times[i] = (timed[j].start, timed[j].end)
                    matched += _similar(keys[i], timed_keys[j])
            else:  # give each new word the timing of its most similar local word, in order
                j_from = j1
                for i in range(i1, i2):
                    scores = [(SequenceMatcher(None, keys[i], timed_keys[j]).ratio(), j) for j in range(j_from, j2)]
                    best, best_j = max(scores, default=(0.0, None))
                    if best_j is not None and best >= 0.5:
                        times[i] = (timed[best_j].start, timed[best_j].end)
                        j_from = best_j + 1
                        matched += 1
                # words left without a timing are spread over the gaps below
        # "insert" (words only Gemini heard) is filled in below; "delete" (only the local model heard) is dropped
    if matched / len(new_words) < MIN_MATCHED:
        return None
    _fill_gaps(times)
    out = []
    for text, (start, end) in zip(new_words, times):
        out.append(Word(start, max(end, start + 0.05), text))
    for a, b in zip(out, out[1:]):  # keep the order strict
        if b.start < a.start:
            b.start = a.start
        if a.end > b.start:
            a.end = max(a.start + 0.05, b.start)
    return out


def _fill_gaps(times: list) -> None:
    """Words without a timing share the gap between their timed neighbours."""
    n = len(times)
    i = 0
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        before = times[i - 1][1] if i > 0 else (times[j][0] - 0.3 * (j - i) if j < n else 0.0)
        after = times[j][0] if j < n else before + 0.3 * (j - i)
        before = max(0.0, before)
        if after - before < 0.12 * (j - i):  # no room: squeeze them in around the boundary
            mid = (before + after) / 2
            before, after = max(0.0, mid - 0.06 * (j - i)), max(0.0, mid - 0.06 * (j - i)) + 0.12 * (j - i)
        step = (after - before) / (j - i)
        for k in range(i, j):
            times[k] = (before + step * (k - i), before + step * (k - i + 1))
        i = j
