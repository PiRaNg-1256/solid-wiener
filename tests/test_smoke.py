"""Whole clips through every layout with effects on and off: catches filter-graph mistakes the unit tests can't."""

from __future__ import annotations

import subprocess

import pytest

from clipper import media
from clipper.pipeline import MakeOptions, make

from .conftest import make_video, needs_ffmpeg


@needs_ffmpeg
@pytest.mark.parametrize("layout, facecam, effects", [
    ("blur", None, "all"), ("crop", None, "all"), ("blur", "auto", "all"), ("blur", None, "none"),
])
def test_every_layout_renders(cfg, fake_transcribe, tmp_path, layout, facecam, effects):
    if facecam:
        from .test_facecam import stream_with_webcam

        src = stream_with_webcam(tmp_path / "stream.mp4", 1500, 760, 400, 300, seconds=24)
    else:
        src = make_video(tmp_path / "src.mp4", duration=40, size="1280x720", burst_at=17.0)
    music = cfg.root / "music"
    music.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=330:d=40", str(music / "song.wav")],
                   check=True)
    cfg.style.watermark = "@Me"
    cfg.captions.bleep = "all"
    folder = make(str(src), MakeOptions(start=4 if facecam else 10, end=18 if facecam else 24, hook="TEST HOOK",
                                        prompt=False, layout=layout, facecam=facecam, effects=effects), cfg)[0]
    info = media.probe(folder / "clip.mp4")
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert 13.0 <= info.duration <= 15.5
    assert (folder / "clip_nomusic.mp4").exists() and (folder / "thumb.jpg").exists() and (folder / "post.txt").exists()
