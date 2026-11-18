"""Hindi in English letters ("bhai ye kya ho gaya") from the Devanagari that Whisper writes.

Two layers:
1. A dictionary of common words, including English words Hindi speakers use (Whisper writes "police" as
   पुलिस; letter-by-letter that would become "pulis").
2. Rules for everything else, including the dropped vowel of spoken Hindi (करना is "karna", not "karana").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}
WITH_NUKTA = {"क": "k", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f", "य": "y"}
# Long vowels use capitals internally (A, I, U) so the spelling (a/aa, i/ee, u/oo) can be chosen later
INDEPENDENT_VOWELS = {
    "अ": "a", "आ": "A", "इ": "i", "ई": "I", "उ": "u", "ऊ": "U", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e",
}
VOWEL_SIGNS = {
    "ा": "A", "ि": "i", "ी": "I", "ु": "u", "ू": "U", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
}
VIRAMA, NUKTA, ANUSVARA, CHANDRABINDU, VISARGA = "्", "़", "ं", "ँ", "ः"
DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
SEMIVOWELS = {"y", "r", "l", "v"}
LABIALS = {"p", "ph", "f", "b", "bh", "m"}

COMMON = {
    # pronouns, "to be"
    "है": "hai", "हैं": "hain", "हूँ": "hoon", "हूं": "hoon", "हो": "ho", "था": "tha", "थी": "thi", "थे": "the",
    "थीं": "thi", "में": "mein", "मैं": "main", "मुझे": "mujhe", "मुझको": "mujhko", "मेरा": "mera", "मेरी": "meri",
    "मेरे": "mere", "तू": "tu", "तुम": "tum", "तुझे": "tujhe", "तेरा": "tera", "तेरी": "teri", "तेरे": "tere",
    "तुम्हारा": "tumhara", "तुम्हारी": "tumhari", "तुम्हें": "tumhe", "आप": "aap", "आपका": "aapka", "आपकी": "aapki",
    "आपको": "aapko", "हम": "hum", "हमारा": "hamara", "हमें": "hume", "वो": "wo", "वह": "woh", "ये": "ye",
    "यह": "yeh", "वे": "ve", "उस": "us", "इस": "is", "उसे": "use", "इसे": "ise", "उसका": "uska", "उसकी": "uski",
    "इसका": "iska", "इसकी": "iski", "उन्होंने": "unhone", "इन्होंने": "inhone", "किसी": "kisi", "कोई": "koi",
    "सबको": "sabko",
    # questions, places
    "क्या": "kya", "क्यों": "kyun", "क्यूँ": "kyun", "क्यूं": "kyun", "कैसे": "kaise", "कैसा": "kaisa",
    "कैसी": "kaisi", "कहाँ": "kahan", "कहां": "kahan", "कब": "kab", "कौन": "kaun", "कितना": "kitna",
    "कितने": "kitne", "कितनी": "kitni", "किधर": "kidhar", "यहाँ": "yahan", "यहां": "yahan", "वहाँ": "wahan",
    "वहां": "wahan", "इधर": "idhar", "उधर": "udhar",
    # yes / no
    "नहीं": "nahi", "नही": "nahi", "ना": "na", "न": "na", "मत": "mat", "हाँ": "haan", "हां": "haan", "जी": "ji",
    # little words
    "और": "aur", "या": "ya", "लेकिन": "lekin", "पर": "par", "तो": "to", "भी": "bhi", "ही": "hi", "सिर्फ": "sirf",
    "सिर्फ़": "sirf", "बस": "bas", "अब": "ab", "फिर": "phir", "अभी": "abhi", "कभी": "kabhi", "सब": "sab",
    "सारे": "saare", "सारा": "saara", "कुछ": "kuch", "बहुत": "bahut", "थोड़ा": "thoda", "थोड़ी": "thodi",
    "ज़्यादा": "zyada", "ज्यादा": "zyada", "बिल्कुल": "bilkul", "बिलकुल": "bilkul", "सच": "sach", "एकदम": "ekdum",
    "शायद": "shayad", "वैसे": "waise", "ऐसे": "aise", "ऐसा": "aisa", "जैसे": "jaise", "का": "ka", "की": "ki",
    "के": "ke", "को": "ko", "से": "se", "ने": "ne", "तक": "tak", "लिए": "liye", "लिये": "liye", "साथ": "saath",
    "बाद": "baad", "पहले": "pehle", "ऊपर": "upar", "नीचे": "neeche", "अंदर": "andar", "बाहर": "bahar",
    # people
    "भाई": "bhai", "भाइयों": "bhaiyon", "यार": "yaar", "भैया": "bhaiya", "दोस्त": "dost", "दोस्तों": "doston",
    "बंदा": "banda", "बंदे": "bande", "लोग": "log", "लोगों": "logon", "आदमी": "aadmi", "बच्चा": "baccha",
    # reactions, describing words
    "अरे": "arey", "ओए": "oye", "अबे": "abey", "वाह": "wah", "अच्छा": "accha", "ठीक": "theek", "सही": "sahi",
    "गज़ब": "gazab", "गजब": "gajab", "मस्त": "mast", "बढ़िया": "badhiya", "बेकार": "bekaar", "पागल": "pagal",
    "खतरनाक": "khatarnak", "ख़तरनाक": "khatarnak", "बड़ा": "bada", "बड़ी": "badi", "बड़े": "bade",
    "छोटा": "chhota", "नया": "naya", "पूरा": "poora", "असली": "asli",
    # verbs
    "देख": "dekh", "देखो": "dekho", "देखा": "dekha", "देखिए": "dekhiye", "चल": "chal", "चलो": "chalo",
    "चला": "chala", "आ": "aa", "आजा": "aaja", "आओ": "aao", "आया": "aaya", "आई": "aayi", "जा": "ja", "जाओ": "jao",
    "जाना": "jaana", "भाग": "bhaag", "भागो": "bhaago", "रुक": "ruk", "रुको": "ruko", "मार": "maar",
    "मारो": "maaro", "मारा": "maara", "कर": "kar", "करो": "karo", "किया": "kiya", "करना": "karna",
    "करते": "karte", "रहा": "raha", "रही": "rahi", "रहे": "rahe", "गया": "gaya", "गई": "gayi", "गयी": "gayi",
    "गए": "gaye", "गये": "gaye", "हुआ": "hua", "हुई": "hui", "हुए": "hue", "होगा": "hoga", "होगी": "hogi",
    "सकता": "sakta", "सकते": "sakte", "लगा": "laga", "लगता": "lagta", "दे": "de", "ले": "le", "दो": "do",
    "लो": "lo", "बोल": "bol", "बोलो": "bolo", "सुन": "sun", "सुनो": "suno", "पता": "pata", "मिल": "mil",
    "मिला": "mila", "बचा": "bacha", "बचाओ": "bachao", "उड़": "ud", "उड़ा": "uda", "गिर": "gir", "गिरा": "gira",
    # numbers, things
    "एक": "ek", "तीन": "teen", "चार": "chaar", "पांच": "paanch", "पाँच": "paanch", "दस": "das", "सौ": "sau",
    "पैसे": "paise", "पैसा": "paisa", "गाड़ी": "gaadi", "गाडी": "gaadi", "गोली": "goli", "बंदूक": "bandook",
    "घर": "ghar", "रास्ता": "raasta", "सड़क": "sadak", "पानी": "paani", "आग": "aag", "जान": "jaan", "बात": "baat",
    "काम": "kaam", "दिन": "din", "रात": "raat",
    # English words as Whisper writes them in Hindi script
    "पुलिस": "police", "कार": "car", "बाइक": "bike", "गेम": "game", "गेमिंग": "gaming", "मिशन": "mission",
    "लेवल": "level", "प्लेयर": "player", "गन": "gun", "किल": "kill", "स्टार": "star", "वांटेड": "wanted",
    "ओके": "okay", "सॉरी": "sorry", "प्लीज़": "please", "प्लीज": "please", "हेलो": "hello",
    "हेलीकॉप्टर": "helicopter", "प्लेन": "plane", "टाइम": "time", "स्पीड": "speed", "फास्ट": "fast",
    "क्रेज़ी": "crazy", "क्रेजी": "crazy", "जीटीए": "GTA", "सिक्स": "six", "सिटी": "city", "मैप": "map",
    "ग्राफिक्स": "graphics", "ग्राफ़िक्स": "graphics", "फिजिक्स": "physics", "ब्रिज": "bridge",
    "बिल्डिंग": "building", "चेज": "chase", "चेज़": "chase", "क्रैश": "crash", "बम": "bomb", "रॉकेट": "rocket",
    "सीन": "scene", "वीडियो": "video", "चैनल": "channel", "सब्सक्राइब": "subscribe", "लाइक": "like",
    "कमेंट": "comment", "शेयर": "share", "लाइव": "live", "स्ट्रीम": "stream", "बग": "bug", "ग्लिच": "glitch",
    "कैरेक्टर": "character", "ब्रो": "bro", "बॉस": "boss", "ओह": "oh", "वॉव": "wow", "नाइस": "nice",
    "गुड": "good", "बेस्ट": "best", "एक्शन": "action", "ट्रेलर": "trailer", "रियल": "real", "लाइफ": "life",
    "लाइफ़": "life", "सुपर": "super", "कूल": "cool", "पावर": "power", "हेल्थ": "health", "आर्मर": "armor",
    "स्टोरी": "story", "ऑनलाइन": "online", "सर्वर": "server", "टीम": "team", "फेल": "fail", "फ़ेल": "fail",
    "विन": "win", "लूज़": "lose", "लूज": "lose", "डेड": "dead", "ड्राइव": "drive", "ड्राइवर": "driver",
    "रेस": "race", "ट्रक": "truck", "टैंक": "tank", "बोट": "boat", "जेल": "jail", "कॉप": "cop", "कॉप्स": "cops",
    "स्नाइपर": "sniper", "हेडशॉट": "headshot", "बैंक": "bank", "रॉबरी": "robbery", "हाइस्ट": "heist",
    "ग्राउंड": "ground", "ओपन": "open", "वर्ल्ड": "world", "डिटेल": "detail", "डिटेल्स": "details",
    # names from the games
    "जेसन": "Jason", "लूसिया": "Lucia", "लुसिया": "Lucia", "वाइस": "Vice", "लियोनिडा": "Leonida",
    "रॉकस्टार": "Rockstar", "ट्रेवर": "Trevor", "फ्रैंकलिन": "Franklin", "माइकल": "Michael", "आर्थर": "Arthur",
}
COMMON = {unicodedata.normalize("NFC", k): v for k, v in COMMON.items()}

_WORD_RE = re.compile("[\u0900-\u0963\u0966-\u097f]+")  # Devanagari, without the full stop (danda)


@dataclass
class _Unit:
    c: str = ""  # consonant in Latin letters ("" for a vowel letter)
    v: str = ""  # vowel: "a" = the inherent vowel, "" = none (virama, or dropped)
    raw: str = ""  # the Devanagari consonant, for special clusters
    independent: bool = False
    nasal: bool = False
    visarga: bool = False
    literal: str | None = None  # anything that isn't a Devanagari letter


def _parse(word: str) -> list[_Unit]:
    units: list[_Unit] = []
    i = 0
    while i < len(word):
        ch = word[i]
        if ch in CONSONANTS:
            unit = _Unit(c=CONSONANTS[ch], v="a", raw=ch)
            if i + 1 < len(word) and word[i + 1] == NUKTA:
                unit.c = WITH_NUKTA.get(ch, unit.c)
                i += 1
            nxt = word[i + 1] if i + 1 < len(word) else ""
            if nxt == VIRAMA:
                unit.v = ""
                i += 1
            elif nxt in VOWEL_SIGNS:
                unit.v = VOWEL_SIGNS[nxt]
                i += 1
            units.append(unit)
        elif ch in INDEPENDENT_VOWELS:
            units.append(_Unit(v=INDEPENDENT_VOWELS[ch], independent=True))
        elif ch in (ANUSVARA, CHANDRABINDU):
            if units:
                units[-1].nasal = True
        elif ch == VISARGA:
            if units:
                units[-1].visarga = True
        elif ch == NUKTA or ch == "ऽ":
            pass
        else:
            units.append(_Unit(literal=DIGITS.get(ch, "." if ch in "।॥" else ch)))
        i += 1
    return units


def _has_vowel(u: _Unit) -> bool:
    return u.literal is None and u.v != ""


def _drop_silent_vowels(units: list[_Unit]) -> None:
    """Spoken Hindi drops the inherent 'a' at the end of a word and in V-C-a-C-V positions."""
    last = len(units) - 1
    if last >= 1:
        u, prev = units[last], units[last - 1]
        after_cluster = prev.c and prev.v == "" and prev.literal is None
        if u.c and u.v == "a" and not u.nasal and not u.visarga:
            # keep it after a cluster ending in y/r/l/v ("patra"), drop it otherwise ("dharm", "kar")
            if not (after_cluster and u.c in SEMIVOWELS):
                u.v = ""
    for k in range(last - 1, 0, -1):  # right to left, so one drop blocks the one before it
        u = units[k]
        if not (u.c and u.v == "a") or u.nasal or u.visarga:
            continue
        prev, nxt = units[k - 1], units[k + 1]
        if _has_vowel(prev) and nxt.c and _has_vowel(nxt):
            u.v = ""


def _vowel(u: _Unit, k: int, units: list[_Unit]) -> str:
    nxt = units[k + 1] if k + 1 < len(units) else None
    closed = (nxt is not None and nxt.c and nxt.v == "") or (nxt is None and u.nasal)
    if u.v == "A":
        return "aa" if closed or (u.independent and k == 0) else "a"
    if u.v == "I":
        return "ee" if closed else "i"
    if u.v == "U":
        return "oo" if closed else "u"
    return u.v


def _needs_y(before: str, after: str) -> bool:
    """Hinglish writes a 'y' between some vowel pairs: gayi, gaye, liye, aaye, soye (but bhai, hue)."""
    if before == "a":
        return after in ("i", "I", "e")
    return before in ("A", "i", "I", "o") and after == "e"


def _render(units: list[_Unit]) -> str:
    out: list[str] = []
    skip_consonant = False
    for k, u in enumerate(units):
        if u.literal is not None:
            out.append(u.literal)
            continue
        nxt = units[k + 1] if k + 1 < len(units) else None
        prev = units[k - 1] if k > 0 else None
        if u.independent and prev is not None and prev.literal is None and not prev.nasal and _needs_y(prev.v, u.v):
            out.append("y")
        if skip_consonant:
            skip_consonant = False
        elif u.raw == "ज" and u.v == "" and nxt is not None and nxt.raw == "ञ":
            out.append("gy")  # ज्ञ is said "gy" (gyaan)
            skip_consonant = True
        else:
            out.append(u.c)
        out.append(_vowel(u, k, units))
        if u.nasal:
            out.append("m" if nxt is not None and nxt.c in LABIALS else "n")
        if u.visarga:
            out.append("h")
    return "".join(out).replace("chchh", "cch")


def word_to_latin(word: str) -> str:
    word = unicodedata.normalize("NFC", word)
    if word in COMMON:
        return COMMON[word]
    units = _parse(word)
    _drop_silent_vowels(units)
    return _render(units)


def to_latin(text: str) -> str:
    """Convert every Devanagari word in `text`; everything else (English, digits, punctuation) stays."""
    text = unicodedata.normalize("NFC", text)
    return _WORD_RE.sub(lambda m: word_to_latin(m.group(0)), text).replace("।", ".").replace("॥", ".")
