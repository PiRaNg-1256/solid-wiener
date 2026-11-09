from __future__ import annotations

import functools
import http.server
import shutil
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest

from clipper.config import Config
from clipper.transcribe import Transcript, Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def make_video(path: Path, duration: float = 120.0, size: str = "1280x720", fps: int = 30, scene: float = 15.0,
               burst_at: float | None = None, audio: bool = True) -> Path:
    """Synthetic gameplay: a new scene every `scene` seconds, quiet noise, optional loud 2 s burst."""
    count = int(np.ceil(duration / scene))
    sources = ["testsrc2", "smptebars", "testsrc", "rgbtestsrc", "smptehdbars"]
    parts = []
    for i in range(count):
        length = min(scene, duration - i * scene)
        parts.append(f"{sources[i % len(sources)]}=s={size}:r={fps}:d={length:.3f},hue=h={(i * 47) % 360},format=yuv420p[v{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(count)) + f"concat=n={count}:v=1:a=0[v]"
    args = ["-filter_complex", graph]
    if audio:
        audio_graph = f"anoisesrc=d={duration}:c=pink:a=0.03:r=48000[noise]"
        if burst_at is not None:
            ms = int(burst_at * 1000)
            audio_graph += (f";sine=f=440:d=2:r=48000,volume=6,adelay={ms}|{ms},apad=whole_dur={duration}[burst];"
                            "[noise][burst]amix=inputs=2:normalize=0[mono]")
        else:
            audio_graph += ";[noise]anull[mono]"
        audio_graph += ";[mono]aformat=channel_layouts=stereo[a]"
        args = ["-filter_complex", graph + ";" + audio_graph, "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    else:
        args += ["-map", "[v]"]
    ffmpeg(*args, "-c:v", "libx264", "-preset", "ultrafast", "-g", str(fps * 2), "-t", str(duration),
           "-movflags", "+faststart", str(path))
    return path


def make_music(path: Path, duration: float = 90.0, intro: float = 15.0) -> Path:
    """Quiet intro, then a louder pulsing chord (so we can check the music starts at the loud part)."""
    expr = (f"if(lt(t,{intro}),0.04,0.3)*(sin(2*PI*220*t)+sin(2*PI*277*t)+sin(2*PI*330*t))/3"
            "*(0.6+0.4*sin(2*PI*2*t))")
    ffmpeg("-f", "lavfi", "-i", f"aevalsrc='{expr}|{expr}':s=48000:d={duration}", "-c:a", "aac", "-b:a", "160k", str(path))
    return path


def fake_words(total: float, start: float = 1.0, step: float = 0.45) -> list[Word]:
    """Talking every 0.45 s in sentences of 6 words with a pause after each sentence."""
    vocab = ["no", "way", "bro", "look", "at", "this.", "the", "cops", "are", "going", "crazy", "now!"]
    words, t, i = [], start, 0
    while t + 0.4 < total - 0.5:
        text = vocab[i % len(vocab)]
        words.append(Word(t, t + 0.38, text, 0.9))
        t += step + (0.8 if text.endswith((".", "!")) else 0.0)
        i += 1
    return words


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    config = Config()
    config.root = tmp_path
    config.output_dir = "output"
    config.cache_dir = "cache"
    config.music.folder = "music"
    config.render.preset = "ultrafast"
    config.captions.model = "tiny"
    return config


@pytest.fixture
def fake_transcribe(monkeypatch):
    """Replace Whisper (needs a model download) with deterministic words."""
    calls = {}

    def _fake(audio, cfg, language=None):
        calls["language"] = language
        total = len(audio) / 16000
        return Transcript(language=language or "en", probability=0.99, words=fake_words(total))

    monkeypatch.setattr("clipper.pipeline.transcribe", _fake)
    return calls


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture
def http_server(tmp_path: Path):
    root = tmp_path / "srv"
    root.mkdir()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
