"""Swear words: transcribed as said, shown as f**k, kept out of titles, optional beep."""

from __future__ import annotations

import json

import numpy as np
import pytest

from clipper import profanity as P
from clipper.config import CaptionConfig, Config, validate
from clipper.transcribe import Word
from clipper.util import ClipperError

from .conftest import make_video


@pytest.mark.parametrize("word, kind", [
    ("fuck", "strong"), ("Fuuuck!", "strong"), ("FUCKING", "strong"), ("motherfucker", "strong"),
    ("fuckin'", "strong"), ("bullshit", "strong"), ("Bitch", "strong"), ("asshole", "strong"),
    ("bhenchod", "strong"), ("madarchod", "strong"), ("chutiya", "strong"), ("bhosdike", "strong"),
    ("gaandu", "strong"), ("भेनचोद", "strong"), ("चूतिया", "strong"), ("मादरचोद,", "strong"),
    ("damn", "mild"), ("hell", "mild"), ("saala", "mild"), ("साला", "mild"), ("ass", "mild"),
    ("nigga", "slur"), ("retarded", "slur"),
    ("f***", "censored"), ("sh*t", "censored"), ("__", "censored"), ("[expletive]", "censored"),
])
def test_swears_are_recognised(word, kind):
    assert P.classify(word) == kind


@pytest.mark.parametrize("word", [
    "as", "pass", "class", "assassin", "hello", "shell", "shoot", "fork", "fake", "folk", "duck", "cocktail",
    "peacock", "dickens", "ganda", "gandi", "Gandhi", "chhut", "bich", "बीच", "land", "sale", "Niger", "shitake",
    "freaking", "BC", "[", "]", "bhai", "kya", "yaar",
])
def test_normal_words_are_left_alone(word):
    assert P.classify(word) is None
    assert P.mask_word(word, "strict") == word


def test_mask_keeps_the_first_and_last_letter():
    assert P.mask_word("fuck") == "f**k"
    assert P.mask_word("Fucking!") == "F*****g!"
    assert P.mask_word('"bhenchod,"') == '"b******d,"'
    assert P.mask_word("भेनचोद") == "भे**द"  # Hindi letters keep their vowel signs
    assert P.mask_text("bhai ye kya bhenchod, what the fuck!") == "bhai ye kya b******d, what the f**k!"


def test_modes():
    line = "damn bro what the fuck nigga"
    assert P.mask_text(line, "mask") == "damn bro what the f**k n***a"
    assert P.mask_text(line, "strict") == "d**n bro what the f**k n***a"
    assert P.mask_text(line, "show") == "damn bro what the fuck n***a"  # slurs are always hidden
    assert P.mask_word("__") == "****" and P.mask_word("f***") == "f***"


def test_censored_or_softened_words_are_put_back_from_the_local_model():
    local = [Word(1.0, 1.3, "what"), Word(1.3, 1.5, "the"), Word(1.5, 1.9, "fucking"), Word(2.0, 2.4, "shoot"),
             Word(2.5, 2.9, "भेनचोद")]
    better = [Word(1.0, 1.3, "what"), Word(1.3, 1.5, "the"), Word(1.5, 1.9, "f***"), Word(2.0, 2.4, "shoot"),
              Word(2.5, 2.9, "b***")]
    assert P.restore(better, local, to_text=lambda t: "bhenchod" if t == "भेनचोद" else t) == 2
    assert [w.text for w in better] == ["what", "the", "fucking", "shoot", "bhenchod"]
    # a softer word is replaced only by a swear with the same first letter said at the same time
    softened = [Word(1.5, 1.9, "freaking"), Word(2.0, 2.4, "darn")]
    P.restore(softened, [Word(1.5, 1.9, "fucking"), Word(2.0, 2.4, "shit")])
    assert [w.text for w in softened] == ["fucking", "darn"]


