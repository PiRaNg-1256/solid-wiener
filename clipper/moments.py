"""Find the most-replayed moments in YouTube's heatmap (the "Most replayed" graph)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.signal import find_peaks, peak_widths

from .config import ClipConfig


@dataclass
class Moment:
    start: float  # proposed clip window, seconds in the source video (refined later)
    end: float
    peak_time: float
    region_start: float  # where the replay spike is (its width at half height)
    region_end: float
    height: float  # graph value at the peak (0-1), as YouTube reports it
    prominence: float  # how much it stands out from its surroundings (0-1)
    score: float
    source: str  # heatmap | loudness | manual
    wide: bool = False  # spike wider than a Short: refinement picks the most intense part

    @property
    def length(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def heatmap_arrays(heatmap: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points = sorted(
        (float(p["start_time"]), float(p["end_time"]), float(p.get("value") or 0.0))
        for p in heatmap or []
        if p.get("start_time") is not None and p.get("end_time") is not None
    )
    if not points:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    arr = np.array(points)
    return arr[:, 0], arr[:, 1], arr[:, 2]


def _smooth(values: np.ndarray) -> np.ndarray:
    if len(values) < 3:
        return values.copy()
    padded = np.pad(values, 1, mode="edge")
    return 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]


def mask_buckets(
    starts: np.ndarray,
    ends: np.ndarray,
    duration: float,
    cfg: ClipConfig,
    masked: list[tuple] = (),
) -> np.ndarray:
    """True for buckets that must not be picked: intro/outro zones and sponsor-type segments."""
    centers = (starts + ends) / 2
    mask = np.zeros(len(starts), dtype=bool)
    if duration > 0:
        mask |= centers < duration * cfg.skip_start_percent / 100
        mask |= centers > duration * (1 - cfg.skip_end_percent / 100)
    widths = np.maximum(ends - starts, 1e-9)
    for seg in masked:
        seg_start, seg_end = float(seg[0]), float(seg[1])
        overlap = np.clip(np.minimum(ends, seg_end) - np.maximum(starts, seg_start), 0, None)
        mask |= overlap / widths >= 0.5
        # viewers skipping the segment land right where it ends, which creates a fake spike
        landing = np.flatnonzero(ends > seg_end)
        if len(landing):
            mask[landing[0]] = True
    return mask


def _fit_length(start: float, end: float, cfg: ClipConfig, duration: float) -> tuple[float, float]:
    length = end - start
    if length < cfg.min_seconds:
        extra = cfg.min_seconds - length
        start, end = start - extra * 0.6, end + extra * 0.4
    elif length > cfg.max_seconds:
        mid = (start + end) / 2
        start, end = mid - cfg.max_seconds / 2, mid + cfg.max_seconds / 2
    if start < 0:
        end, start = end - start, 0.0
    if duration and end > duration:
        start, end = max(0.0, start - (end - duration)), duration
    return start, end


def find_moments(
    heatmap: list[dict],
    duration: float,
    cfg: ClipConfig,
    masked: list[tuple] = (),
    count: int = 1,
    source: str = "heatmap",
) -> list[Moment]:
    """Best moments first. Empty list if the graph has no clear spike."""
    starts, ends, values = heatmap_arrays(heatmap)
    if len(values) < 5:
        return []
    duration = float(duration or ends[-1])
    bucket = float(np.median(ends - starts))
    smooth = _smooth(values)
    mask = mask_buckets(starts, ends, duration, cfg, masked)

    floor = float(smooth.min())
    padded = np.concatenate([[floor], smooth, [floor]])  # lets peaks at the edges count
    peaks, props = find_peaks(padded, prominence=cfg.min_prominence)
    if len(peaks) == 0:
        return []
    _, _, left_ips, right_ips = peak_widths(
        padded, peaks, rel_height=0.5, prominence_data=(props["prominences"], props["left_bases"], props["right_bases"])
    )

    def center_time(index: float) -> float:
        return float(starts[0] + (index + 0.5) * bucket)

    candidates: list[Moment] = []
    for k, p in enumerate(peaks):
        i = int(p) - 1
        if not 0 <= i < len(smooth) or mask[i]:
            continue
        height = float(smooth[i])  # smoothed, used for ranking
        prominence = float(min(1.0, props["prominences"][k]))
        region_start = min(center_time(left_ips[k] - 1), float(starts[i]))
        region_end = max(center_time(right_ips[k] - 1), float(ends[i]))
        start = region_start - cfg.lead_in_seconds
        end = region_end + cfg.tail_seconds
        wide = (end - start) > cfg.max_seconds
        if wide:
            start, end = max(0.0, start), min(duration, end)
        else:
            start, end = _fit_length(start, end, cfg, duration)
        candidates.append(
            Moment(
                start=start,
                end=end,
                peak_time=float((starts[i] + ends[i]) / 2),
                region_start=region_start,
                region_end=region_end,
                height=float(values[i]),  # shown to the user: matches the graph
                prominence=prominence,
                # "highest part of the graph" first, but a spike must also stand out:
                # this stops a slow slope near the start from beating a real spike
                score=0.5 * height + 0.5 * prominence,
                source=source,
                wide=wide,
            )
        )

    candidates.sort(key=lambda m: (m.score, m.height), reverse=True)
    chosen: list[Moment] = []
    for m in candidates:
        clash = any(
            m.start < c.end and c.start < m.end or abs(m.peak_time - c.peak_time) < cfg.min_gap_seconds for c in chosen
        )
        if not clash:
            chosen.append(m)
        if len(chosen) >= count:
            break
    return chosen


def manual_moment(start: float, end: float) -> Moment:
    return Moment(start, end, (start + end) / 2, start, end, 0.0, 0.0, 0.0, "manual")
