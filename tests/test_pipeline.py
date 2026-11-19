"""End to end on synthetic videos: download/cut -> moment -> captions -> music -> render -> output folder.

Whisper is replaced by fake words (the real model needs a download), and YouTube by a local web server
that yt-dlp downloads from with the same partial-download code path."""

from __future__ import annotations

import json
import shutil

import numpy as np

from clipper import media
from clipper.analysis import envelope_db, integrated_lufs
from clipper.metadata import VideoInfo
from clipper.pipeline import MakeOptions, make

from .conftest import make_music, make_video, needs_ffmpeg

pytestmark = needs_ffmpeg

BURST = 70.0  # loud 2-second moment in the synthetic video
SCENES = 15.0  # a new scene every 15 s


def audio_peak(path) -> float:
    mono = media.decode_audio(path, sr=16000)
    t, db = envelope_db(mono, 16000, hop=0.02, window=0.05)
    return float(t[np.argmax(db > db.max() - 6)])  # onset of the loudest part


def scene_changes(path) -> list[float]:
    return media.scene_cuts(path, threshold=8.0)


def check_clip(folder, min_len=20.0, max_len=59.0):
    clip = folder / "clip.mp4"
    info = media.probe(clip)
    assert (info.width, info.height) == (1080, 1920)
    assert min_len - 0.2 <= info.duration <= max_len + 0.2
    assert info.has_audio
    lufs = integrated_lufs(media.decode_audio(clip, sr=48000, channels=2), 48000)
    assert abs(lufs - (-14.0)) <= 1.0, lufs
    assert (folder / "thumb.jpg").exists() and (folder / "post.txt").exists()
    return json.loads((folder / "meta.json").read_text(encoding="utf-8"))


def test_youtube_style_run_with_heatmap(cfg, fake_transcribe, http_server, monkeypatch):
    srv_root, base_url = http_server
    make_video(srv_root / "video.mp4", duration=120, burst_at=BURST, scene=SCENES)
    (cfg.root / "music").mkdir()
    make_music(cfg.root / "music" / "hype.m4a")
    (cfg.root / "music" / "hype.txt").write_text("Hype Track - Test Artist [NCS]", encoding="utf-8")

    # 100-point graph for a 120 s video with a replay spike at the loud moment
    values = 0.2 + 0.02 * np.sin(np.arange(100))
    values[int(BURST / 1.2)] = 1.0
    values[int(BURST / 1.2) + 1] = 0.7
    heat = [{"start_time": i * 1.2, "end_time": (i + 1) * 1.2, "value": float(v)} for i, v in enumerate(values)]
    url = f"{base_url}/video.mp4"
    video = VideoInfo(id="testvideo01", url=url, title="GTA 6 police chase gone wrong", channel="Test Channel",
                      handle="@testcreator", duration=120.0, heatmap=heat)
    monkeypatch.setattr("clipper.pipeline.fetch_info", lambda u, c, refresh=False: video)
    monkeypatch.setattr("clipper.pipeline.sponsorblock.fetch_segments", lambda vid: ([], "test"))

    folders = make(url, MakeOptions(hook="GTA 6 COPS | DON'T PLAY", prompt=False), cfg)
    assert len(folders) == 1
    folder = folders[0]
    meta = check_clip(folder)
    assert meta["moment"]["source"] == "heatmap" and meta["game"] == "gta6"
    start, end = meta["clip"]["source_start"], meta["clip"]["source_end"]
    assert start < BURST < end

    # sound and picture line up with the source: the burst and a scene cut land where they should
    assert abs(audio_peak(folder / "clip.mp4") - (BURST - start)) < 0.15
    expected_cut = next(k * SCENES for k in range(1, 9) if start + 0.5 < k * SCENES < end - 0.5)
    assert any(abs(c - (expected_cut - start)) < 0.1 for c in scene_changes(folder / "clip.mp4"))

    post = (folder / "post.txt").read_text(encoding="utf-8")
    assert "@testcreator" in post and "watch?v=testvideo01&t=" in post and "Hype Track - Test Artist [NCS]" in post
    assert "#GTA6" in post
    assert (folder / "clip_nomusic.mp4").exists() and meta["music"]["start_in_track"] >= 10  # skipped the quiet intro
    assert meta["captions"]["words"] > 10

    # the same moment isn't clipped twice
    assert make(url, MakeOptions(hook="again", prompt=False), cfg) == []
    assert len(make(url, MakeOptions(hook="again", prompt=False, force=True), cfg)) == 1


def test_local_file_with_chosen_times_and_no_music(cfg, fake_transcribe, tmp_path):
    src = make_video(tmp_path / "gameplay.mp4", duration=90, burst_at=40.0)
    folders = make(str(src), MakeOptions(start=30.0, end=55.0, hook="", music="none", lang="hi", prompt=False), cfg)
    meta = check_clip(folders[0], min_len=20, max_len=30)
    assert meta["moment"]["source"] == "manual" and meta["music"] is None
    assert not (folders[0] / "clip_nomusic.mp4").exists()
    assert fake_transcribe["language"] == "hi"
    assert 28.5 <= meta["clip"]["source_start"] <= 32.0


