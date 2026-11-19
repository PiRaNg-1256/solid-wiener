"""Swear words: recognise them (English, Hindi in English letters, Hindi script) and mask them as f**k.

captions.srt keeps the real words; the mask is applied when the video is drawn, so the setting can be changed and
the clip rendered again. Titles, descriptions and hashtags never carry swear words: YouTube gives no ads to videos
with strong language in the title or thumbnail."""

from __future__ import annotations

import re
import unicodedata

MODES = ("mask", "strict", "show")  # strict also hides mild words (damn, hell, saala); show hides only slurs
BLEEP_MODES = ("off", "all")

STRONG, MILD, SLUR, CENSORED = "strong", "mild", "slur", "censored"

# word families: (spellings, prefixes, suffixes). Every letter may be stretched ("fuuuck"), so these are written
# in their normal spelling. Hindi words are listed in the ways people type them in English letters.
_HI_END = ["", "a", "e", "i", "o", "y", "ey", "on", "s"]
_FAMILIES: dict[str, list[tuple[list[str], list[str], list[str]]]] = {
    STRONG: [
        (["fuck", "fuk", "fck", "fk", "phuck", "fawk"], ["", "mother", "motha", "mutha", "muther", "cluster"],
         ["", "s", "ed", "er", "ers", "in", "ing", "ings", "ery", "face", "head", "hole", "wit", "tard", "boy", "off",
          "up", "ton"]),
        (["shit", "shite"], ["", "bull", "horse", "dip", "chicken", "ape", "jack"],
         ["", "s", "ty", "ting", "ted", "head", "heads", "hole", "show", "load", "face", "bag", "ter", "less", "post",
          "storm"]),
        (["bitch", "biatch"], [""], ["", "es", "ing", "y", "ass", "ez", "s"]),  # not "bich": Hinglish for "between"
        (["asshole", "arsehole", "asswipe", "asshat", "jackass", "dumbass", "smartass", "fatass", "dumass"], [""],
         ["", "s"]),
        (["dick"], [""], ["", "s", "head", "heads", "hole", "face", "wad"]),
        (["cock"], [""], ["", "s", "sucker", "suckers", "sucking"]),
        (["pussy", "pussies"], [""], [""]),
        (["cunt"], [""], ["", "s", "ing"]),
        (["bastard"], [""], ["", "s"]),
        (["whore", "slut", "wanker", "twat", "prick", "bollocks", "knobhead"], [""], ["", "s"]),
        # Hindi in English letters
        (["madarchod", "maderchod", "madharchod", "maadarchod", "madarchood", "maderchood", "madarjaat", "madarjat",
          "maderjaat", "maadarjaat"], [""], _HI_END),
        (["bhenchod", "behenchod", "bhanchod", "bahanchod", "behnchod", "benchod", "bhenchood", "bhencho",
          "behenchood", "bahenchod", "betichod"], [""], _HI_END),
        (["chutiya", "chutia", "chootiya", "chutiye", "chootiye", "chutiyapa", "chutiyap", "chutmarike", "chutmarika",
          "choot", "chut"], [""], [""]),
        (["bhosdike", "bhosdi", "bhosadike", "bhosdiwale", "bhosdiwala", "bhosda", "bhosri", "bhosriwale", "bhosdiki",
          "bhosadi", "bhosadiwale"], [""], [""]),
        (["gaandu", "gandu", "gaand", "gand", "gaandfat", "gandfat"], [""], [""]),
        (["lund", "lauda", "lavda", "lawda", "loda", "lode", "laude", "lavde", "lawde", "lodu", "laudu", "lodey",
          "lawdu", "lavdu"], [""], [""]),
        (["randi", "randwa", "randibaaz", "randikhana"], [""], [""]),
        (["haramzada", "haramzade", "haramzaade", "haramzaada", "haramkhor", "haraamkhor", "haraamzaada",
          "haraamzade", "haramjada", "haramjade"], [""], [""]),
        (["bakchod", "bakchodi"], [""], [""]),
        (["jhaant", "jhant", "jhaatu", "jhatu", "jhaantu", "jhaat"], [""], [""]),
        (["chudai", "chodu", "chudakkad", "kutiya"], [""], [""]),
    ],
    MILD: [
        (["damn", "dammit", "damnit", "goddamn", "goddamnit", "goddammit"], [""], ["", "ed"]),
        (["hell", "crap", "crappy", "piss", "pissed", "pissing", "bloody", "bugger", "arse", "ass", "asses"], [""],
         [""]),
        (["wtf", "stfu", "omfg", "lmfao"], [""], [""]),
        (["saala", "saale", "sala", "saali", "kamina", "kamine", "kaminey", "kamini", "kutta", "kutte", "harami",
          "haraami", "tatti"], [""], [""]),
    ],
    SLUR: [
        (["nigger", "nigga", "niggah", "nigguh"], [""], ["", "s", "z"]),
        (["faggot", "fag"], [""], ["", "s"]),
        (["retard"], [""], ["", "s", "ed"]),
        (["tranny", "trannies", "chink", "spic", "kike", "paki"], [""], ["", "s"]),
        (["chamar", "bhangi", "katua", "katwa"], [""], [""]),
    ],
}

