"""GPU selection (speech + video encoding), Gemini client, and lining Gemini's words up with local timings."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clipper import gemini, gpu, render, transcribe
from clipper.align import align, key
from clipper.config import CaptionConfig, GeminiConfig, RenderConfig
from clipper.transcribe import Word, choose_engine
from clipper.util import ClipperError

from .conftest import needs_ffmpeg


# ---------- speech model: GPU or CPU ----------

@pytest.fixture(autouse=True)
def fresh_engine_cache():
    transcribe._ENGINES.clear()
    yield
    transcribe._ENGINES.clear()


def fake_gpu(monkeypatch, memory_mb=8192, cuda_devices=1, probe=lambda path, ct: (True, "ok")):
    monkeypatch.setattr(gpu, "nvidia_gpus", lambda: [gpu.NvidiaGpu("NVIDIA GeForce RTX 3060", memory_mb)])
    monkeypatch.setattr(gpu, "prepare_cuda_libraries", lambda: [])
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: cuda_devices)
    monkeypatch.setattr(transcribe, "_download", lambda model: f"/models/{model}")
    monkeypatch.setattr(gpu, "probe_whisper", lambda path, ct, cache_dir: probe(path, ct))


def test_cpu_setting_and_no_gpu_use_the_small_model(tmp_path, monkeypatch):
    assert choose_engine(CaptionConfig(device="cpu"), tmp_path).label == "small on CPU (int8)"
    monkeypatch.setattr(gpu, "nvidia_gpus", lambda: [])
    engine = choose_engine(CaptionConfig(), tmp_path)
    assert (engine.device, engine.model, engine.note) == ("cpu", "small", "no NVIDIA GPU found")
    with pytest.raises(ClipperError, match="no NVIDIA GPU"):
        choose_engine(CaptionConfig(device="cuda"), tmp_path)


def test_working_gpu_gets_large_v3(tmp_path, monkeypatch):
    fake_gpu(monkeypatch)
    engine = choose_engine(CaptionConfig(), tmp_path)
    assert (engine.device, engine.compute_type, engine.model) == ("cuda", "float16", "large-v3")
    transcribe._ENGINES.clear()
    assert choose_engine(CaptionConfig(model="medium"), tmp_path).model == "medium"


def test_small_or_picky_gpus_step_down_precision_then_fall_back_to_cpu(tmp_path, monkeypatch):
    tried = []
    fake_gpu(monkeypatch, memory_mb=4096, probe=lambda path, ct: (tried.append(ct) or ct == "int8", "no fp16"))
    engine = choose_engine(CaptionConfig(), tmp_path)
    assert tried == ["int8_float16", "int8"] and engine.compute_type == "int8" and engine.device == "cuda"

    transcribe._ENGINES.clear()
    fake_gpu(monkeypatch, probe=lambda path, ct: (False, "cublas64_12.dll not found"))
    engine = choose_engine(CaptionConfig(), tmp_path)
    assert engine.device == "cpu" and engine.model == "small" and "cublas64_12.dll" in engine.note

    transcribe._ENGINES.clear()
    fake_gpu(monkeypatch, cuda_devices=0)
    assert "CUDA isn't usable" in choose_engine(CaptionConfig(), tmp_path).note


def test_gpu_test_runs_in_a_separate_process_and_is_remembered(tmp_path, monkeypatch):
    # no GPU here: the child process fails, the main process carries on
    ok, detail = gpu.probe_whisper(str(tmp_path / "no-such-model"), "float16", tmp_path, timeout=300)
    assert not ok and detail
    calls = []
    monkeypatch.setattr(gpu.subprocess, "run", lambda *a, **k: calls.append(1))
    assert gpu.probe_whisper(str(tmp_path / "no-such-model"), "float16", tmp_path) == (ok, detail)
    assert calls == []  # answered from the saved result
    gpu.forget_gpu_checks(tmp_path)
    assert not (tmp_path / "gpu_check.json").exists()


# ---------- video encoding ----------

@needs_ffmpeg
def test_encoder_auto_falls_back_to_cpu_without_gpu_and_forced_gpu_encoder_recovers(tmp_path):
    from clipper import media
    from clipper.captions import build_ass
    from clipper.config import PROJECT_DIR, StyleConfig
    from clipper.layout import build_layout

    from .conftest import make_video

    media.init()
    render._WORKS.clear()
    assert render.pick_encoder(RenderConfig()) == "libx264"  # this machine has no GPU encoder that works
    src = make_video(tmp_path / "src.mp4", duration=6, audio=True)
    work = tmp_path / "work"
    (work / "fonts").mkdir(parents=True)
    for f in (PROJECT_DIR / "assets" / "fonts").glob("*.ttf"):
        (work / "fonts" / f.name).write_bytes(f.read_bytes())
    layout = build_layout("blur", 1280, 720)
    (work / "c.ass").write_text(build_ass([], layout=layout, style=StyleConfig(), fonts_dir=PROJECT_DIR / "assets" / "fonts",
                                          duration=3, hook="TEST"), encoding="utf-8")
    wav = work / "a.wav"
    media.write_wav(wav, media.decode_audio(src, sr=48000, channels=2, start=1, duration=3), 48000)
    render._WORKS["h264_nvenc"] = True  # pretend the quick test passed, then the real encode fails
    used = render.render_video(src, 1, 4, layout, work, wav, tmp_path / "out.mp4",
                               RenderConfig(encoder="h264_nvenc", preset="ultrafast"), 30, ass_name="c.ass")
    assert used == "libx264" and media.probe(tmp_path / "out.mp4").width == 1080
    assert render._WORKS["h264_nvenc"] is False


# ---------- Gemini client (against a fake Gemini server) ----------

class FakeGemini:
    def __init__(self):
        self.replies = {}  # model -> (status, json body)
        self.requests = []

    def serve(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model = self.path.split("/models/")[1].split(":")[0]
                fake.requests.append((model, dict(self.headers), body))
                status, reply = fake.replies.get(model, (404, {"error": {"message": "not found"}}))
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"


def answer(words, language="hi"):
    payload = json.dumps({"language": language, "words": [{"text": t, "latin": l} for t, l in words]})
    return 200, {"candidates": [{"content": {"parts": [{"text": payload}]}, "finishReason": "STOP"}]}


@pytest.fixture
def fake_api(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(gemini, "API", fake.serve())
    monkeypatch.setattr(gemini, "_PAUSED_UNTIL", 0.0)
    yield fake
    fake.server.shutdown()


def audio():
    import numpy as np

    return (0.1 * np.sin(np.linspace(0, 2000, 16000 * 2))).astype("float32")


def test_gemini_success_sends_key_and_audio(fake_api):
    fake_api.replies["gemini-flash-latest"] = answer([("भाई", "bhai"), ("मेरा फ्रेंड", "mera friend")])
    result = gemini.transcribe_clip(audio(), GeminiConfig(api_key="test-key"))
    assert result.model == "gemini-flash-latest" and result.language == "hi"
    assert result.words == [("भाई", "bhai"), ("मेरा", "mera"), ("फ्रेंड", "friend")]  # two-word item split
    model, headers, body = fake_api.requests[0]
    assert {k.lower(): v for k, v in headers.items()}.get("x-goog-api-key") == "test-key"
    part = body["contents"][0]["parts"][0]["inline_data"]
    assert part["mime_type"] == "audio/wav" and base64.b64decode(part["data"])[:4] == b"RIFF"


def test_gemini_moves_to_the_next_free_model_then_gives_up_and_pauses(fake_api):
    fake_api.replies["gemini-flash-latest"] = (429, {"error": {"message": "Quota exceeded, limit: 20"}})
    fake_api.replies["gemini-flash-lite-latest"] = answer([("hello", "hello")], "en")
    assert gemini.transcribe_clip(audio(), GeminiConfig(api_key="k")).model == "gemini-flash-lite-latest"

    fake_api.replies["gemini-flash-lite-latest"] = (429, {"error": {"message": "Quota exceeded"}})
    with pytest.raises(gemini.GeminiError) as err:
        gemini.transcribe_clip(audio(), GeminiConfig(api_key="k"))
    assert err.value.kind == "quota"
    assert not gemini.available(GeminiConfig(api_key="k"))  # no more requests this run
    count = len(fake_api.requests)
    with pytest.raises(gemini.GeminiError):
        gemini.transcribe_clip(audio(), GeminiConfig(api_key="k"))
    assert len(fake_api.requests) == count


@pytest.mark.parametrize(
    "reply, kind",
    [
        ((403, {"error": {"message": "API key not valid"}}), "auth"),
        ((200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}), "bad_reply"),
        ((200, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}), "blocked"),
        ((200, {"promptFeedback": {"blockReason": "OTHER"}}), "blocked"),
        ((503, {"error": {"message": "overloaded"}}), "server"),
    ],
)
def test_gemini_failures_raise_so_captions_fall_back(fake_api, reply, kind):
    fake_api.replies["gemini-flash-latest"] = reply
    fake_api.replies["gemini-flash-lite-latest"] = reply
    with pytest.raises(gemini.GeminiError) as err:
        gemini.transcribe_clip(audio(), GeminiConfig(api_key="k"))
    assert err.value.kind == kind
    if kind == "auth":
        assert len(fake_api.requests) == 1  # a bad key won't work on the other model either


def test_no_key_means_no_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert not gemini.available(GeminiConfig())
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert gemini.api_key(GeminiConfig()) == "from-env"
    monkeypatch.delenv("GEMINI_API_KEY")
    assert gemini.check(GeminiConfig()) == [("API key", False, gemini.check(GeminiConfig())[0][2])]


# ---------- lining up Gemini's words with local timings ----------

def timed(*items):
    return [Word(s, e, t) for t, s, e in items]


def test_same_words_take_the_local_timings_even_across_scripts():
    local = timed(("भाई", 0.0, 0.4), ("भागो", 0.5, 0.9))
    out = align(["bhai", "bhaago"], [key("bhai"), key("bhaago")], local)
    assert [(w.text, w.start, w.end) for w in out] == [("bhai", 0.0, 0.4), ("bhaago", 0.5, 0.9)]


def test_corrected_extra_and_missing_words():
    local = timed(("mera", 0.0, 0.3), ("phrend", 0.4, 0.8), ("uh", 0.9, 1.0), ("aa", 1.1, 1.3), ("gaya", 2.0, 2.4))
    new = ["mera", "friend", "aa", "gaya", "bro"]
    out = align(new, [key(w) for w in new], local)
    assert [w.text for w in out] == new
    assert (out[1].start, out[1].end) == pytest.approx((0.4, 0.8))  # misheard word: same slot, right spelling
    assert out[3].start == pytest.approx(2.0)
    assert out[4].start >= out[3].end - 1e-6  # extra word placed after, in order
    assert all(a.start <= b.start for a, b in zip(out, out[1:]))


def test_disagreeing_transcripts_are_rejected():
    local = timed(("the", 0, 0.2), ("cops", 0.3, 0.6), ("are", 0.7, 0.8), ("here", 0.9, 1.2))
    assert align(["totally", "different", "sentence", "words"], [key(w) for w in ["totally", "different", "sentence", "words"]], local) is None
    assert align([], [], local) is None and align(["x"], ["x"], []) is None
