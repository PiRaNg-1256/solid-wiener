"""Facecam split layout: finding the webcam, the layout itself, and the whole clip."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipper import facecam, media
from clipper.layout import build_layout
from clipper.util import ClipperError

from .conftest import needs_ffmpeg

FACE = Path(__file__).parent / "data" / "webcam_face.jpg"
# fixed seed: the same "gameplay" every run (detection was checked on 6 different seeds while building it)
GAMEPLAY = "life=s=1920x1080:mold=10:r=15:ratio=0.1:seed=2:death_color=#203040:life_color=#90d0ff,format=yuv420p"


def iou(a, b) -> float:
    inter = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def stream_with_webcam(path: Path, x: int, y: int, w: int, h: int, face: bool = True, seconds: int = 12) -> Path:
    """Moving 'gameplay' with a live-looking webcam box (moving content) at x, y, w, h."""
    if face:
        cam = f"[1:v]scale={int(w * 1.1)}:{int(h * 1.1)},crop={w}:{h}:x='{w * 0.05}+{w * 0.04}*sin(t*2)':y='{h * 0.05}+{h * 0.04}*cos(t*1.5)',noise=alls=6:allf=t[cam]"
        inputs = ["-loop", "1", "-framerate", "15", "-i", str(FACE)]
    else:  # a webcam showing something that isn't a face
        cam = f"[1:v]scale={w}:{h},hue=h=t*40[cam]"
        inputs = ["-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=15"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", GAMEPLAY, *inputs, "-filter_complex",
                    f"{cam};[0:v][cam]overlay={x}:{y}:shortest=1,format=yuv420p", "-t", str(seconds),
                    "-c:v", "libx264", "-preset", "ultrafast", "-an", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def streams(tmp_path_factory):
    folder = tmp_path_factory.mktemp("streams")
    return {
        "bottom-right": (stream_with_webcam(folder / "br.mp4", 1500, 760, 400, 300), (1500, 760, 400, 300)),
        "top-left": (stream_with_webcam(folder / "tl.mp4", 24, 24, 512, 288), (24, 24, 512, 288)),
        "no-face": (stream_with_webcam(folder / "nf.mp4", 1460, 740, 440, 320, face=False), (1460, 740, 440, 320)),
    }


def test_settings_are_understood():
    assert facecam.parse_hint("") is None and facecam.parse_hint("Off") is None
    assert facecam.parse_hint("Auto") == "auto" and facecam.parse_hint("Bottom right") == "bottom-right"
    assert facecam.parse_hint("top_left") == "top-left"
    assert facecam.parse_hint("75, 70, 25, 30") == (75, 70, 25, 30) and facecam.parse_hint("75%,70%,25%,30%") == (75, 70, 25, 30)
    for bad in ("middle", "1,2,3", "50,50,0,10", "120,0,10,10"):
        with pytest.raises(ClipperError, match="Facecam must be"):
            facecam.parse_hint(bad)


@needs_ffmpeg
@pytest.mark.parametrize("name, hint, minimum", [
    ("bottom-right", "auto", 0.95), ("bottom-right", "bottom-right", 0.95), ("top-left", "auto", 0.85),
])
def test_webcam_is_found_from_the_face(streams, name, hint, minimum):
    media.init()
    path, truth = streams[name]
    box = facecam.find_facecam(path, 0, 12, 1920, 1080, facecam.parse_hint(hint))
    assert box is not None and iou(box.as_list(), truth) >= minimum, (box, truth)
    assert box.face is not None and truth[0] < box.face[0] < truth[0] + truth[2]


@needs_ffmpeg
def test_no_face_auto_gives_up_but_a_corner_still_works(streams):
    media.init()
    path, truth = streams["no-face"]
    assert facecam.find_facecam(path, 0, 12, 1920, 1080, "auto") is None
    box = facecam.find_facecam(path, 0, 12, 1920, 1080, "bottom-right")
    assert box is not None and iou(box.as_list(), truth) >= 0.85, (box, truth)
    exact = facecam.find_facecam(path, 0, 12, 1920, 1080, (75, 70, 25, 30))
    assert exact.as_list() == [1440, 756, 480, 324]


def _crops(filter_text: str) -> list[tuple[int, int, int, int]]:
    return [tuple(int(v) for v in m) for m in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", filter_text)]


def test_layout_keeps_the_webcam_out_of_the_gameplay_half():
    right = build_layout("blur", 1920, 1080, facecam=(1500, 760, 400, 300), face=(1700, 880))
    (cw, ch, cx, cy), (gw, gh, gx, gy) = _crops(right.filter)[:2]
    assert right.name == "facecam" and right.gameplay == (0, 960, 1080, 960)
    assert cx >= 1500 and cx + cw <= 1900 and abs(cw / ch - 1080 / 960) < 0.02  # webcam cropped to the panel shape
    assert gx + gw <= 1500 and "boxblur" not in right.filter  # gameplay taken from the left of the webcam
    left = build_layout("blur", 1920, 1080, facecam=(24, 24, 512, 288))
    gw, gh, gx, gy = _crops(left.filter)[1]
    assert gx >= 24 + 512 and gx + gw <= 1920 and gy == 0
    middle = build_layout("blur", 1920, 1080, facecam=(760, 700, 400, 300))
    assert "boxblur" in middle.filter  # can't be avoided: blurred over instead
    share = build_layout("blur", 1920, 1080, facecam=(1500, 760, 400, 300), share=0.4)
    assert share.gameplay == (0, 768, 1080, 1152)
    # the webcam panel is cut a little inside the box, so a box found a few pixels too big shows no gameplay strip
    assert cy >= 760 + 0.025 * 300 and cy + ch <= 760 + 300 - 0.025 * 300


def test_text_stays_off_the_face(cfg):
    """The top half is the face, so the hook is centred on the line between the halves and captions go under it."""
    from clipper.captions import Font, build_ass
    from clipper.transcribe import Word

    layout = build_layout("blur", 1920, 1080, facecam=(1500, 760, 400, 300))
    style, words = cfg.style, [Word(0.5, 0.9, "the"), Word(1.0, 1.4, "cops")]
    font = Font(cfg.fonts_dir / style.font_file)
    pad = max(8, round(style.hook_size * 0.3))

    def positions(hook):
        ass = build_ass(words, layout=layout, style=style, fonts_dir=cfg.fonts_dir, duration=5, hook=hook).splitlines()
        caption_bottom = 1920 - int(next(x for x in ass if x.startswith("Style: Caption,")).split(",")[-2])
        hook_line = next((x for x in ass if x.startswith("Dialogue:") and ",Hook," in x), None)
        return caption_bottom, int(hook_line.split(",")[7]) if hook_line else None

    bottom, margin = positions(None)
    assert margin is None and bottom == 960 + 80  # no hook: captions sit on the line
    for hook, lines in (("WAIT FOR IT", 1), ("WAIT FOR IT | NO WAY", 2)):
        bottom, margin = positions(hook)
        box = lines * font.ass_size(style.hook_size) + 2 * pad
        top = margin - pad
        assert abs(top + box / 2 - 960) <= 1, (hook, top, box)  # hook box centred on the line
        assert bottom - font.ass_size(style.caption_size) >= top + box + 20  # caption line starts below the box
        assert bottom < 1600 - 150  # and stays clear of YouTube's title overlay
    plain = build_ass(words, layout=build_layout("blur", 1920, 1080), style=style, fonts_dir=cfg.fonts_dir,
                      duration=5, hook="WAIT FOR IT")
    assert ",Hook,,0,0,0,," in plain  # other layouts keep the hook at the top, as before


@needs_ffmpeg
def test_whole_clip_face_big_on_top_and_not_in_the_gameplay(cfg, fake_transcribe, streams):
    import cv2

    from clipper.pipeline import MakeOptions, make

    path, truth = streams["bottom-right"]
    folder = make(str(path), MakeOptions(start=2, end=10, hook="WAIT FOR IT", music="none", prompt=False,
                                         facecam="auto"), cfg)[0]
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert meta["layout"]["name"] == "facecam" and iou(job["facecam"]["box"], truth) >= 0.9
    assert (folder / "clip.mp4").exists()
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "3", "-i", str(folder / "clip.mp4"), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    image = np.frombuffer(frame, np.uint8).reshape(1920, 1080, 3)

    def faces(img):
        h, w = img.shape[:2]
        detector = facecam._detector((w, h))
        _, found = detector.detect(np.ascontiguousarray(img))
        return [] if found is None else found

    top, bottom = image[:960], image[960:]
    big = faces(top)
    assert len(big) >= 1 and big[0][2] > 150  # the face fills a good part of the top half
    assert len(faces(bottom)) == 0  # the webcam's corner is not in the gameplay half


@needs_ffmpeg
def test_preview_picture_is_made_when_captions_are_prepared(cfg, fake_transcribe, streams):
    from clipper.pipeline import MakeOptions, prepare

    path, _ = streams["bottom-right"]
    folder = prepare(str(path), MakeOptions(start=2, end=10, hook="X", music="none", prompt=False, review=True,
                                            facecam="bottom-right"), cfg).folders[0]
    preview = folder / "preview.jpg"
    assert preview.exists() and preview.stat().st_size > 10_000 and not (folder / "clip.mp4").exists()
    info = media.probe(preview)
    assert (info.width, info.height) == (1080, 1920)


def test_sheet_facecam_column(cfg):
    from clipper import sheet

    assert sheet.parse_row(2, {"link": "x", "facecam": "Bottom right"}, cfg).facecam == "bottom-right"
    assert sheet.parse_row(2, {"link": "x", "facecam": "Off"}, cfg).facecam == "off"
    assert sheet.parse_row(2, {"link": "x", "facecam": "75,70,25,30"}, cfg).facecam == "75,70,25,30"
    assert sheet.parse_row(2, {"link": "x"}, cfg).facecam is None
    with pytest.raises(ClipperError, match="Facecam must be"):
        sheet.parse_row(2, {"link": "x", "facecam": "center please"}, cfg)


@needs_ffmpeg
def test_a_failed_preview_does_not_lose_the_prepared_clip(cfg, fake_transcribe, streams, monkeypatch):
    from clipper import pipeline
    from clipper.pipeline import MakeOptions, prepare

    def broken(*args, **kwargs):
        raise ClipperError("preview broke")

    monkeypatch.setattr(pipeline.render, "preview_frame", broken)
    path, _ = streams["bottom-right"]
    folder = prepare(str(path), MakeOptions(start=2, end=10, hook="X", music="none", prompt=False, review=True,
                                            facecam="off"), cfg).folders[0]
    assert (folder / "job.json").exists() and (folder / "captions.srt").exists()
    assert not (folder / "preview.jpg").exists()
