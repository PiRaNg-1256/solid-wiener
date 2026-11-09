"""Audio analysis in numpy: loudness envelope, smoothing, and BS.1770 integrated loudness."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import lfilter


def envelope_db(samples: np.ndarray, sr: int, hop: float = 0.1, window: float = 0.4) -> tuple[np.ndarray, np.ndarray]:
    """RMS loudness (dBFS) every `hop` seconds, measured over `window` seconds."""
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    n = len(mono)
    hop_n, win_n = max(1, int(sr * hop)), max(1, int(sr * window))
    if n == 0:
        return np.zeros(0), np.zeros(0)
    csum = np.concatenate([[0.0], np.cumsum(mono.astype(np.float64) ** 2)])
    centers = np.arange(0, n, hop_n)
    lo = np.clip(centers - win_n // 2, 0, n)
    hi = np.clip(centers + win_n // 2, 0, n)
    mean_square = (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)
    return centers / sr, 10 * np.log10(mean_square + 1e-12)


def smooth_db(times: np.ndarray, db: np.ndarray, seconds: float) -> np.ndarray:
    """Moving average over `seconds`, done on power (not dB) so loud bursts count properly."""
    if len(db) < 2 or seconds <= 0:
        return db.copy()
    step = float(times[1] - times[0]) or 0.1
    width = max(1, int(round(seconds / step)))
    power = 10 ** (db / 10)
    kernel = np.ones(width) / width
    padded = np.pad(power, (width // 2, width - 1 - width // 2), mode="edge")
    return 10 * np.log10(np.convolve(padded, kernel, mode="valid") + 1e-12)


def loudest_time(times: np.ndarray, db: np.ndarray, start: float, end: float, smooth: float = 1.0) -> float:
    """Time of the most intense moment between start and end."""
    if len(times) == 0:
        return (start + end) / 2
    sm = smooth_db(times, db, smooth)
    inside = (times >= start) & (times <= end)
    if not inside.any():
        return (start + end) / 2
    idx = np.flatnonzero(inside)
    return float(times[idx[int(np.argmax(sm[idx]))]])


def _k_weighting(sr: int):
    """ITU-R BS.1770 K-weighting filters for any sample rate (same formulas as libebur128)."""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = np.tan(np.pi * f0 / sr)
    vh = 10 ** (gain_db / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    b1 = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    a1 = [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    f0, q = 38.13547087602444, 0.5003270373238773
    k = np.tan(np.pi * f0 / sr)
    a0 = 1 + k / q + k * k
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return (b1, a1), (b2, a2)


def integrated_lufs(samples: np.ndarray, sr: int) -> float:
    """Integrated loudness (LUFS) with the standard -70 LUFS and -10 LU gates."""
    x = samples if samples.ndim > 1 else samples[:, None]
    x = x.astype(np.float64)
    (b1, a1), (b2, a2) = _k_weighting(sr)
    y = lfilter(b2, a2, lfilter(b1, a1, x, axis=0), axis=0)
    block, step = int(round(0.4 * sr)), int(round(0.1 * sr))
    if len(y) < block:
        return float("-inf")
    csum = np.vstack([np.zeros((1, y.shape[1])), np.cumsum(y**2, axis=0)])
    starts = np.arange(0, len(y) - block + 1, step)
    power = ((csum[starts + block] - csum[starts]) / block).sum(axis=1)
    with np.errstate(divide="ignore"):
        loudness = -0.691 + 10 * np.log10(power)
    gated = power[loudness > -70]
    if gated.size == 0:
        return float("-inf")
    relative_gate = -0.691 + 10 * np.log10(gated.mean()) - 10
    final = power[(loudness > -70) & (loudness > relative_gate)]
    if final.size == 0:
        return float("-inf")
    return float(-0.691 + 10 * np.log10(final.mean()))


def pseudo_heatmap(times: np.ndarray, db: np.ndarray, bucket: float = 2.0, smooth: float = 6.0) -> list[dict]:
    """Turn a loudness envelope into heatmap-like points (0-1) for videos without replay data."""
    if len(times) == 0:
        return []
    sm = smooth_db(times, db, smooth)
    total = float(times[-1])
    edges = np.arange(0.0, total + bucket, bucket)
    values = []
    for a, b in zip(edges[:-1], edges[1:]):
        inside = (times >= a) & (times < b)
        values.append(float(sm[inside].mean()) if inside.any() else -120.0)
    v = np.array(values)
    lo, hi = np.percentile(v, 10), v.max()
    norm = np.clip((v - lo) / (hi - lo), 0, 1) if hi > lo else np.zeros_like(v)
    return [
        {"start_time": float(a), "end_time": float(b), "value": float(val)}
        for a, b, val in zip(edges[:-1], edges[1:], norm)
    ]


def limit_peaks(x: np.ndarray, sr: int, ceiling: float, lookahead: float = 0.03, smooth: float = 0.01) -> np.ndarray:
    """Look-ahead peak limiter: turns the volume down just around peaks that would go over `ceiling`."""
    peak = np.abs(x).max(axis=1) if x.ndim > 1 else np.abs(x)
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    if need.min() >= 1.0:
        return x
    # the minimum over +-30 ms starts the gain dip before each peak; averaging over 10 ms (< half of that
    # window) smooths it without ever letting a peak through
    gain = uniform_filter1d(minimum_filter1d(need, size=int(2 * lookahead * sr) | 1, mode="nearest"),
                            size=max(1, int(smooth * sr)), mode="nearest")
    gain = np.minimum(gain, need).astype(x.dtype)
    return x * (gain[:, None] if x.ndim > 1 else gain)


def master(x: np.ndarray, sr: int, target_lufs: float = -14.0, true_peak_db: float = -1.0, passes: int = 5) -> np.ndarray:
    """Bring audio to `target_lufs` without peaks above `true_peak_db` (half a dB kept for peaks between samples)."""
    ceiling = 10 ** ((true_peak_db - 0.5) / 20)
    x = np.asarray(x, dtype=np.float32)
    for _ in range(passes):
        loudness = integrated_lufs(x, sr)
        if not np.isfinite(loudness) or loudness < -70:
            return x  # silence
        if abs(loudness - target_lufs) <= 0.2 and np.abs(x).max() <= ceiling * 1.001:
            break
        x = limit_peaks(x * np.float32(10 ** ((target_lufs - loudness) / 20)), sr, ceiling)
    return x
