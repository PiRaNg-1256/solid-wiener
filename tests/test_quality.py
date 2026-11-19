"""Picture quality: sharper sources for enlarged layouts, clean scaling, encoder settings."""

from __future__ import annotations

import pytest

from clipper import render
from clipper.config import Config, RenderConfig, validate
from clipper.download import video_format
from clipper.layout import SCALE_FLAGS, build_layout, enlarge
from clipper.pipeline import source_height
from clipper.util import ClipperError


def test_formats_never_pick_hdr_and_prefer_h264_up_to_1080p():
    assert video_format(1080).startswith("bv*[height<=1080][vcodec^=avc1]")
    tall = video_format(1440)
    assert "[height<=1440][dynamic_range=SDR]" in tall and "[height<=1080][vcodec^=avc1]" in tall


def test_layouts_that_enlarge_the_picture_download_1440p():
    cfg = Config()
    assert source_height(cfg) == 1080  # blur layout: the 1080p picture is shown smaller anyway
    cfg.style.layout = "crop"
    assert source_height(cfg) == 1440
    cfg.style.layout, cfg.style.facecam = "blur", "auto"
    assert source_height(cfg) == 1440
    cfg.youtube.max_height = "2160p"
    validate(cfg)
    assert source_height(cfg) == 2160
    cfg.youtube.max_height = "8k"
    with pytest.raises(ClipperError, match="max_height"):
        validate(cfg)


def test_enlarged_pictures_are_cleaned_and_sharpened_smaller_ones_left_alone():
    assert enlarge(0.7) == ("", "")
    pre, post = enlarge(1080 / 810)  # crop layout from 1440p
    assert pre == "" and post.startswith(",cas=0.3")
    pre, post = enlarge(3.3)  # facecam webcam
    assert pre.startswith("hqdn3d") and post == ",cas=0.70"
    assert enlarge(1.5, cas=False)[1].startswith(",unsharp=")  # very old FFmpeg without 'cas'
    crop = build_layout("crop", 1920, 1080).filter
    assert SCALE_FLAGS in crop and "cas=" in crop and "hqdn3d" in crop  # 1080p source, 1.78x
    sharp = build_layout("crop", 2560, 1440).filter  # 1440p source, 1.33x: sharpened, no clean-up needed
    assert "cas=" in sharp and "hqdn3d" not in sharp
    assert "cas=" not in build_layout("blur", 1920, 1080).filter  # gameplay is shown smaller: no sharpening
    cam = build_layout("blur", 1920, 1080, facecam=(1500, 760, 400, 300)).filter
    assert cam.count("cas=") == 1 and "unsharp" not in cam  # only the enlarged webcam is sharpened


def test_encoder_settings():
    x264 = render.encoder_args("libx264", RenderConfig(), 30.0)
    assert x264[x264.index("-g") + 1] == "60" and "aq-mode=3" in x264
    full = render.encoder_args("h264_nvenc", RenderConfig(), 60.0)
    safe = render.encoder_args("h264_nvenc", RenderConfig(), 60.0, safe=True)
    assert "-spatial-aq" in full and "-spatial-aq" not in safe and safe[safe.index("-g") + 1] == "120"
    assert render.encoder_args("libx264", RenderConfig(max_fps=30), 60.0)[-3] == "60"  # capped at max_fps
