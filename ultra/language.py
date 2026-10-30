"""Language detection, including Algerian Darija written in Arabic or Latin letters."""
from __future__ import annotations

import re

DARIJA_WORDS = {
    # Arabic script
    "واش", "كاش", "بزاف", "درك", "دروك", "ديرلي", "دير", "نحب", "حاب", "راني", "راك", "راه", "راهي",
    "كيفاش", "وين", "علاش", "ماشي", "شحال", "برك", "خلاص", "ياسر", "هدرة", "صحا", "بصح", "زعما",
    "نتاع", "تاع", "ديال", "مليح", "شوية", "شويا", "قاع", "ڨاع", "نحي", "حط", "جيب", "بلاك", "واحد",
    "عيط", "سقسي", "تفكر", "تتفكر", "عقل", "نسى", "كمل", "بدا", "خدم", "يخدم",
    # Latin script (Arabizi)
    "wesh", "wach", "rani", "rak", "rah", "bezaf", "bzaf", "dork", "drok", "kifach", "win", "3lach",
    "machi", "chhal", "berk", "khlas", "ya3tik", "sahit", "saha", "nta3", "ta3", "dir", "dirli",
    "n7ab", "nheb", "habit", "ga3", "chwiya", "mlih", "jib", "khdem",
}

ARABIC_RE = re.compile(r"[؀-ۿ]")
FRENCH_HINTS = {"le", "la", "les", "est", "une", "des", "pour", "avec", "je", "tu", "vous", "bonjour", "merci", "fais"}


def detect(text: str) -> str:
    """Return 'ar-dz' (Darija), 'ar', 'fr' or 'en'."""
    t = (text or "").lower()
    words = set(re.findall(r"[\w']+", t))
    darija = len(words & DARIJA_WORDS)
    arabic_chars = len(ARABIC_RE.findall(t))
    if arabic_chars > len(t) * 0.3:
        return "ar-dz" if darija else "ar"
    if darija >= 1 and re.search(r"\b\w*[379]\w*\b", t) or darija >= 2:
        return "ar-dz"
    if len(words & FRENCH_HINTS) >= 2 or re.search(r"[éèêàçù]", t):
        return "fr"
    return "en"


def reply_language(detected: str) -> str:
    """Language code used for the UI strings of a reply."""
    return "ar" if detected.startswith("ar") else ("fr" if detected == "fr" else "en")


LANG_NAMES = {"ar-dz": "Algerian Darija (reply in simple Arabic the user understands; Darija is welcome)",
              "ar": "Modern Standard Arabic", "fr": "French", "en": "English"}