# Hindi script (after dropping the nukta dot and writing ँ as ं). English swears written in Hindi letters too.
_DEVANAGARI = {
    STRONG: """मादरचोद मादरजात भेनचोद भेंचोद बहनचोद बहेनचोद बेहनचोद बहिनचोद चूतिया चुतिया चूतिये चुतिये चूत भोसडीके
               भोसडी भोसडा भोसडीवाले गांडू गाण्डू गांड गाण्ड लौडा लोडा लौडे लौडू लंड लण्ड रंडी रण्डी हरामजादा हरामजादे
               हरामखोर बकचोद बकचोदी चुदाई झांट झाटू कुतिया फक फकिंग फक्ड मदरफकर शिट""",
    MILD: "साला साले साली कमीना कमीने कुत्ता कुत्ते हरामी टट्टी डैम",
}

_EUPHEMISMS = {  # softer words a transcriber may write instead of the swear actually said
    "freaking", "freakin", "fricking", "frickin", "frigging", "friggin", "frick", "frig", "effing", "fudge", "fudging",
    "darn", "dang", "heck", "f-ing", "f-word", "s-word", "a-hole", "b-word", "bleep", "beep", "bleeping", "beeping",
}

_CENSOR_TOKEN = re.compile(r"^[\[(]?\s*(?:_{2,}|\*{2,}|#[#$@%!&*]{2,}|[$@%!&*#]{4,}|expletive|censored|bleep(?:ed)?|beep)\s*[\])]?$",
                           re.I)
_STARRED = re.compile(r"^[^\W\d_]+\*+[^\W\d_]*$|^\*+[^\W\d_]+$")  # f***, sh*t, f*cking, ***k


def _stretch(word: str) -> str:
    """'fuck' -> 'fu+ck+': vowels and the last letter may be repeated ('fuuuck', 'fuckkk'). Other letters may not,
    or 'chut' would also match the everyday Hinglish 'chhut' ('chhut gaya')."""
    return "".join(re.escape(ch) + ("+" if ch in "aeiouy" or i == len(word) - 1 else "")
                   for i, ch in enumerate(word))


def _compile(families) -> re.Pattern:
    parts = []
    for spellings, prefixes, suffixes in families:
        pre = "|".join(_stretch(p) for p in prefixes if p)
        base = "|".join(_stretch(s) for s in spellings)
        suf = "|".join(_stretch(s) for s in suffixes if s)
        parts.append(f"(?:(?:{pre})?)(?:{base})(?:(?:{suf})?)" if pre else f"(?:{base})(?:(?:{suf})?)")
    return re.compile("^(?:" + "|".join(parts) + ")$")


_LATIN = {kind: _compile(fams) for kind, fams in _FAMILIES.items()}


def _deva_norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("़", "").replace("ँ", "ं")


_DEVA_SETS = {kind: {_deva_norm(w) for w in words.split()} for kind, words in _DEVANAGARI.items()}


def _romanised_extras() -> dict[str, set[str]]:
    """The Hindi-script swears as this tool writes them in English letters, so converted captions match too."""
    try:
        from .hinglish import to_latin
    except Exception:  # pragma: no cover - the converter is part of the package
        return {}
    extras: dict[str, set[str]] = {}
    for kind, words in _DEVANAGARI.items():
        extras[kind] = {re.sub(r"[^a-z]", "", to_latin(w).lower()) for w in words.split()} - _NOT_SWEARS - {""}
    return extras


_NOT_SWEARS = {"land", "sale", "daim"}  # English words the conversion of लंड / साले / डैम happens to spell
_LATIN_EXTRAS = _romanised_extras()


def _is_edge(ch: str) -> bool:
    """Punctuation or a symbol around a word. Not '*' (part of f**k), and not Hindi vowel signs: they are marks,
    which a regex \\w doesn't count as letters."""
    if ch == "*":
        return False
    cat = unicodedata.category(ch)
    return cat[0] in "PSZ" or cat == "Cc"


def split_word(text: str) -> tuple[str, str, str]:
    """('"', 'Fuck', '!') for '"Fuck!'."""
    i, j = 0, len(text)
    while i < j and _is_edge(text[i]):
        i += 1
    while j > i and _is_edge(text[j - 1]):
        j -= 1
    return text[:i], text[i:j], text[j:]


def classify(text: str) -> str | None:
    """'strong', 'mild', 'slur', 'censored' (already hidden, like f*** or [ __ ]) or None for a normal word."""
    raw = text.strip()
    if not raw:
        return None
    if _CENSOR_TOKEN.match(raw):
        return CENSORED
    _, core, _ = split_word(raw)
    if not core:
        return None
    if _STARRED.match(core):
        return CENSORED
    if re.search(r"[ऀ-ॿ]", core):
        norm = _deva_norm(core)
        for kind in (SLUR, STRONG, MILD):
            if norm in _DEVA_SETS.get(kind, ()):
                return kind
        return None
    low = core.lower().replace("’", "'")
    low = low[:-1] if low.endswith("'") else low  # fuckin' -> fuckin
    letters = re.sub(r"[^a-z]", "", low)
    if not letters or len(letters) != len(re.sub(r"[^\w]", "", low)):  # digits or other scripts mixed in
        return None
    for kind in (SLUR, STRONG, MILD):
        if _LATIN[kind].match(letters) or letters in _LATIN_EXTRAS.get(kind, ()):
            return kind
    return None


