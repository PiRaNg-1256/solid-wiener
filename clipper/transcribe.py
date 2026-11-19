"""Speech to text with word timings (faster-whisper), English and Hindi."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import gpu
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
    engine: str = ""  # e.g. "large-v3 on GPU (float16)"
    removed: int = 0  # made-up lines dropped (see made_up)

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words).strip()


@dataclass
class Engine:
    device: str  # cuda | cpu
    compute_type: str
    model: str  # model name, e.g. large-v3
    path: str = ""  # local folder of the downloaded model
    note: str = ""  # why this choice was made (shown to the user when the GPU wasn't used)

    @property
    def label(self) -> str:
        where = "GPU" if self.device == "cuda" else "CPU"
        return f"{self.model} on {where} ({self.compute_type})"


GPU_MODEL, CPU_MODEL = "large-v3", "small"
_ENGINES: dict[tuple, Engine] = {}
_MODEL = None
_MODEL_KEY: tuple | None = None


def _download(model: str) -> str:
    if Path(model).is_dir():
        return model
    from faster_whisper.utils import download_model

    try:
        return download_model(model)
    except Exception as exc:
        raise ClipperError(
            f"Couldn't download the speech model '{model}': {exc}\n"
            "The first run downloads it from huggingface.co, so you need an internet connection."
        ) from None


def choose_engine(cfg: CaptionConfig, cache_dir: Path) -> Engine:
    """GPU when an NVIDIA GPU passes a one-time test, else CPU. 'auto' model = large-v3 on GPU, small on CPU."""
    key = (cfg.model, cfg.device, str(cache_dir))
    if key in _ENGINES:
        return _ENGINES[key]
    model = (cfg.model or "auto").strip()
    cpu_model = CPU_MODEL if model == "auto" else model
    gpu_model = GPU_MODEL if model == "auto" else model

    def on_cpu(reason: str) -> Engine:
        if cfg.device == "cuda":
            raise ClipperError(f"captions.device is 'cuda' but the GPU can't be used: {reason}")
        return Engine("cpu", "int8", cpu_model, note=reason)

    if cfg.device == "cpu":
        engine = Engine("cpu", "int8", cpu_model)
    elif not (gpus := gpu.nvidia_gpus()):
        engine = on_cpu("no NVIDIA GPU found")
    else:
        gpu.prepare_cuda_libraries()
        try:
            import ctranslate2

            usable = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            usable = False
        if not usable:
            engine = on_cpu(f"{gpus[0].name} found, but CUDA isn't usable: run setup.cmd again "
                            "(it installs the GPU library) or update the NVIDIA driver")
        else:
            path = _download(gpu_model)
            big = max(g.memory_mb for g in gpus) >= 6000  # large-v3 in float16 needs roughly 4-5 GB
            engine = None
            last = ""
            for compute_type in (["float16", "int8_float16", "int8"] if big else ["int8_float16", "int8"]):
                ok, detail = gpu.probe_whisper(path, compute_type, cache_dir)
                if ok:
                    engine = Engine("cuda", compute_type, gpu_model, path)
                    break
                last = detail
            if engine is None:
                engine = on_cpu(f"the GPU test failed ({last})")
    _ENGINES[key] = engine
    return engine


def _load_model(engine: Engine):
    global _MODEL, _MODEL_KEY
    key = (engine.path or engine.model, engine.device, engine.compute_type)
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL
    if engine.device == "cuda":
        gpu.prepare_cuda_libraries()
    from faster_whisper import WhisperModel

    try:
        _MODEL = WhisperModel(engine.path or _download(engine.model), device=engine.device, compute_type=engine.compute_type)
    except ClipperError:
        raise
    except Exception as exc:
        raise ClipperError(f"Couldn't load the speech model {engine.label}: {exc}") from None
    _MODEL_KEY = key
    return _MODEL


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


# Speech models learn from subtitles that often censor swearing, so they tend to drop swears or write a milder
# word. Showing them uncensored gamer talk first makes them write what is actually said (masking happens later).
STYLE_PROMPT = {
    "en": "Bro, what the fuck was that? Holy shit, he's dead! Let's go, man.",
    "hi": "भाई ये क्या था, भेनचोद! What the fuck, चल चल, मर गया यार।",
}
SWEAR_HINTS = {
    "en": ["fuck", "fucking", "shit", "bitch", "asshole", "motherfucker"],
    "hi": ["भेनचोद", "मादरचोद", "चूतिया", "भोसड़ीके", "गांडू", "साला", "fuck", "shit"],
}


# Lines speech models invent over music or silence. Credits are never real speech; a streamer may really say
# "subscribe" or "thank you", so those only go when the model itself wasn't sure.
_CREDITS = re.compile(r"amara\.org|subtitles? (by|from)|transcri(bed|ption) by|captions? by|translated by|"
                      r"www\.|\.com\b", re.I)
_OUTRO = re.compile(r"^(thanks?( you)?( so much| very much)?( for watching| for listening)?|please subscribe|"
                    r"(like and |don'?t forget to )?subscribe( to (my|the|our) channel)?|see you (in the next video|"
                    r"next time)|bye(-| )?bye|धन्यवाद|शुक्रिया|सब्सक्राइब( करें)?)[.!?। ]*$", re.I)


def made_up(text: str, avg_logprob: float, no_speech_prob: float, compression_ratio: float) -> bool:
    """Is this recognised line most likely invented by the model (a 'hallucination')?"""
    clean = " ".join(text.split()).strip()
    if not clean:
        return False
    if _CREDITS.search(clean):
        return True
    if compression_ratio > 2.4:  # the same words over and over: the model got stuck in a loop
        return True
    unsure = avg_logprob < -0.35 or no_speech_prob > 0.2
    return bool(_OUTRO.match(clean)) and unsure


def hint_text(language: str, vocabulary: list[str] | None = None) -> str:
    """Words the model should expect in every 30-second window: names from the game, your own words, swears."""
    words = list(dict.fromkeys([v.strip() for v in (vocabulary or []) if v and v.strip()][:24]))
    words += SWEAR_HINTS.get(language, [])  # only for languages with a list: others would be nudged to English
    return ", ".join(words)


def transcribe(audio: np.ndarray, cfg: CaptionConfig, language: str | None = None, cache_dir: Path | None = None,
               vocabulary: list[str] | None = None) -> Transcript:
    """audio: 16 kHz mono float32. language: force 'en' / 'hi', or None to detect.
    vocabulary: names and words to expect (the game's places and characters, your own list)."""
    if not has_speech(audio):
        return Transcript(note="no speech found")
    engine = choose_engine(cfg, cache_dir or Path("cache"))
    model = _load_model(engine)
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
        initial_prompt=STYLE_PROMPT.get(lang) if task == "transcribe" else None,
        hotwords=(hint_text(lang, list(cfg.vocabulary) + list(vocabulary or [])) or None) if task == "transcribe" else None,
    )
    words: list[Word] = []
    removed = 0
    for segment in segments:
        if made_up(segment.text or "", float(getattr(segment, "avg_logprob", 0.0)),
                   float(getattr(segment, "no_speech_prob", 0.0)), float(getattr(segment, "compression_ratio", 1.0))):
            removed += 1
            continue
        for w in segment.words or []:
            text = (w.word or "").strip()
            if text and w.end > w.start:
                words.append(Word(float(w.start), float(w.end), text, float(w.probability)))
    return Transcript(lang, prob, words, task == "translate", note=engine.note, engine=engine.label, removed=removed)


def model_is_downloaded(name: str) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(name, local_files_only=True)
        return True
    except Exception:
        return False
