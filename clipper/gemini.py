"""Optional caption clean-up with Gemini's free tier. Any failure (quota used up, no reply, bad answer)
raises GeminiError and the caller keeps the local speech model's words.

Stays free as long as the API key comes from a Google AI Studio project without billing turned on:
Google can't charge such a key; when its free quota runs out it just answers "quota exceeded"."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field

import numpy as np

from .config import GeminiConfig
from .util import _EMOJI_RE as _EMOJI
from .util import strip_emoji

API = "https://generativelanguage.googleapis.com/v1beta"

PROMPT = """This is a short clip from a gaming video that will become a YouTube Short. You get its audio and a few
frames from it, in order.
{context}

Fill in:
1. "language": the main spoken language as a two-letter code ("hi" for Hindi or Hinglish, "en" for English).
2. "words": the speech for captions. Only words actually spoken (by the player or game characters), in order;
   don't add anything. Skip song lyrics, music and sound effects. If nobody speaks, return an empty list.
   For each word: "text" = the word in the script it is normally written in (Hindi in Devanagari, English in
   English); "latin" = the same word in English letters, spelled the way Indians type Hindi online ("bhai",
   "kya", "nahi", "yaar"), and English words in normal English spelling. Keep names and slang (GTA, Vice City, bro).
   Write swear words exactly as said and in full, English and Hindi ("fuck", "shit", "bhenchod", "chutiya"):
   never asterisks like "f***", never "[expletive]", never a softer word like "freaking". The tool hides them on
   screen itself; a missing or changed swear makes the captions wrong.
3. "hook": the text shown at the top of the Short for the whole clip. At most 7 words. Make people want to keep
   watching: tease or react to what happens, without giving away the ending. Be specific to what is seen and heard.
   No emojis, hashtags or quotes. No graphic violence or gore words, and no spoilers of the game's story.
   English, or Hindi in English letters if the speech is Hindi. No swear words, not even with asterisks.
4. "titles": three different YouTube title options, each under 70 characters, honest about what's in the clip.
   At most one emoji each. No hashtags. No swear words (YouTube gives no ads to titles with them).
5. "description": one or two short sentences for the video description. No hashtags, no links, no swear words.
6. "hashtags": up to 3 extra hashtags (starting with #) about the game or what happens."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "language": {"type": "STRING"},
        "words": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"text": {"type": "STRING"}, "latin": {"type": "STRING"}},
                "required": ["text", "latin"],
            },
        },
        "hook": {"type": "STRING"},
        "titles": {"type": "ARRAY", "items": {"type": "STRING"}},
        "description": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["language", "words", "hook", "titles", "description"],
}


class GeminiError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # quota | auth | model | network | blocked | bad_reply | server | off


@dataclass
class GeminiWords:
    language: str
    words: list[tuple[str, str]]  # (text in normal script, text in English letters)
    model: str
    notes: list[str] = field(default_factory=list)
    hook: str = ""
    titles: list[str] = field(default_factory=list)
    description: str = ""
    hashtags: list[str] = field(default_factory=list)


_PAUSED_UNTIL = 0.0  # after "quota used up", stop asking for a while instead of wasting a request per clip


