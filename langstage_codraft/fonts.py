"""Fonts a document can use, and how a font found in an uploaded file maps onto them.

Bundled families ship with the editor (Latin subsets), so the editor and every export
render them offline. Any other Google Fonts family works too: the editor and the export
renderer load it from fonts.googleapis.com when ``web_fonts`` is on (the default).

``map_font`` turns a font name from a PDF or Word file ("ABCDEF+Calibri-Bold",
"TimesNewRomanPSMT") into an open family that looks the same: the family itself when
it is a Google Font, a metric-compatible stand-in for the common Office and Apple fonts,
or a sensible default for its category.
"""
from __future__ import annotations

import re
from typing import Any

# family -> category. Must match the @fontsource imports in frontend/src/fonts.ts.
BUNDLED: dict[str, str] = {
    "Inter": "sans", "Roboto Condensed": "sans", "Source Serif 4": "serif", "JetBrains Mono": "mono",
    "Raleway": "sans", "Lato": "sans", "Open Sans": "sans", "Montserrat": "sans", "Roboto": "sans",
    "Merriweather": "serif", "Lora": "serif", "Playfair Display": "serif", "EB Garamond": "serif",
    "Libre Baskerville": "serif", "Carlito": "sans", "Arimo": "sans", "Tinos": "serif",
}

# Popular Google Fonts families offered in the font picker (loaded on demand).
WEB: dict[str, str] = {
    "Poppins": "sans", "Source Sans 3": "sans", "Noto Sans": "sans", "Noto Serif": "serif",
    "IBM Plex Sans": "sans", "IBM Plex Serif": "serif", "IBM Plex Mono": "mono",
    "Work Sans": "sans", "Nunito Sans": "sans", "Nunito": "sans", "DM Sans": "sans",
    "DM Serif Display": "serif", "Manrope": "sans", "Space Grotesk": "sans", "Fira Sans": "sans",
    "Barlow": "sans", "Oswald": "sans", "Bebas Neue": "sans", "Josefin Sans": "sans", "Jost": "sans",
    "Libre Franklin": "sans", "Mulish": "sans", "Karla": "sans", "Rubik": "sans", "Cabin": "sans",
    "Hind": "sans", "PT Sans": "sans", "PT Serif": "serif", "Crimson Pro": "serif",
    "Crimson Text": "serif", "Cormorant Garamond": "serif", "Spectral": "serif", "Bitter": "serif",
    "Zilla Slab": "serif", "Roboto Slab": "serif", "Alegreya": "serif", "Vollkorn": "serif",
    "Domine": "serif", "Gelasio": "serif", "Caladea": "serif", "Cousine": "mono",
    "Inconsolata": "mono", "Source Code Pro": "mono", "Roboto Mono": "mono", "Space Mono": "mono",
    "Public Sans": "sans", "Atkinson Hyperlegible": "sans", "Plus Jakarta Sans": "sans",
    "Red Hat Display": "sans", "Red Hat Text": "sans", "Figtree": "sans", "Outfit": "sans",
    "Lexend": "sans", "Archivo": "sans", "Archivo Narrow": "sans", "Questrial": "sans",
    "Titillium Web": "sans", "Ubuntu": "sans", "Exo 2": "sans", "Heebo": "sans", "Assistant": "sans",
    "Overpass": "sans", "Sora": "sans", "Urbanist": "sans", "Abril Fatface": "serif",
    "Libre Bodoni": "serif", "Libre Caslon Text": "serif", "Old Standard TT": "serif",
    "Cardo": "serif", "Arvo": "serif", "Josefin Slab": "serif", "Quicksand": "sans", "Anton": "sans",
    "Saira": "sans", "Kanit": "sans", "Prompt": "sans", "Alegreya Sans": "sans",
}

GENERIC = {
    "sans": "system-ui, -apple-system, 'Segoe UI', Arial, sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "mono": "Consolas, 'Courier New', monospace",
}

# Common commercial fonts -> the open family that looks (and, where noted, measures) the same.
LOOKALIKES: dict[str, str] = {
    "calibri": "Carlito",               # metric-compatible
    "cambria": "Caladea",               # metric-compatible
    "arial": "Arimo",                   # metric-compatible
    "arial nova": "Arimo",
    "helvetica": "Arimo",               # metric-compatible with Arial, so with Helvetica's widths
    "helvetica neue": "Inter",
    "times": "Tinos",                   # metric-compatible
    "times new roman": "Tinos",
    "georgia": "Gelasio",               # metric-compatible
    "courier": "Cousine",
    "courier new": "Cousine",
    "consolas": "Inconsolata",
    "menlo": "JetBrains Mono",
    "monaco": "JetBrains Mono",
    "sf mono": "JetBrains Mono",
    "garamond": "EB Garamond",
    "adobe garamond": "EB Garamond",
    "adobe garamond pro": "EB Garamond",
    "palatino": "Crimson Pro",
    "palatino linotype": "Crimson Pro",
    "book antiqua": "Crimson Pro",
    "minion": "Crimson Pro",
    "minion pro": "Crimson Pro",
    "baskerville": "Libre Baskerville",
    "bodoni": "Libre Bodoni",
    "didot": "Playfair Display",
    "caslon": "Libre Caslon Text",
    "futura": "Jost",
    "gill sans": "Cabin",
    "gill sans mt": "Cabin",
    "avenir": "Nunito Sans",
    "avenir next": "Nunito Sans",
    "segoe ui": "Open Sans",
    "verdana": "Noto Sans",
    "tahoma": "Noto Sans",
    "trebuchet ms": "Fira Sans",
    "century gothic": "Questrial",
    "franklin gothic": "Libre Franklin",
    "franklin gothic book": "Libre Franklin",
    "myriad": "PT Sans",
    "myriad pro": "PT Sans",
    "frutiger": "Hind",
    "univers": "Roboto",
    "din": "Barlow",
    "din pro": "Barlow",
    "proxima nova": "Montserrat",
    "gotham": "Montserrat",
    "aptos": "Inter",
    "san francisco": "Inter",
    "sf pro": "Inter",
    "sf pro text": "Inter",
    "sf pro display": "Inter",
    "source sans pro": "Source Sans 3",
    "source serif pro": "Source Serif 4",
    "dejavu sans": "Noto Sans",
    "liberation sans": "Arimo",
    "liberation serif": "Tinos",
    "liberation mono": "Cousine",
}