def test_bleep_replaces_the_sound_with_a_tone():
    from clipper.audio import bleep

    sr = 48000
    t = np.arange(sr * 2) / sr
    game = np.stack([0.3 * np.sin(2 * np.pi * 220 * t)] * 2, axis=1).astype(np.float32)
    out = bleep(game, sr, [(0.5, 1.0)])
    inside, outside = out[int(0.6 * sr):int(0.9 * sr), 0], out[int(1.2 * sr):int(1.5 * sr), 0]

    def peak_hz(x):
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        return np.fft.rfftfreq(len(x), 1 / sr)[np.argmax(spec)]

    assert abs(peak_hz(inside) - 1000) < 5 and abs(peak_hz(outside) - 220) < 5
    spans = P.bleep_spans([Word(0.5, 0.9, "fuck"), Word(1.0, 1.2, "damn"), Word(1.25, 1.5, "shit")], "mask")
    assert np.allclose(spans, [(0.46, 0.94), (1.21, 1.54)])  # mild "damn" isn't beeped


def test_settings_are_checked():
    cfg = Config()
    cfg.captions.bleep = "False"  # what YAML makes of a bare `bleep: off`
    validate(cfg)
    assert cfg.captions.bleep == "off"
    cfg.captions.profanity = "Strict"
    validate(cfg)
    assert cfg.captions.profanity == "strict"
    cfg.captions.profanity = "hide"
    with pytest.raises(ClipperError, match="profanity"):
        validate(cfg)


def test_speech_model_gets_the_style_line_and_hint_words(monkeypatch):
    from clipper import transcribe as T

    seen = {}

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return iter([]), None

    monkeypatch.setattr(T, "has_speech", lambda audio: True)
    monkeypatch.setattr(T, "choose_engine", lambda cfg, cache: T.Engine("cpu", "int8", "small"))
    monkeypatch.setattr(T, "_load_model", lambda engine: FakeModel())
    T.transcribe(np.zeros(16000, np.float32), CaptionConfig(vocabulary=["Mortal"]), language="en",
                 vocabulary=["Vice City", "Lucia"])
    assert "fuck" in seen["initial_prompt"]
    assert seen["hotwords"].startswith("Mortal, Vice City, Lucia") and "motherfucker" in seen["hotwords"]
    T.transcribe(np.zeros(16000, np.float32), CaptionConfig(), language="hi")
    assert "भेनचोद" in seen["hotwords"]
    T.transcribe(np.zeros(16000, np.float32), CaptionConfig(translate_to_english=True), language="hi")
    assert seen["initial_prompt"] is None and seen["hotwords"] is None  # translating: no hints in Hindi


def test_titles_and_descriptions_never_carry_swears():
    from clipper.metadata import VideoInfo
    from clipper.package import description, title_options

    titles = title_options("WHAT THE FUCK", None, ["{hook} #shorts", "Wait for it… #shorts"], ["#gaming"])
    assert titles == ["Wait for it… #shorts"]
    video = VideoInfo(id="x", url="u", title="t", channel="c", handle="@c", duration=60.0)
    assert "f**k" in description(None, video, 0, None, [], False, summary="what the fuck happened")


