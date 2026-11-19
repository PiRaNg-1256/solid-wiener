"""Hindi in English letters."""

from __future__ import annotations

import pytest

from clipper.hinglish import to_latin, word_to_latin
from clipper.pipeline import caption_words
from clipper.transcribe import Transcript, Word


@pytest.mark.parametrize(
    "hindi, latin",
    [
        # dictionary: everyday words and English words written in Hindi script
        ("है", "hai"), ("नहीं", "nahi"), ("क्यों", "kyun"), ("में", "mein"), ("यार", "yaar"),
        ("पुलिस", "police"), ("मिशन", "mission"), ("हेलीकॉप्टर", "helicopter"), ("जेसन", "Jason"),
        # rules: the dropped vowel of spoken Hindi
        ("समझना", "samajhna"), ("लड़का", "ladka"), ("जानवर", "jaanvar"), ("मतलब", "matlab"),
        ("बचपन", "bachpan"), ("रहना", "rahna"), ("कमरा", "kamra"), ("सबसे", "sabse"), ("समय", "samay"),
        ("अलग", "alag"), ("बहन", "bahan"), ("पत्र", "patra"), ("धर्म", "dharm"), ("दुश्मन", "dushman"),
        # rules: nasal sounds, clusters, long vowels, joining vowels
        ("ठंडा", "thanda"), ("संबंध", "sambandh"), ("इंसान", "insaan"), ("ज्ञान", "gyaan"),
        ("दूध", "doodh"), ("खाना", "khana"), ("गोलियां", "goliyaan"), ("इसलिए", "isliye"),
        ("नई", "nayi"), ("हवाई", "havai"), ("कमाई", "kamai"), ("हुए", "hue"), ("आए", "aaye"),
    ],
)
def test_words(hindi, latin):
    assert word_to_latin(hindi) == latin


def test_sentences_keep_punctuation_and_english():
    assert to_latin("भाई ये क्या हो गया") == "bhai ye kya ho gaya"
    assert to_latin("पुलिस आ गई, भागो!") == "police aa gayi, bhaago!"
    assert to_latin("मिशन फेल हो गया।") == "mission fail ho gaya."
    assert to_latin("GTA 6 में ये बंदा उड़ गया") == "GTA 6 mein ye banda ud gaya"
    assert to_latin("२ स्टार") == "2 star"


def test_both_ways_of_writing_nukta_letters_match():
    precomposed = chr(0x095B) + "्यादा"  # ज़ as one character
    combined = "ज" + chr(0x093C) + "्यादा"  # ज + nukta sign
    assert word_to_latin(precomposed) == word_to_latin(combined) == "zyada"


def test_hindi_captions_use_english_letters_unless_devanagari_is_chosen():
    hindi = Transcript(language="hi", words=[Word(0, 0.5, "भाई"), Word(0.6, 1.0, "देख")])
    script, words = caption_words(hindi.words, hindi, "latin")
    assert script == "latin" and [w.text for w in words] == ["bhai", "dekh"]
    script, words = caption_words(hindi.words, hindi, "devanagari")
    assert script == "devanagari" and words is hindi.words
    english = Transcript(language="en", words=[Word(0, 0.5, "hello")])
    assert caption_words(english.words, english, "latin") == ("", english.words)
    translated = Transcript(language="hi", words=[Word(0, 0.5, "brother")], translated=True)
    assert caption_words(translated.words, translated, "latin") == ("", translated.words)
