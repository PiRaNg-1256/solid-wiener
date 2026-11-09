"""Captions file, layouts, loudness meter and music mixing."""

from __future__ import annotations

import re
import subprocess

import numpy as np
import pytest

from clipper import media
from clipper.analysis import integrated_lufs
from clipper.audio import _loop, duck_gain, music_start, speech_intervals
from clipper.captions import Font, build_ass, chunk_words, clean_word
from clipper.config import PROJECT_DIR, StyleConfig
from clipper.layout import OUT_H, OUT_W, build_layout
from clipper.transcribe import Word, pick_language

from .conftest import ffmpeg, needs_ffmpeg

FONTS = PROJECT_DIR / "assets" / "fonts"


def events(ass: str, style: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:") and f",{style}," in line]


def test_layouts_fit_the_frame_and_keep_text_out_of_youtube_ui():
    blur = build_layout("blur", 1920, 1080, 1.25)
    x, y, w, h = blur.gameplay
    assert (w, h) == (1080, 760) and y == 520
    assert blur.hook_top + 200 <= y  # two-line hook fits above the gameplay
    assert blur.caption_bottom <= 1560 and blur.credit_top > y + h
    assert build_layout("blur", 1280, 720, 1.0).gameplay[3] == 608
    assert build_layout("crop", 1920, 1080).name == "crop"
    assert build_layout("blur", 1080, 1920).name == "crop"  # vertical source: just fill the screen
    tall = build_layout("blur", 1440, 1080, 1.0)
    assert tall.gameplay[3] <= 1100 and tall.gameplay[1] >= 330


def test_font_size_conversion_matches_libass_metrics():
    font = Font(FONTS / "Poppins-Black.ttf")
    assert font.ass_name == "Poppins Black"
    assert font.ass_size(84) == round(84 * 1762 / 1000)  # Poppins winAscent+winDescent = 1762 units


def test_caption_chunks_respect_words_pauses_and_sentences():
    words = [Word(0.0, 0.3, "no"), Word(0.4, 0.7, "way"), Word(0.8, 1.1, "bro."), Word(1.2, 1.5, "look"),
             Word(2.6, 2.9, "at"), Word(3.0, 3.3, "this")]
    chunks = chunk_words(words, 3, lambda c: True)
    assert [[w.text for w in c] for c in chunks] == [["no", "way", "bro."], ["look"], ["at", "this"]]
    assert clean_word("crazy,", True) == "CRAZY" and clean_word("now!", True) == "NOW!"
    assert clean_word("क्या।", True) == "क्या"


def test_ass_file_highlights_each_word_and_escapes_text():
    style = StyleConfig()
    layout = build_layout("blur", 1920, 1080)
    words = [Word(0.5, 0.8, "they"), Word(0.9, 1.2, "{hacked}"), Word(1.3, 1.7, "it."), Word(3.0, 3.4, "पुलिस"), Word(3.5, 3.9, "आई")]
    ass = build_ass(words, layout=layout, style=style, fonts_dir=FONTS, duration=10.0,
                    hook="GTA 6 cops 💀 | don't play", credit="footage: @creator")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    hook = events(ass, "Hook")
    assert len(hook) == 1 and r"GTA 6 COPS\NDON'T PLAY" in hook[0] and "💀" not in hook[0]
    assert "@creator" in events(ass, "Credit")[0]
    caps = events(ass, "Caption")
    assert len(caps) == 5  # one event per spoken word
    assert all("{" not in re.sub(r"\{\\[^}]*\}", "", c.split(",,", 1)[1]) for c in caps)  # no raw braces left
    assert "(HACKED)" in caps[0] and "&H0000E6FF" in caps[0]
    assert "पुलिस" in caps[3]
    starts = [c.split(",")[1] for c in caps]
    assert starts == sorted(starts)


def test_language_choice_counts_urdu_as_hindi():
    probs = [("ur", 0.45), ("en", 0.30), ("hi", 0.20), ("pa", 0.05)]
    assert pick_language(probs, ["en", "hi"])[0] == "hi"
    assert pick_language([("en", 0.8), ("hi", 0.1)], ["en", "hi"])[0] == "en"
    assert pick_language(probs, ["en"])[0] == "en"


@needs_ffmpeg
def test_loudness_meter_agrees_with_ffmpeg(tmp_path):
    path = tmp_path / "tone.wav"
    ffmpeg("-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.2:r=48000", "-af", "aformat=channel_layouts=stereo", str(path))
    samples = media.decode_audio(path, sr=48000, channels=2)
    ours = integrated_lufs(samples, 48000)
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    theirs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", out)[-1])
    assert abs(ours - theirs) < 0.3


def test_ducking_lowers_music_only_while_talking():
    sr = 1000
    spans = speech_intervals([Word(2.0, 2.4, "a"), Word(2.5, 3.0, "b"), Word(6.0, 6.5, "c")])
    assert spans[0][0] == pytest.approx(1.9) and len(spans) == 2
    gain = duck_gain(10 * sr, sr, spans, -8.0)
    db = 20 * np.log10(gain)
    assert db[int(0.5 * sr)] == pytest.approx(0, abs=0.01)
    assert db[int(2.5 * sr)] == pytest.approx(-8, abs=0.01)
    assert db[int(4.5 * sr)] == pytest.approx(0, abs=0.01)


def test_music_starts_after_a_quiet_intro_and_loops_when_short():
    sr = 1000
    t = np.arange(60 * sr) / sr
    music = np.where(t < 15, 0.02, 0.5) * np.sin(2 * np.pi * 50 * t)
    start = music_start(music.astype(np.float32), sr, 20.0)
    assert 12.0 <= start <= 16.0
    short = np.ones((5 * sr, 2), dtype=np.float32)
    looped = _loop(short, 12 * sr, sr)
    assert looped.shape == (12 * sr, 2) and np.isfinite(looped).all()


@needs_ffmpeg
def test_hindi_and_english_captions_render_with_bundled_fonts(tmp_path):
    """Burn a caption file onto a frame the same way the renderer does (relative paths, fontsdir)."""
    style = StyleConfig()
    layout = build_layout("blur", 1920, 1080)
    words = [Word(0.0, 0.5, "पुलिस"), Word(0.6, 1.0, "आ"), Word(1.1, 1.5, "गई")]
    (tmp_path / "captions.ass").write_text(
        build_ass(words, layout=layout, style=style, fonts_dir=FONTS, duration=2.0, hook="भाई ये क्या हो गया"),
        encoding="utf-8",
    )
    (tmp_path / "fonts").mkdir()
    for f in FONTS.glob("*.ttf"):
        (tmp_path / "fonts" / f.name).write_bytes(f.read_bytes())
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s={OUT_W}x{OUT_H}:d=1",
                    "-vf", "ass=captions.ass:fontsdir=fonts", "-ss", "0.3", "-frames:v", "1", "-update", "1", "frame.png"],
                   cwd=tmp_path, check=True)
    from PIL import Image

    img = np.asarray(Image.open(tmp_path / "frame.png").convert("L"))
    caption_band = img[layout.caption_bottom - 160 : layout.caption_bottom]
    hook_band = img[layout.hook_top : layout.hook_top + 120]
    assert (caption_band > 200).mean() > 0.01  # white/yellow letters drawn
    assert (hook_band > 240).mean() > 0.2  # white hook box drawn


def test_mastering_hits_youtube_loudness_even_with_a_huge_burst():
    from clipper.analysis import master

    sr = 48000
    rng = np.random.default_rng(0)
    t = np.arange(30 * sr) / sr
    x = 0.01 * rng.standard_normal(len(t))  # very quiet background
    burst = (t > 12) & (t < 14)
    x[burst] += 0.9 * np.sin(2 * np.pi * 440 * t[burst])  # one very loud moment
    out = master(np.stack([x, x], 1).astype(np.float32), sr, -14.0, -1.0)
    assert abs(integrated_lufs(out, sr) - (-14.0)) <= 0.5
    assert np.abs(out).max() <= 10 ** (-1.5 / 20) * 1.001
    silent = master(np.zeros((sr * 5, 2), np.float32), sr)
    assert not silent.any()