def test_whole_clip(cfg, tmp_path, monkeypatch):
    """Gemini censors the swear and offers a swearing title: captions get the real word, shown masked."""
    from clipper import gemini
    from clipper.pipeline import MakeOptions, make
    from clipper.transcribe import Transcript

    said = ["bro", "what", "the", "fucking", "hell"]

    def fake_local(audio, c, language=None, **kwargs):
        total, words, t, i = len(audio) / 16000, [], 1.0, 0
        while t + 0.5 < total:
            words.append(Word(t, t + 0.4, said[i % len(said)], 0.9))
            t, i = t + 0.5, i + 1
        return Transcript(language="en", probability=0.9, words=words, engine="small on CPU (int8)")

    def fake_gemini(clip_audio, gcfg, sr=16000, **kwargs):
        n = int(len(clip_audio) / 16000 / 0.5) + 2
        heard = [("bro", "bro"), ("what", "what"), ("the", "the"), ("f***ing", "f***ing"), ("hell", "hell")]
        return gemini.GeminiWords("en", [heard[i % 5] for i in range(n)], "gemini-flash-latest",
                                  hook="HOLY SHIT LOOK", titles=["This is fucking crazy", "He did not see it coming"],
                                  description="What the fuck just happened", hashtags=["#fuckthis", "#cops"])

    monkeypatch.setattr("clipper.pipeline.transcribe", fake_local)
    monkeypatch.setattr("clipper.pipeline.gemini.transcribe_clip", fake_gemini)
    monkeypatch.setattr(gemini, "_PAUSED_UNTIL", 0.0)
    cfg.captions.gemini.api_key = "test-key"
    cfg.captions.bleep = "all"
    cfg.render.keep_work_files = True
    src = make_video(tmp_path / "talk.mp4", duration=50, burst_at=25.0)
    folder = make(str(src), MakeOptions(start=15, end=37, music="none", prompt=False), cfg)[0]

    srt = (folder / "captions.srt").read_text(encoding="utf-8")
    assert "fucking" in srt and "f***" not in srt  # put back as said
    ass = (next((cfg.root / "cache" / "work").iterdir()) / "clip.ass").read_text(encoding="utf-8")
    assert "F*****G" in ass and "FUCKING" not in ass and "HELL" in ass  # masked on screen; mild words shown
    hook = (folder / "hook.txt").read_text(encoding="utf-8")
    assert "SHIT" not in hook.upper()  # Gemini's swearing hook was skipped
    post = (folder / "post.txt").read_text(encoding="utf-8")
    assert "fucking crazy" not in post and "He did not see it coming" in post and "#fuckthis" not in post
    assert "What the f**k just happened" in post
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert "put back as said" in meta["captions"]["gemini_note"]


def test_made_up_lines_are_dropped_but_real_speech_stays(monkeypatch):
    from types import SimpleNamespace as NS

    from clipper import transcribe as T

    def seg(text, logprob=-0.2, no_speech=0.01, ratio=1.3):
        words = [NS(word=" " + w, start=i * 0.3, end=i * 0.3 + 0.25, probability=0.9) for i, w in enumerate(text.split())]
        return NS(text=text, words=words, avg_logprob=logprob, no_speech_prob=no_speech, compression_ratio=ratio)

    segments = [
        seg("No way bro, look at this."),
        seg("Thanks for watching!", logprob=-0.6),  # over the outro music, model unsure
        seg("Subtitles by the Amara.org community"),  # never real
        seg("go go go go go go go go go go go go go go go", ratio=3.1),  # stuck in a loop
        seg("Please subscribe to the channel", logprob=-0.1, no_speech=0.01),  # the streamer really said it
    ]

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            return iter(segments), None

    monkeypatch.setattr(T, "has_speech", lambda audio: True)
    monkeypatch.setattr(T, "choose_engine", lambda cfg, cache: T.Engine("cpu", "int8", "small"))
    monkeypatch.setattr(T, "_load_model", lambda engine: FakeModel())
    result = T.transcribe(np.zeros(16000, np.float32), CaptionConfig(), language="en")
    text = " ".join(w.text for w in result.words)
    assert result.removed == 3
    assert text == "No way bro, look at this. Please subscribe to the channel"


def test_unreadable_swears_show_as_stars_in_the_captions_file(cfg):
    from clipper.captions import make_lines

    words = [Word(0.0, 0.3, "what"), Word(0.3, 0.5, "the"), Word(0.5, 0.6, "["), Word(0.6, 0.8, "__"),
             Word(0.8, 0.9, "]")]
    lines = make_lines(words, cfg.style, cfg.fonts_dir, 2.0)
    assert [w.text for ln in lines for w in ln.words] == ["what", "the", "****"]
