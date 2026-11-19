"""Snapping the cut to scene changes and pauses, never in the middle of a word."""

from __future__ import annotations

import numpy as np

from clipper.config import ClipConfig
from clipper.refine import inside_word, refine_window
from clipper.transcribe import Word


def flat_env(duration, loud_at=None):
    t = np.arange(0, duration, 0.1)
    db = np.full_like(t, -40.0)
    if loud_at is not None:
        db[(t >= loud_at) & (t < loud_at + 1.0)] = -6.0
    return t, db


def sentence(start, texts, step=0.4):
    words, t = [], start
    for text in texts:
        words.append(Word(t, t + 0.32, text))
        t += step
    return words


def test_start_moves_to_a_nearby_scene_cut_and_end_to_a_sentence_break():
    words = sentence(9.0, ["okay", "watch", "this."]) + sentence(11.0, ["he", "is", "flying", "away!"]) + sentence(40.5, ["what", "was", "that"])
    t, db = flat_env(60, loud_at=25)
    r = refine_window(10.4, 38.0, words=words, cuts=[9.5, 30.0], env_times=t, env_db=db, seg_duration=60, cfg=ClipConfig())
    assert not inside_word(r.start, words) and not inside_word(r.end, words)
    assert abs(r.start - 9.5) < 0.3 or abs(r.start - 10.8) < 0.3  # scene cut or sentence start
    assert 20 <= r.end - r.start <= 59
    assert 24.5 <= r.payoff <= 26.5


def test_never_ends_mid_word_when_length_is_capped():
    words = [Word(i * 0.5, i * 0.5 + 0.45, "word") for i in range(160)]  # nonstop talking
    t, db = flat_env(80)
    r = refine_window(5.0, 75.0, words=words, cuts=[], env_times=t, env_db=db, seg_duration=80, cfg=ClipConfig())
    assert r.end - r.start <= 59 + 1e-6
    assert not inside_word(r.end, words) and not inside_word(r.start, words)


def test_short_window_is_extended_to_the_minimum():
    t, db = flat_env(40)
    r = refine_window(15.0, 22.0, words=[], cuts=[], env_times=t, env_db=db, seg_duration=40, cfg=ClipConfig())
    assert r.end - r.start >= 20 - 1e-6 and r.end <= 40


def test_wide_spike_is_centred_on_the_loudest_part():
    t, db = flat_env(150, loud_at=100)
    r = refine_window(10.0, 140.0, words=[], cuts=[], env_times=t, env_db=db, seg_duration=150, cfg=ClipConfig(), wide=True)
    assert r.start < 100 < r.end
    assert abs((r.end - r.start) - 30) < 3.5  # target length
    assert any("wider" in n for n in r.notes)


def test_keeps_a_reaction_after_the_payoff():
    t, db = flat_env(60, loud_at=37.5)
    r = refine_window(10.0, 38.0, words=[], cuts=[], env_times=t, env_db=db, seg_duration=60, cfg=ClipConfig())
    assert r.end >= r.payoff + 1.0


def test_your_own_times_are_only_nudged_never_stretched():
    words = [Word(9.7, 10.2, "talking"), Word(14.8, 15.3, "end.")]
    t, db = flat_env(40, loud_at=12)
    r = refine_window(10.0, 15.0, words=words, cuts=[], env_times=t, env_db=db, seg_duration=40, cfg=ClipConfig(), manual=True)
    assert 9.0 <= r.start <= 10.5 and 14.5 <= r.end <= 16.0  # a 5-second clip stays about 5 seconds
    assert not inside_word(r.start, words) and not inside_word(r.end, words)


def test_quiet_opening_is_skipped_but_talking_or_typed_times_are_kept():
    import numpy as np

    from clipper.config import ClipConfig
    from clipper.refine import refine_window, skip_dead_air
    from clipper.transcribe import Word

    times = np.arange(0, 60, 0.1)
    db = np.where(times < 13.0, -45.0, -18.0)  # quiet until 13 s, then the action
    words = [Word(13.6 + i * 0.5, 13.6 + i * 0.5 + 0.4, "go") for i in range(20)]
    s, why = skip_dead_air(10.0, 40.0, words, times, db)
    assert 12.6 <= s <= 13.4 and "quiet" in why
    assert skip_dead_air(10.0, 40.0, [Word(10.4, 10.8, "bro")] + words, times, db)[0] == 10.0  # talking at once
    assert skip_dead_air(10.0, 40.0, words, times, np.full_like(times, -20.0))[0] == 10.0  # not quiet
    cfg = ClipConfig()
    auto = refine_window(10.0, 40.0, words=words, cuts=[], env_times=times, env_db=db, seg_duration=60, cfg=cfg)
    assert auto.start > 12.5 and any("quiet" in n for n in auto.notes)
    typed = refine_window(10.0, 40.0, words=words, cuts=[], env_times=times, env_db=db, seg_duration=60, cfg=cfg,
                          manual=True)
    assert typed.start <= 10.5
