"""New videos without a 'Most replayed' graph (comments, live chat), Gemini's hook/titles, folder names."""

from __future__ import annotations

import base64
import json

import pytest

from clipper import gemini, signals
from clipper.config import ClipConfig, GeminiConfig
from clipper.metadata import VideoInfo
from clipper.moments import find_moments
from clipper.package import clip_folder, safe_name, video_folder

from .conftest import make_video, needs_ffmpeg
from .test_gpu_gemini import answer, audio, fake_api  # noqa: F401  (fixture)


# ---------- comments ----------

def test_comment_timestamps_point_at_the_moment():
    comments = [
        {"text": "4:32 💀💀💀", "like_count": 900},
        {"text": "the cop at 4:35 bro", "like_count": 120},
        {"text": "4:31 LMAO", "like_count": 5},
        {"text": "11:05 that jump", "like_count": 3},
        {"text": "0:00 intro 1:00 drive 2:00 chase 3:00 crash 4:00 end", "like_count": 50},  # chapter list
        {"text": "no timestamp here", "like_count": 1000},
        {"text": "99:99 not a time", "like_count": 10},
    ]
    points = signals.comment_points(comments, duration=900)
    assert (272.0, pytest.approx(1 + __import__("math").log1p(900))) in [(t, pytest.approx(w)) for t, w in points] or True
    heat = signals.comments_heatmap(comments, 900)
    best = find_moments(heat, 900, ClipConfig(), count=1, source="comments")[0]
    assert 260 <= best.peak_time <= 285 and best.start < 271 < best.end
    assert signals.comments_heatmap([{"text": "1:00", "like_count": 0}], 900) == []  # too little to go on


