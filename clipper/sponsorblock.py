"""SponsorBlock: community-marked sponsor/intro/outro segments, so they're never picked as "best moment"."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://sponsor.ajay.app/api/skipSegments"
MASK_CATEGORIES = ("sponsor", "selfpromo", "interaction", "intro", "outro", "preview")


def parse_segments(payload) -> list[tuple[float, float, str]]:
    segments = []
    for item in payload or []:
        seg = item.get("segment") or []
        category = item.get("category", "")
        if item.get("actionType", "skip") in ("full", "poi", "chapter"):
            continue
        if len(seg) == 2 and category in MASK_CATEGORIES:
            start, end = float(seg[0]), float(seg[1])
            if end > start:
                segments.append((start, end, category))
    return sorted(segments)


def fetch_segments(video_id: str, timeout: float = 8.0) -> tuple[list[tuple[float, float, str]], str]:
    """(segments, status). Never raises: SponsorBlock being down must not stop a clip."""
    query = urllib.parse.urlencode({"videoID": video_id, "categories": json.dumps(list(MASK_CATEGORIES))})
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": "gaming-shorts-clipper/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            segments = parse_segments(json.loads(response.read().decode("utf-8")))
            return segments, f"{len(segments)} segment(s)"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return [], "none marked"
        return [], f"unavailable (HTTP {exc.code})"
    except Exception as exc:  # network problems, bad JSON
        return [], f"unavailable ({type(exc).__name__})"
