"""Speech to text with word timings (faster-whisper), English and Hindi."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import CaptionConfig
from .util import ClipperError

SAMPLE_RATE = 16000


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Transcript:
    language: str = ""
    probability: float = 0.0
    words: list[Word] = field(default_factory=list)
    translated: bool = False
    note: str = ""

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words).strip()


_MODEL = None
_MODEL_KEY: tuple | None = None


def _load_model(name: str, device: str):
    global _MODEL, _MODEL_KEY
    if _MODEL is not None and _MODEL_KEY == (name, device):
        return _MODEL
    from faster_whisper import WhisperModel

    attempts: list[tuple[str, str]] = []
    if device in ("cuda", "auto"):
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                attempts.append(("cuda", "float16"))
        except Exception:
            pass
        if device == "cuda" and not attempts:
            raise ClipperError("No NVIDIA GPU was found for captions. Set captions.device: cpu in config.yaml.")
    if device != "cuda":
        attempts.append(("cpu", "int8"))
    last_error: Exception | None = None
    for dev, compute_type in attempts:
        try:
            _MODEL = WhisperModel(name, device=dev, compute_type=compute_type)
            _MODEL_KEY = (name, device)
            return _MODEL
        except Exception as exc:  # e.g. missing CUDA libraries
            last_error = exc
    raise ClipperError(
        f"Couldn't load the speech model '{name}': {last_error}\n"
        "The first run downloads it from huggingface.co, so you need an internet connection."
    )


def has_speech(audio: np.ndarray, sr: int = SAMPLE_RATE) -> bool:
    """Quick voice check with the VAD model bundled in faster-whisper (no download needed)."""
    if audio.size < sr // 2:
        return False
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    chunks = get_speech_timestamps(audio, VadOptions(min_speech_duration_ms=250), sampling_rate=sr)
    return len(chunks) > 0


def pick_language(all_probs: list[tuple[str, float]], allowed: list[str]) -> tuple[str, float]:
    """Choose among the allowed languages. Hindi speech is often detected as Urdu, so they count together."""
    allowed = [a.lower().strip() for a in allowed if a and a.strip()] or ["en"]
    probs = {lang: float(p) for lang, p in all_probs}
    if "hi" in allowed:
        probs["hi"] = probs.get("hi", 0.0) + probs.get("ur", 0.0)
    best = max(allowed, key=lambda lang: probs.get(lang, 0.0))
    return best, probs.get(best, 0.0)


def transcribe(audio: np.ndarray, cfg: CaptionConfig, language: str | None = None) -> Transcript:
    """audio: 16 kHz mono float32. language: force 'en' / 'hi', or None to detect."""
    if not has_speech(audio):
        return Transcript(note="no speech found")
    model = _load_model(cfg.model, cfg.device)
    allowed = [language] if language else cfg.languages
    if len(allowed) == 1:
        lang, prob = allowed[0], 1.0
    else:
        try:
            _, _, all_probs = model.detect_language(audio=audio, vad_filter=True)
            lang, prob = pick_language(all_probs, allowed)
        except Exception:
            lang, prob = allowed[0], 0.0
    task = "translate" if cfg.translate_to_english and lang != "en" else "transcribe"
    segments, _info = model.transcribe(
        audio,
        language=lang,
        task=task,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
    )
    words: list[Word] = []
    for segment in segments:
        for w in segment.words or []:
            text = (w.word or "").strip()
            if text and w.end > w.start:
                words.append(Word(float(w.start), float(w.end), text, float(w.probability)))
    return Transcript(lang, prob, words, task == "translate")


def model_is_downloaded(name: str) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(name, local_files_only=True)
        return True
    except Exception:
        return False