def hides(kind: str | None, mode: str) -> bool:
    """Is a word of this kind masked on screen in this mode?"""
    if kind is None:
        return False
    if kind in (SLUR, CENSORED):
        return True
    if mode == "show":
        return False
    return kind == STRONG or (kind == MILD and mode == "strict")


def _clusters(text: str) -> list[str]:
    """Letters as people see them: a Hindi letter keeps its vowel signs and joined consonants."""
    out: list[str] = []
    for ch in text:
        if out and (unicodedata.category(ch).startswith("M") or out[-1].endswith("्")):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def mask_core(core: str) -> str:
    """'fucking' -> 'f*****g', 'भेनचोद' -> 'भे**द'. Words of two letters or less are left alone."""
    letters = _clusters(core)
    if len(letters) <= 2:
        return core
    return letters[0] + "*" * (len(letters) - 2) + letters[-1]


def mask_word(text: str, mode: str = "mask") -> str:
    """One caption word, punctuation kept: 'Fuck!' -> 'F**k!'. Already-hidden words become **** if unreadable."""
    kind = classify(text)
    if not hides(kind, mode):
        return text
    lead, core, trail = split_word(text.strip())
    if kind == CENSORED:
        if _STARRED.match(core):
            return text.strip()
        return "****"
    return lead + mask_core(core) + trail


def readable(text: str) -> str:
    """Whisper writes an unheard swear as '[ __ ]' (like YouTube's auto-captions): show it as ****."""
    if classify(text) == CENSORED and not _STARRED.match(split_word(text.strip())[1]):
        return "****"
    return text


def mask_text(text: str, mode: str = "mask") -> str:
    """Mask every swear in a line of text, keeping spaces and line breaks ('|' in hooks counts as a break)."""
    return re.sub(r"[^\s|]+", lambda m: mask_word(m.group(0), mode), text)


def has_swear(text: str, mode: str = "mask") -> bool:
    """Does the text contain a word that this mode hides (or any swear for titles: use mode='strict')?"""
    return any(hides(classify(w), mode) for w in re.split(r"[\s|]+", text) if w)


_ROOTS = ("fuck", "shit", "bitch", "cunt", "nigg", "fagg", "retard", "chod", "chutiy", "bhosd", "gaand", "lund",
          "lauda", "lavda", "randi", "pussy", "dick", "whore", "slut", "asshole")


def has_swear_root(text: str) -> bool:
    """For hashtags (#wtfbro, #fuckthis): any swear inside the letters."""
    letters = re.sub(r"[^a-z]", "", text.lower())
    return any(root in letters for root in _ROOTS)


def drop_swears(text: str) -> str:
    """Remove swear words entirely (for hashtags and similar), tidying the spaces."""
    kept = [w for w in text.split() if classify(w) is None]
    return " ".join(kept)


def is_softened(text: str) -> bool:
    """A censored or softened spelling that may stand for a swear the local speech model heard in full."""
    _, core, _ = split_word(text.strip())
    return classify(text) == CENSORED or core.lower() in _EUPHEMISMS


def restore(words: list, local: list, to_text=None) -> int:
    """Put back swears that the better transcript censored or softened ('f***', '[ __ ]', 'freaking'), using the
    local speech model's word said at the same moment, if that word is a swear starting with the same letter.
    words/local: items with .start, .end, .text (changed in place). to_text converts a local word's text (for
    example Hindi script to English letters). Returns how many words were put back."""
    fixed = 0
    for w in words:
        if not is_softened(w.text):
            continue
        lead, core, trail = split_word(w.text.strip())
        first = core[:1].lower() if core[:1].isalpha() else ""
        best, best_overlap = None, 0.0
        for lw in local:
            overlap = min(w.end, lw.end) - max(w.start, lw.start)
            if overlap <= 0 or classify(lw.text) not in (STRONG, SLUR):
                continue
            text = to_text(lw.text) if to_text else lw.text
            _, lcore, _ = split_word(text.strip())
            if first and lcore[:1].lower() != first:
                continue
            if overlap > best_overlap:
                best, best_overlap = lcore, overlap
        if best:
            w.text = (lead if not lead.startswith("[") else "") + best + (trail if not trail.endswith("]") else "")
            fixed += 1
    return fixed


def bleep_spans(words: list, mode: str, pad: float = 0.04) -> list[tuple[float, float]]:
    """Times to beep: strong swears, slurs and already-hidden words (words relative to the clip start)."""
    spans: list[tuple[float, float]] = []
    for w in words:
        if classify(w.text) in (STRONG, SLUR, CENSORED) or (mode == "strict" and classify(w.text) == MILD):
            s, e = max(0.0, w.start - pad), w.end + pad
            if spans and s <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], e))
            else:
                spans.append((s, e))
    return spans
