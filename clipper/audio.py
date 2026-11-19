"""Audio: pick background music, set its level against the game audio, dip it under speech, normalize."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import media
from .analysis import envelope_db, integrated_lufs, master, smooth_db
from .config import MusicConfig, RenderConfig
from .transcribe import Word
from .util import ClipperError

SR = 48000
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}


@dataclass
class MusicTrack:
    path: Path
    credit: str


@dataclass
class AudioResult:
    mixed: Path  # final audio for clip.mp4
    game: Path  # game audio only, for clip_nomusic.mp4
    track: MusicTrack | None = None
    music_offset: float = 0.0
    music_gain_db: float = 0.0
    game_lufs: float = float("-inf")


def _credit_for(path: Path) -> str:
    """Credit line from 'track.txt' next to 'track.mp3', else the file name."""
    note = path.with_suffix(".txt")
    if note.exists():
        text = note.read_text(encoding="utf-8").strip()
        if text:
            return text
    return path.stem


def list_tracks(folder: Path) -> list[MusicTrack]:
    if not folder.is_dir():
        return []
    return [
        MusicTrack(p, _credit_for(p))
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ]


def pick_track(folder: Path, choice: str | None = None, rng: random.Random | None = None) -> MusicTrack | None:
    """choice: None = random track from the folder, 'none' = no music, or a path to a file."""
    if choice is not None and choice.strip().lower() == "none":
        return None
    if choice:
        path = Path(choice).expanduser()
        if not path.is_file():
            raise ClipperError(f"Music file not found: {choice}")
        return MusicTrack(path, _credit_for(path))
    tracks = list_tracks(folder)
    return (rng or random).choice(tracks) if tracks else None


def speech_intervals(words: list[Word], merge_gap: float = 0.5, before: float = 0.1, after: float = 0.25) -> list[tuple[float, float]]:
    spans: list[list[float]] = []
    for w in sorted(words, key=lambda w: w.start):
        start, end = w.start - before, w.end + after
        if spans and start - spans[-1][1] <= merge_gap:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [(max(0.0, s), e) for s, e in spans]


def duck_gain(n: int, sr: int, spans: list[tuple[float, float]], duck_db: float, attack: float = 0.12, release: float = 0.35) -> np.ndarray:
    """Per-sample gain: 1.0 normally, duck_db lower during speech, with smooth ramps."""
    rate = 200
    t = np.arange(int(np.ceil(n / sr * rate)) + 2) / rate
    amount = np.zeros_like(t)
    for start, end in spans:
        ramp_in = np.clip((t - (start - attack)) / attack, 0, 1)
        ramp_out = np.clip(1 - (t - end) / release, 0, 1)
        amount = np.maximum(amount, np.minimum(ramp_in, ramp_out))
    gain = 10 ** (duck_db * amount / 20)
    return np.interp(np.arange(n) / sr, t, gain).astype(np.float32)


def music_start(music_mono: np.ndarray, sr: int, length: float) -> float:
    """Start where the track gets going (skip quiet intros), leaving enough music for the clip."""
    total = len(music_mono) / sr
    if total <= length + 1:
        return 0.0
    times, db = envelope_db(music_mono, sr, hop=0.5, window=1.0)
    energy = smooth_db(times, db, 4.0)
    loud = np.flatnonzero(energy >= energy.max() - 3.0)
    first = float(times[loud[0]]) if len(loud) else 0.0
    return float(min(max(0.0, first - 1.0), total - length))


def _fit(samples: np.ndarray, n: int) -> np.ndarray:
    if len(samples) >= n:
        return samples[:n]
    return np.vstack([samples, np.zeros((n - len(samples), samples.shape[1]), dtype=samples.dtype)])


def _loop(samples: np.ndarray, n: int, sr: int) -> np.ndarray:
    if len(samples) == 0:
        return np.zeros((n, 2), dtype=np.float32)
    if len(samples) >= n:
        return samples[:n]
    fade = min(int(0.5 * sr), len(samples) // 4)
    if fade == 0:
        return np.resize(samples, (n, samples.shape[1]))
    out = samples.copy()
    while len(out) < n:  # crossfade the end of the track into its start
        ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        head = samples[:fade] * ramp
        out[-fade:] = out[-fade:] * (1 - ramp) + head
        out = np.vstack([out, samples[fade:]])
    return out[:n]


def _fade(samples: np.ndarray, sr: int, fade_in: float, fade_out: float) -> None:
    n = len(samples)
    a, b = min(n, int(fade_in * sr)), min(n, int(fade_out * sr))
    if a:
        samples[:a] *= np.linspace(0, 1, a, dtype=np.float32)[:, None]
    if b:
        samples[n - b :] *= np.linspace(1, 0, b, dtype=np.float32)[:, None]


def bleep(samples: np.ndarray, sr: int, spans: list[tuple[float, float]], freq: float = 1000.0,
          level_db: float = -16.0, ramp: float = 0.008) -> np.ndarray:
    """Replace the sound in each span with a beep (TV style), with short ramps so there are no clicks."""
    n = len(samples)
    keep = np.ones(n, np.float32)
    tone = np.zeros(n, np.float32)
    r = max(1, int(ramp * sr))
    t = np.arange(n) / sr
    for start, end in spans:
        a, b = max(0, int(start * sr)), min(n, int(end * sr))
        if b - a < 2 * r:
            continue
        env = np.ones(b - a, np.float32)
        env[:r], env[-r:] = np.linspace(0, 1, r), np.linspace(1, 0, r)
        keep[a:b] = np.minimum(keep[a:b], 1 - env)
        tone[a:b] = np.maximum(tone[a:b], env)
    beep = (np.sin(2 * np.pi * freq * t) * 10 ** (level_db / 20)).astype(np.float32) * tone
    return samples * keep[:, None] + beep[:, None]


def prepare_audio(
    segment: Path,
    has_audio: bool,
    clip_start: float,
    clip_end: float,
    words: list[Word],
    track: MusicTrack | None,
    music_cfg: MusicConfig,
    render_cfg: RenderConfig,
    workdir: Path,
    bleeps: list[tuple[float, float]] = (),
) -> AudioResult:
    """words: relative to the clip start. Writes WAV files into workdir. bleeps: (start, end) times where the
    game audio is replaced by a beep (hidden swear words)."""
    duration = clip_end - clip_start
    n = int(round(duration * SR))
    if has_audio:
        game = media.decode_audio(segment, sr=SR, channels=2, start=clip_start, duration=duration)
    else:
        game = np.zeros((n, 2), dtype=np.float32)
    game = _fit(game.astype(np.float32), n)
    _fade(game, SR, 0.015, 0.015)
    if bleeps:
        game = bleep(game, SR, bleeps)
    game_lufs = integrated_lufs(game, SR)

    game_out = workdir / "game.wav"
    media.write_wav(game_out, master(game, SR, render_cfg.loudness_lufs, render_cfg.true_peak_db), SR)
    result = AudioResult(mixed=game_out, game=game_out, game_lufs=game_lufs)
    if track is None or not music_cfg.enabled:
        return result

    music = media.decode_audio(track.path, sr=SR, channels=2)
    if len(music) < SR:
        raise ClipperError(f"The music file is empty or too short: {track.path.name}")
    offset = music_start(music.mean(axis=1), SR, duration)
    music = _loop(music[int(offset * SR) :].astype(np.float32), n, SR)
    _fade(music, SR, 0.25, 1.2)
    music_lufs = integrated_lufs(music, SR)
    # quiet or silent game audio: let the music carry the clip instead of matching silence
    reference = game_lufs if np.isfinite(game_lufs) and game_lufs > -45 else -30.0
    gain_db = (reference + music_cfg.volume_db - music_lufs) if np.isfinite(music_lufs) else -20.0
    music *= np.float32(10 ** (gain_db / 20))
    if words and music_cfg.duck_db < 0:
        music *= duck_gain(n, SR, speech_intervals(words), music_cfg.duck_db)[:, None]

    mix_out = workdir / "mix.wav"
    media.write_wav(mix_out, master(game + music, SR, render_cfg.loudness_lufs, render_cfg.true_peak_db), SR)
    result.mixed, result.track, result.music_offset, result.music_gain_db = mix_out, track, offset, gain_db
    return result
