"""Remembers which parts of which videos were already clipped (SQLite, in the cache folder)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    source_start REAL NOT NULL,
    source_end REAL NOT NULL,
    folder TEXT NOT NULL,
    hook TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_video ON clips(video_id);
"""


def _connect(cache_dir: Path) -> sqlite3.Connection:
    cache_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cache_dir / "clipper.db")
    conn.executescript(SCHEMA)
    return conn


def find_overlap(cache_dir: Path, video_id: str, start: float, end: float, min_share: float = 0.5) -> str | None:
    """Folder of an earlier clip covering at least `min_share` of this window, if any."""
    with _connect(cache_dir) as conn:
        rows = conn.execute(
            "SELECT source_start, source_end, folder FROM clips WHERE video_id = ? AND source_start < ? AND source_end > ?",
            (video_id, end, start),
        ).fetchall()
    for s, e, folder in rows:
        if min(e, end) - max(s, start) >= min_share * (end - start) and Path(folder).exists():
            return folder
    return None


def record(cache_dir: Path, video_id: str, start: float, end: float, folder: Path, hook: str | None) -> None:
    with _connect(cache_dir) as conn:
        conn.execute(
            "INSERT INTO clips (video_id, source_start, source_end, folder, hook, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, start, end, str(folder), hook, datetime.now().isoformat(timespec="seconds")),
        )
