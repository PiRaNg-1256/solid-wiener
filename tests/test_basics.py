"""Small helpers, settings, SponsorBlock parsing, game detection and post text."""

from __future__ import annotations

import pytest

from clipper.config import load_config
from clipper.metadata import VideoInfo
from clipper.package import description, detect_game, hashtags_for, title_options
from clipper.sponsorblock import parse_segments
from clipper.util import (
    ClipperError, ass_time, fmt_time, hex_to_ass, is_url, normalize_url, parse_time, slugify, strip_emoji, youtube_id,
)


def test_parse_and_format_times():
    assert parse_time("75") == 75
    assert parse_time("1:15") == 75
    assert parse_time("1:01:15") == 3675
    assert parse_time("12:30.5") == 750.5
    with pytest.raises(ClipperError):
        parse_time("abc")
    assert fmt_time(83.4) == "1:23"
    assert fmt_time(3725) == "1:02:05"
    assert ass_time(83.456) == "0:01:23.46"


def test_text_helpers():
    assert slugify("GTA 6 cops DON'T play!") == "gta-6-cops-don-t-play"
    assert slugify("भाई ये क्या", fallback="vid123") == "vid123"
    assert strip_emoji("COPS 💀💀 DON'T PLAY 😳") == "COPS DON'T PLAY"
    assert strip_emoji("पुलिस आ गई 🔥") == "पुलिस आ गई"
    assert hex_to_ass("#FFE600") == "&H0000E6FF"
    with pytest.raises(ClipperError):
        hex_to_ass("yellow")


def test_youtube_links():
    assert youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s") == "dQw4w9WgXcQ"
    assert youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_id("https://example.com/video") is None
    assert is_url("youtu.be/dQw4w9WgXcQ") and normalize_url("youtu.be/x") == "https://youtu.be/x"
    assert not is_url(r"C:\videos\clip.mp4")


def test_config_overrides_and_errors(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "clip:\n  max_seconds: 45\nstyle:\n  zoom: 1\n  layout: crop\n"
        "games:\n  gta6:\n    hashtags: ['#GTA6']\n  forza:\n    name: Forza\n    keywords: [forza]\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.clip.max_seconds == 45 and cfg.style.zoom == 1.0 and cfg.style.layout == "crop"
    assert cfg.games["gta6"].hashtags == ["#GTA6"] and cfg.games["gta6"].keywords  # merged, keywords kept
    assert cfg.games["forza"].name == "Forza"
    assert cfg.root == tmp_path and cfg.path("output") == tmp_path / "output"

    path.write_text("clip:\n  max_secondz: 45\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="Unknown setting 'clip.max_secondz'"):
        load_config(path)
    path.write_text("style:\n  layout: diagonal\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="layout"):
        load_config(path)


def test_sponsorblock_parsing():
    payload = [
        {"segment": [30.0, 75.5], "category": "sponsor", "actionType": "skip"},
        {"segment": [0, 0], "category": "sponsor", "actionType": "full"},
        {"segment": [500.0, 500.0], "category": "poi_highlight", "actionType": "poi"},
        {"segment": [5.0, 12.0], "category": "intro", "actionType": "skip"},
        {"segment": [100.0, 110.0], "category": "filler", "actionType": "skip"},  # kept: could be the fun part
    ]
    assert parse_segments(payload) == [(5.0, 12.0, "intro"), (30.0, 75.5, "sponsor")]


def test_game_detection_and_post_text():
    cfg = load_config(None)
    assert detect_game("GTA 6 Funny Moments #1", cfg.games)[0] == "gta6"
    assert detect_game("GTA VI police chase", cfg.games)[0] == "gta6"
    assert detect_game("GTA V heist fails", cfg.games)[0] == "gta5"  # 'gta v' must not match 'gta vi'
    assert detect_game("Red Dead Redemption 2 horse glitch", cfg.games)[0] == "rdr2"
    assert detect_game("Minecraft speedrun", cfg.games) is None

    game = cfg.games["gta6"]
    tags = hashtags_for(game, cfg.default_hashtags)
    assert tags[0] == "#GTA6" and "#shorts" in tags
    titles = title_options("GTA 6 COPS | DON'T PLAY", game, cfg.title_templates, tags)
    assert titles[0].startswith("GTA 6 COPS DON'T PLAY") and all(len(t) <= 100 for t in titles)
    assert all("{" not in t for t in title_options(None, game, cfg.title_templates, tags))
    assert not any("expected this in" in t for t in title_options("HOOK", None, cfg.title_templates, ["#gaming"]))

    video = VideoInfo(id="abcdefghijk", url="u", title="t", channel="Some Channel", handle="@creator", duration=600)
    text = description("COPS DON'T PLAY", video, 754.2, "Track - Artist (NCS)", tags, credit_on=True)
    assert "@creator" in text and "watch?v=abcdefghijk&t=754s" in text and "Track - Artist (NCS)" in text
