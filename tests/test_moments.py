"""Picking the most-replayed moment from YouTube's heatmap."""

from __future__ import annotations

import numpy as np

from clipper.config import ClipConfig
from clipper.moments import find_moments


def heatmap(values, duration):
    step = duration / len(values)
    return [{"start_time": i * step, "end_time": (i + 1) * step, "value": float(v)} for i, v in enumerate(values)]


def retention_curve(n=100, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return 0.5 * np.exp(-t / 25) + 0.15 + rng.uniform(0, 0.02, n)


def normalize(v):
    return v / v.max()


def test_picks_the_real_spike_not_the_intro():
    v = retention_curve()
    v[0] = 1.5  # everyone watches the start
    v[62] += 0.9
    hm = heatmap(normalize(v), 1200)  # 12 s buckets
    best = find_moments(hm, 1200, ClipConfig())[0]
    assert 62 * 12 - 1 <= best.peak_time <= 63 * 12 + 1
    assert best.start < 62 * 12 and best.end > 63 * 12  # covers the spike
    assert best.start <= 62 * 12 - ClipConfig().lead_in_seconds + 1e-6  # with setup time before it
    assert 20 <= best.length <= 59


def test_slow_slope_near_start_does_not_beat_a_clear_spike():
    v = retention_curve()
    v[4] += 0.05  # tiny bump on the high early part of the curve
    v[70] += 0.45  # lower absolute value but a clear spike
    best = find_moments(heatmap(normalize(v), 1200), 1200, ClipConfig())[0]
    assert 69 * 12 <= best.peak_time <= 71 * 12


def test_sponsor_segment_and_the_landing_spike_after_it_are_ignored():
    v = retention_curve()
    v[40] += 1.0  # spike inside a sponsor read (people seeking around)
    v[45] += 0.9  # spike right where the sponsor ends (people skipping land here)
    v[80] += 0.5  # the real moment
    masked = [(40 * 12 - 6, 45 * 12 + 3, "sponsor")]
    best = find_moments(heatmap(normalize(v), 1200), 1200, ClipConfig(), masked=masked)[0]
    assert 79 * 12 <= best.peak_time <= 81 * 12


def test_several_moments_are_separate():
    v = retention_curve()
    for i, bump in ((20, 0.5), (22, 0.45), (50, 0.4), (85, 0.35)):
        v[i] += bump
    moments = find_moments(heatmap(normalize(v), 1200), 1200, ClipConfig(), count=4)
    assert len(moments) >= 3
    for a in moments:
        for b in moments:
            if a is not b:
                assert a.end <= b.start or b.end <= a.start
                assert abs(a.peak_time - b.peak_time) >= ClipConfig().min_gap_seconds
    assert moments == sorted(moments, key=lambda m: m.score, reverse=True)


def test_wide_buckets_from_long_streams_are_flagged():
    v = retention_curve()
    v[50] += 0.8
    best = find_moments(heatmap(normalize(v), 7200), 7200, ClipConfig())[0]  # 72 s buckets
    assert best.wide and best.length > 59  # refine() will pick the most intense part


def test_spike_near_the_end_stays_inside_the_video():
    v = retention_curve()
    v[96] += 0.8
    best = find_moments(heatmap(normalize(v), 600), 600, ClipConfig(skip_end_percent=0))[0]
    assert best.end <= 600 and best.length >= 20


def test_flat_or_tiny_graphs_give_nothing():
    assert find_moments(heatmap(np.full(100, 0.5), 600), 600, ClipConfig()) == []
    assert find_moments(heatmap([0.1, 1.0, 0.2], 60), 60, ClipConfig()) == []
    assert find_moments([], 600, ClipConfig()) == []