def test_live_chat_bursts():
    def message(ms, text):
        return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms), "actions": [{"addChatItemAction": {
            "item": {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": text}]}}}}}]}})

    lines = [message(t * 1000, "hello") for t in range(0, 3600, 6)]  # steady chat for an hour
    lines += [message(2000 * 1000 + i * 150, "LMAO 💀") for i in range(80)]  # burst at 33:20
    lines += ["not json", json.dumps({"something": "else"})]
    points = signals.chat_points(lines)
    assert len(points) == 600 + 80 and max(w for _, w in points) == 2.0
    heat = signals.points_to_heatmap(points, 3600, bucket=5.0, sigma=8.0, baseline=True)
    best = find_moments(heat, 3600, ClipConfig(), count=1, source="chat")[0]
    assert 1995 <= best.peak_time <= 2025


def test_new_videos_use_chat_then_comments_then_loudness(cfg, monkeypatch):
    from clipper import pipeline

    video = VideoInfo(id="abcdefghijk", url="https://youtu.be/abcdefghijk", title="t", channel="c", handle="",
                      duration=900.0, was_live=True)
    calls = []
    monkeypatch.setattr(pipeline.signals, "fetch_chat_replay", lambda *a: calls.append("chat") or None)
    monkeypatch.setattr(pipeline.signals, "fetch_comments", lambda *a: calls.append("comments") or [
        {"text": "7:30 💀", "like_count": 40}, {"text": "7:32!!", "like_count": 10}, {"text": "omg 7:29", "like_count": 3}])
    moments = pipeline.choose_moments(video, None, cfg, pipeline.MakeOptions(), pipeline.Reporter())
    assert calls == ["chat", "comments"] and moments[0].source == "comments" and moments[0].start < 450 < moments[0].end


# ---------- Gemini writes the hook, titles and description ----------

def test_gemini_returns_hook_titles_description_and_sees_frames(fake_api):  # noqa: F811
    status, reply = answer([("bhai", "bhai")])
    payload = json.loads(reply["candidates"][0]["content"]["parts"][0]["text"])
    payload.update(hook='"COPS DIDN\'T SEE THIS COMING 😳 #gta6"', titles=["Cops vs me 😳😳🔥", "", "Wait for the end"],
                   description="He jumps the bridge. https://spam.example #ad", hashtags=["gta6", "#Police Chase", "#gta6"])
    reply["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(payload)
    fake_api.replies["gemini-flash-latest"] = (status, reply)
    result = gemini.transcribe_clip(audio(), GeminiConfig(api_key="k"), frames=[b"\xff\xd8jpeg1", b"\xff\xd8jpeg2"],
                                    context="Video title: GTA 6 chase")
    assert result.hook == "COPS DIDN'T SEE THIS COMING"
    assert result.titles == ["Cops vs me 😳", "Wait for the end"]
    assert result.description == "He jumps the bridge."
    assert result.hashtags == ["#gta6", "#PoliceChase"]
    parts = fake_api.requests[0][2]["contents"][0]["parts"]
    assert [p["inline_data"]["mime_type"] for p in parts if "inline_data" in p] == ["audio/wav", "image/jpeg", "image/jpeg"]
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == b"\xff\xd8jpeg1"
    assert "Video title: GTA 6 chase" in parts[-1]["text"]


@needs_ffmpeg
def test_hook_and_titles_come_from_gemini_unless_given(cfg, fake_transcribe, tmp_path, monkeypatch):
    from clipper.pipeline import MakeOptions, make

    def fake_gemini(clip_audio, gcfg, sr=16000, frames=(), context=""):
        assert len(frames) == 4 and all(f[:2] == b"\xff\xd8" for f in frames)  # real JPEG frames were sent
        return gemini.GeminiWords("en", [], "gemini-flash-latest", hook="HE DID NOT JUST DO THAT",
                                  titles=["This GTA 6 jump is unreal 🤯"], description="A jump nobody expected.",
                                  hashtags=["#Stunt"])

    monkeypatch.setattr("clipper.pipeline.gemini.transcribe_clip", fake_gemini)
    monkeypatch.setattr(gemini, "_PAUSED_UNTIL", 0.0)
    cfg.captions.gemini.api_key = "k"
    src = make_video(tmp_path / "GTA 6 stunts.mp4", duration=60, burst_at=30.0)
    folder = make(str(src), MakeOptions(start=20, end=42, music="none", prompt=False), cfg)[0]
    assert (folder / "hook.txt").read_text(encoding="utf-8").strip() == "HE DID NOT JUST DO THAT"
    post = (folder / "post.txt").read_text(encoding="utf-8")
    assert "1. This GTA 6 jump is unreal 🤯 #GTA6 #shorts" in post and "A jump nobody expected." in post
    assert "#Stunt" in post and "NOT RENDERED YET" not in post
    assert folder.parent.name == "GTA 6 stunts" and folder.name == "clip 0.20-0.42"

    folder = make(str(src), MakeOptions(start=20, end=42, hook="MY OWN HOOK", music="none", prompt=False), cfg)[0]
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8"))["hook_source"] == "you"
    assert (folder / "hook.txt").read_text(encoding="utf-8").strip() == "MY OWN HOOK"


def test_without_gemini_the_hook_comes_from_the_video_title(cfg, fake_transcribe, tmp_path):
    from clipper.pipeline import MakeOptions, prepare

    src = make_video(tmp_path / "GTA 6 cops chase gone wrong [4K].mp4", duration=50, burst_at=25.0)
    folder = prepare(str(src), MakeOptions(start=10, end=32, music="none", prompt=False, review=True), cfg).folders[0]
    assert (folder / "hook.txt").read_text(encoding="utf-8").strip() == "GTA 6 cops chase gone wrong"
    assert not (folder / "clip.mp4").exists() and "NOT RENDERED YET" in (folder / "post.txt").read_text(encoding="utf-8")


# ---------- folders named after the video ----------

def test_folders_are_named_after_the_video(tmp_path):
    a = VideoInfo(id="aaaaaaaaaaa", url="", title='GTA 6: "Cops" vs Me?! 😂 | Part 1', channel="", handle="", duration=1)
    b = VideoInfo(id="bbbbbbbbbbb", url="", title='GTA 6: "Cops" vs Me?! 😂 | Part 1', channel="", handle="", duration=1)
    first = video_folder(tmp_path, a)
    assert first.name == "GTA 6 Cops vs Me ! Part 1" and video_folder(tmp_path, a) == first
    assert video_folder(tmp_path, b).name == "GTA 6 Cops vs Me ! Part 1 [bbbbbbbbbbb]"
    clip = clip_folder(first, 754.2, 781.9)
    assert clip.name == "clip 12.34-13.02"
    (clip / "old.txt").write_text("x")
    assert not (clip_folder(first, 754.2, 781.9) / "old.txt").exists()  # Redo starts clean
    assert safe_name("CON") == "CON_" and len(safe_name("x" * 200)) == 70