def test_local_file_without_replay_data_falls_back_to_loudness(cfg, fake_transcribe, tmp_path):
    src = make_video(tmp_path / "gameplay.mp4", duration=150, burst_at=100.0)
    folders = make(str(src), MakeOptions(hook="LOUDEST PART", music="none", prompt=False), cfg)
    meta = check_clip(folders[0])
    assert meta["moment"]["source"] == "loudness"
    assert meta["clip"]["source_start"] < 100.0 < meta["clip"]["source_end"]


def test_video_without_audio_still_renders(cfg, tmp_path):
    src = make_video(tmp_path / "silent.mp4", duration=60, audio=False)
    folders = make(str(src), MakeOptions(start=10, end=35, hook="NO SOUND", prompt=False), cfg)
    info = media.probe(folders[0] / "clip.mp4")
    assert (info.width, info.height) == (1080, 1920) and info.has_audio


def test_hindi_clip_gets_one_video_with_english_letter_captions(cfg, tmp_path, monkeypatch):
    from clipper.transcribe import Transcript, Word

    hindi = ["भाई", "देख", "पुलिस", "आ", "गई।", "भागो", "यार", "जल्दी", "निकल।"]

    def fake_hindi(audio, c, language=None, **kwargs):
        total, t, words = len(audio) / 16000, 1.0, []
        i = 0
        while t + 0.5 < total:
            words.append(Word(t, t + 0.4, hindi[i % len(hindi)], 0.9))
            t += 0.5
            i += 1
        return Transcript(language="hi", probability=0.9, words=words)

    monkeypatch.setattr("clipper.pipeline.transcribe", fake_hindi)
    cfg.render.keep_work_files = True
    src = make_video(tmp_path / "hindi.mp4", duration=60, burst_at=30.0)
    folder = make(str(src), MakeOptions(start=20, end=42, hook="भाई ये क्या हो गया", music="none", prompt=False), cfg)[0]
    meta = check_clip(folder, max_len=22)
    assert meta["captions"]["script"] == "latin"
    assert not list(folder.glob("*devanagari*"))
    srt = (folder / "captions.srt").read_text(encoding="utf-8")
    assert "police" in srt and "पुलिस" not in srt
    ass = (next((cfg.root / "cache" / "work").iterdir()) / "clip.ass").read_text(encoding="utf-8")
    assert "POLICE" in ass and "BHAAGO" in ass
    assert "English letters" in (folder / "post.txt").read_text(encoding="utf-8")


def test_gemini_words_replace_misheard_ones_and_failures_fall_back(cfg, tmp_path, monkeypatch):
    from clipper import gemini
    from clipper.transcribe import Transcript, Word

    local = ["भाई", "मेरा", "फ्रेंड", "आ", "गया"]  # what the local model heard ("friend" written in Hindi script)

    def fake_local(audio, c, language=None, **kwargs):
        total, words, t, i = len(audio) / 16000, [], 1.0, 0
        while t + 0.5 < total:
            words.append(Word(t, t + 0.4, local[i % len(local)], 0.9))
            t, i = t + 0.5, i + 1
        return Transcript(language="hi", probability=0.9, words=words, engine="small on CPU (int8)")

    def fake_gemini(clip_audio, gcfg, sr=16000, **kwargs):
        n = int(len(clip_audio) / 16000 / 0.5) + 2
        pairs = [("भाई", "bhai"), ("मेरा", "mera"), ("फ्रेंड", "friend"), ("आ", "aa"), ("गया", "gaya")]
        return gemini.GeminiWords("hi", [pairs[i % 5] for i in range(n)], "gemini-flash-latest")

    monkeypatch.setattr("clipper.pipeline.transcribe", fake_local)
    monkeypatch.setattr("clipper.pipeline.gemini.transcribe_clip", fake_gemini)
    monkeypatch.setattr(gemini, "_PAUSED_UNTIL", 0.0)
    cfg.captions.gemini.api_key = "test-key"
    cfg.render.keep_work_files = True
    src = make_video(tmp_path / "talk.mp4", duration=50, burst_at=25.0)
    folder = make(str(src), MakeOptions(start=15, end=37, hook="X", music="none", prompt=False, hindi_script="latin"), cfg)[0]
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["captions"]["words_from"] == "gemini:gemini-flash-latest"
    ass = next((cfg.root / "cache" / "work").iterdir()) / "clip.ass"
    text = ass.read_text(encoding="utf-8")
    assert "FRIEND" in text and "PHREND" not in text

    def broken(clip_audio, gcfg, sr=16000, **kwargs):
        raise gemini.GeminiError("quota", "free quota used up (limit: 20)")

    monkeypatch.setattr("clipper.pipeline.gemini.transcribe_clip", broken)
    shutil.rmtree(cfg.root / "cache" / "work")
    folder = make(str(src), MakeOptions(start=15, end=37, hook="X", music="none", prompt=False, hindi_script="latin", force=True), cfg)[0]
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["captions"]["words_from"] == "local" and "free quota used up" in meta["captions"]["gemini_note"]
    assert "PHREND" in (next((cfg.root / "cache" / "work").iterdir()) / "clip.ass").read_text(encoding="utf-8")
    assert (folder / "clip.mp4").exists()
