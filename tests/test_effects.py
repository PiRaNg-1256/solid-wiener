"""Effects: zoom cuts on the big moments, caption/hook animation, progress bar, watermark, sounds, presets."""

from __future__ import annotations

import numpy as np
import pytest

from clipper.captions import build_ass
from clipper.config import Config, EffectsConfig, apply_effects_preset
from clipper.layout import build_layout
from clipper.refine import pick_punches
from clipper.transcribe import Word
from clipper.util import ClipperError


def _ass(cfg, effects=None, **style):
    for k, v in style.items():
        setattr(cfg.style, k, v)
    words = [Word(0.5, 0.9, "the"), Word(1.0, 1.4, "cops")]
    return build_ass(words, layout=build_layout("blur", 1920, 1080), style=cfg.style, fonts_dir=cfg.fonts_dir,
                     duration=10, hook="WAIT FOR IT", effects=effects)


def test_caption_and_hook_animation_and_extras(cfg):
    on = _ass(cfg, EffectsConfig(progress_bar=True), watermark="@MyChannel")
    assert r"\t(0,70,\fscx118\fscy118)" in on  # the spoken word pops
    assert r"\fscx60\fscy60\t(0,130" in on  # the hook pops in
    assert r"\fscx0\t(0,10000,\fscx100)" in on and "Style: Bar," in on  # progress bar fills over 10 s
    assert "@MyChannel" in on and r"\an1\pos(26," in on  # watermark bottom-left of the gameplay
    off = _ass(cfg, EffectsConfig(word_pop=False, hook_pop=False), watermark="")
    assert r"\t(0,70" not in off and r"\fscx60" not in off and r"\fscx110\fscy110" in off
    assert r"\p1" not in off and "@MyChannel" not in off


def test_zoom_cuts_land_on_the_big_moments():
    times = np.arange(0, 40, 0.1)
    db = np.full_like(times, -30.0)
    db[(times > 20) & (times < 20.6)] = -8.0  # the payoff
    db[(times > 12) & (times < 12.5)] = -10.0  # another loud moment
    db[(times > 21.5) & (times < 22.0)] = -9.0  # loud, but too close to the payoff
    db[(times > 10.1) & (times < 10.5)] = -5.0  # loudest of all, but in the first 0.8 s of the clip
    found = pick_punches(times, db, 10.0, 30.0, payoff=20.2, most=3)
    starts = [round(a + 0.04, 1) for a, _ in found]
    assert starts[0] == pytest.approx(10.2) and 2.0 <= starts[1] <= 2.6  # payoff first, then 12 s (clip time 2)
    assert len(found) == 2 and all(b - a == pytest.approx(0.64) for a, b in found)
    assert pick_punches(times, db, 10.0, 30.0, payoff=20.2, most=0) == []


def test_zoom_cut_filter_only_touches_the_gameplay():
    blur = build_layout("blur", 1920, 1080, punches=[(2.0, 2.6), (8.0, 8.6)]).filter
    assert "enable='between(t,2.000,2.600)+between(t,8.000,8.600)'" in blur
    assert blur.index("[clipfgsz]") > blur.index("[clipfg]")  # zoomed copy made from the gameplay, not the blur
    cam = build_layout("blur", 1920, 1080, facecam=(1500, 760, 400, 300), punches=[(2.0, 2.6)]).filter
    assert "[clipbotsz]" in cam and "[cliptop]" in cam.split("[clipbotsz]")[0]  # webcam half isn't zoomed
    crop = build_layout("crop", 1920, 1080, punches=[(2.0, 2.6)]).filter
    assert crop.endswith("[clipco]null")  # still a chain the subtitles can follow
    assert "between(t" not in build_layout("blur", 1920, 1080).filter


def test_presets_and_punch_choice():
    from clipper.pipeline import punches_for

    cfg = Config()
    job = {"punches": [[10.2, 10.8], [2.0, 2.6], [15.0, 15.6]], "payoff": 10.24}
    assert punches_for(job, cfg) == [(2.0, 2.6), (10.2, 10.8)]  # the two most important, in time order
    apply_effects_preset(cfg, "none")
    assert punches_for(job, cfg) == [] and not cfg.effects.word_pop and not cfg.effects.shadow
    apply_effects_preset(cfg, "all")
    assert cfg.effects.progress_bar and cfg.effects.sounds and cfg.effects.zoom_punch
    with pytest.raises(ClipperError, match="Effects"):
        apply_effects_preset(cfg, "max")


def test_sound_effects_are_placed_and_never_clip():
    from clipper.audio import add_effects, sound_effect

    sr = 48000
    for kind in ("whoosh", "hit"):
        s = sound_effect(kind, sr)
        assert 0.1 < np.abs(s).max() <= 0.6 and len(s) < sr
    out = add_effects(np.zeros((sr * 3, 2), np.float32), sr, [("hit", 1.5)])
    loud = np.flatnonzero(np.abs(out[:, 0]) > 0.05)
    assert abs(loud[0] / sr - 1.5) < 0.01


def test_sheet_columns(cfg):
    from clipper import sheet

    row = sheet.parse_row(2, {"link": "x", "effects": "None", "swears": "Mask + beep"}, cfg)
    assert (row.effects, row.profanity, row.bleep) == ("none", "mask", True)
    assert row.render_overrides()["bleep"] == "all"
    assert sheet.parse_row(2, {"link": "x", "swears": "show"}, cfg).profanity == "show"
    for bad, col in (({"effects": "max"}, "Effects"), ({"swears": "hide"}, "Swear words")):
        with pytest.raises(ClipperError, match=col):
            sheet.parse_row(2, {"link": "x", **bad}, cfg)