DEFAULTS = {"sans": "Inter", "serif": "Source Serif 4", "mono": "JetBrains Mono"}

_KNOWN = {**{f: c for f, c in WEB.items()}, **BUNDLED}
_BY_KEY = {re.sub(r"[^a-z0-9]", "", f.lower()): f for f in _KNOWN}
_LOOK_BY_KEY = {re.sub(r"[^a-z0-9]", "", k): v for k, v in LOOKALIKES.items()}
_STYLE_WORDS = re.compile(
    r"(?i)[-,_ ]?(bold|semibold|semi|demibold|demi|extrabold|ultrabold|black|heavy|medium|light|"
    r"extralight|ultralight|thin|hairline|regular|book|roman|normal|italic|oblique|it|condensed|"
    r"cond|narrow|extended|display|text|caption|headline|subhead|ps|mt|std|lt|bt|mn|w\d+)\b")


def catalog() -> list[dict[str, Any]]:
    """The font picker's list: bundled families first, then popular web fonts."""
    rows = [{"family": f, "category": c, "bundled": True} for f, c in BUNDLED.items()]
    rows += [{"family": f, "category": c, "bundled": False} for f, c in sorted(WEB.items())]
    return rows


def css_stack(family: str, category: str = "sans") -> str:
    """A CSS font-family list for one family, with a generic fallback."""
    return f"'{family}', {GENERIC.get(category, GENERIC['sans'])}"


def family_of(stack: str) -> str:
    """First family named in a CSS font list."""
    first = (stack or "").split(",")[0].strip().strip("'\"")
    return first


def _words(raw: str) -> list[str]:
    """'ABCDEF+TimesNewRomanPS-BoldItalicMT' -> ['Times', 'New', 'Roman', 'PS', 'Bold', ...]."""
    name = re.sub(r"^[A-Z]{6}\+", "", (raw or "").strip().lstrip("/"))
    name = name.replace(",", " ").replace("-", " ").replace("_", " ")
    name = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-zA-Z])(?=\d)", " ", name)
    return name.split()


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def clean_name(raw: str) -> str:
    """The family part of a font name: the longest known prefix ('Times New Roman'),
    or the name without its style words ('Unknown Serif Face')."""
    words = _words(raw)
    for k in range(len(words), 0, -1):
        cand = " ".join(words[:k])
        if _key(cand) in _BY_KEY or _key(cand) in _LOOK_BY_KEY:
            return cand
    name = " ".join(words)
    prev = None
    while prev != name:
        prev = name
        name = _STYLE_WORDS.sub("", name).strip(" -_,")
    return re.sub(r"\s+", " ", name).strip()


def weight_of(raw: str) -> int:
    n = (raw or "").lower()
    for word, w in (("thin", 100), ("hairline", 100), ("extralight", 200), ("ultralight", 200),
                    ("light", 300), ("semibold", 600), ("demibold", 600), ("demi", 600),
                    ("extrabold", 800), ("ultrabold", 800), ("black", 900), ("heavy", 900),
                    ("bold", 700), ("medium", 500)):
        if word in n:
            return w
    return 400


def category_hint(name: str, *, serif: bool | None = None, mono: bool | None = None) -> str:
    n = name.lower()
    if mono or "mono" in n or "courier" in n or "code" in n:
        return "mono"
    if "sans" in n:
        return "sans"
    if serif or "serif" in n or "times" in n or "roman" in n or "garamond" in n:
        return "serif"
    return "sans"


def map_font(raw: str, *, serif: bool | None = None, mono: bool | None = None) -> dict[str, Any]:
    """Map a font name from a PDF or Word file to a family Codraft can render."""
    name = clean_name(raw)
    key = _key(name)
    if key in _BY_KEY:
        fam = _BY_KEY[key]
        return {"original": name, "family": fam, "category": _KNOWN[fam], "exact": True,
                "weight": weight_of(raw)}
    look = _LOOK_BY_KEY.get(key)
    if look:
        return {"original": name, "family": look, "category": _KNOWN.get(look, "sans"), "exact": False,
                "weight": weight_of(raw)}
    cat = category_hint(name, serif=serif, mono=mono)
    return {"original": name, "family": DEFAULTS[cat], "category": cat, "exact": False,
            "weight": weight_of(raw)}