def api_key(cfg: GeminiConfig) -> str:
    return (cfg.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def available(cfg: GeminiConfig) -> bool:
    return bool(api_key(cfg)) and time.time() >= _PAUSED_UNTIL


def wav_base64(audio: np.ndarray, sr: int = 16000) -> str:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _post(model: str, body: dict, key: str, timeout: float) -> dict:
    request = urllib.request.Request(
        f"{API}/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            message = ""
        message = message[:300] or f"HTTP {exc.code}"
        if exc.code == 429:
            raise GeminiError("quota", f"free quota used up ({message})") from None
        if exc.code in (401, 403):
            raise GeminiError("auth", f"the API key was refused ({message})") from None
        if exc.code in (400, 404):
            raise GeminiError("model", f"{model} can't do this ({message})") from None
        raise GeminiError("server", f"Gemini error {exc.code} ({message})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GeminiError("network", f"no reply from Gemini ({exc})") from None
    except ValueError:
        raise GeminiError("bad_reply", "Gemini's reply wasn't valid JSON") from None


def parse_reply(reply: dict, model: str) -> GeminiWords:
    if (reply.get("promptFeedback") or {}).get("blockReason"):
        raise GeminiError("blocked", f"Gemini refused the clip ({reply['promptFeedback']['blockReason']})")
    candidates = reply.get("candidates") or []
    if not candidates:
        raise GeminiError("bad_reply", "Gemini sent an empty reply")
    finish = candidates[0].get("finishReason", "")
    if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII"):
        raise GeminiError("blocked", f"Gemini stopped the answer ({finish})")
    text = "".join(part.get("text", "") for part in (candidates[0].get("content") or {}).get("parts", []))
    try:
        data = json.loads(text)
    except ValueError:
        raise GeminiError("bad_reply", "Gemini's answer wasn't the expected JSON") from None
    words = []
    for item in data.get("words") or []:
        original = str(item.get("text", "")).strip()
        latin = str(item.get("latin", "")).strip() or original
        if original:
            words.extend(_split(original, latin))
    hook = strip_emoji(re.sub(r"#\w+", "", str(data.get("hook") or ""))).strip().strip("\"'“”").strip()[:70]
    titles = [strip_emoji_but_one(str(t)).strip()[:90] for t in data.get("titles") or [] if str(t).strip()][:3]
    description = re.sub(r"https?://\S+|#\w+", "", str(data.get("description") or "")).strip()[:400]
    hashtags = []
    for tag in data.get("hashtags") or []:
        tag = "#" + re.sub(r"[^\w]", "", str(tag).lstrip("#"))
        if len(tag) > 1 and tag.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(tag)
    return GeminiWords(str(data.get("language", "")).lower()[:5], words, model, hook=hook, titles=titles,
                       description=description, hashtags=hashtags[:3])


def strip_emoji_but_one(text: str) -> str:
    """Titles may keep emoji, but not a wall of them."""
    found = 0

    def keep_first(m):
        nonlocal found
        found += 1
        return m.group(0)[0] if found == 1 else ""

    return re.sub(r"\s{2,}", " ", _EMOJI.sub(keep_first, text))


def _split(original: str, latin: str) -> list[tuple[str, str]]:
    """Gemini sometimes puts two words in one item; keep one word per caption item."""
    a, b = original.split(), latin.split()
    if len(a) == len(b):
        return list(zip(a, b))
    return [(original, latin)]


def transcribe_clip(audio: np.ndarray, cfg: GeminiConfig, sr: int = 16000, frames: list[bytes] = (),
                    context: str = "") -> GeminiWords:
    """Words, hook, titles and description for one clip, in a single request (the free tier counts requests).
    Tries each configured free model in turn; raises GeminiError if none gives a usable answer."""
    global _PAUSED_UNTIL
    key = api_key(cfg)
    if not key:
        raise GeminiError("off", "no Gemini API key set")
    if time.time() < _PAUSED_UNTIL:
        raise GeminiError("quota", "free quota used up earlier in this run")
    body = {
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "audio/wav", "data": wav_base64(audio, sr)}},
            *[{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f).decode("ascii")}} for f in frames],
            {"text": PROMPT.format(context=context.strip())},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
        "safetySettings": [
            {"category": c, "threshold": "BLOCK_NONE"}
            for c in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                      "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
        ],
    }
    notes: list[str] = []
    last: GeminiError | None = None
    for model in cfg.models:
        try:
            result = parse_reply(_post(model, body, key, cfg.timeout_seconds), model)
            result.notes = notes
            return result
        except GeminiError as exc:
            last = exc
            notes.append(f"{model}: {exc}")
            if exc.kind in ("auth", "network"):
                break  # another model won't help
    if last is not None and last.kind == "quota" and all("quota" in n for n in notes):
        _PAUSED_UNTIL = time.time() + 30 * 60
    raise GeminiError(last.kind if last else "off", "; ".join(notes) or "no Gemini models configured")


def check(cfg: GeminiConfig) -> list[tuple[str, bool, str]]:
    """For `clipper gemini-test`: does each configured model answer on this key? Uses one tiny request per model."""
    key = api_key(cfg)
    if not key:
        return [("API key", False, "not set: paste it into gemini_api_key at the top of config.yaml")]
    results = []
    body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word OK."}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 64}}
    for model in cfg.models:
        try:
            reply = _post(model, body, key, cfg.timeout_seconds)
            ok = bool(reply.get("candidates"))
            results.append((model, ok, "answers on your free tier" if ok else "empty reply"))
        except GeminiError as exc:
            results.append((model, False, str(exc)))
    return results
