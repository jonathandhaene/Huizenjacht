"""
NLP normalization utilities for property data.

Provides multilingual text extraction and normalization for Belgian real-estate
listings (Dutch, French, English).  All functions are pure — no network I/O or
external dependencies beyond the standard library.

Usage example::

    from agents.scrapers.nlp_normalizer import (
        extract_bedrooms, extract_price, extract_land_area,
        classify_property_type, deduplicate_properties,
    )

    bedrooms = extract_bedrooms("Ruime woning met 4 slaapkamers en grote tuin")
    # → 4
"""
from __future__ import annotations

import re
from typing import List, Optional

from models.property import Property, PropertyType

# ── Bedroom extraction ────────────────────────────────────────────────────────

# Ordered from most-specific to least-specific to avoid greedy mis-matches.
_BEDROOM_PATTERNS = [
    # "3 slaapkamers" / "3 slaapk" / "3 bedrooms" / "3 chambres" / "3 ch."
    r"(\d+)\s*(?:slaapkamers?|slaapk\.?|bedrooms?|chambres?|ch\.\s|slpk\.?)",
    # "3 bed" / "3 beds" (abbreviated English, e.g. Realo cards)
    r"(\d+)\s+beds?\b",
    # Keyword-first: "slaapkamers: 3" / "bedrooms: 3"
    r"(?:slaapkamers?|bedrooms?|chambres?)\s*[:\-]\s*(\d+)",
    # "3 br" abbreviation
    r"(\d+)\s*br\b",
]


def extract_bedrooms(text: str) -> Optional[int]:
    """Return bedroom count extracted from multilingual text, or None.

    Handles Dutch (slaapkamers), French (chambres), and English (bedrooms).
    """
    lower = text.lower()
    for pattern in _BEDROOM_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            # The capturing group is always the digit group
            digit_group = next(g for g in m.groups() if g is not None)
            try:
                val = int(digit_group)
                if 0 < val < 20:  # Sanity check — no property has 20+ bedrooms
                    return val
            except (ValueError, TypeError):
                continue
    return None


# ── Price extraction ──────────────────────────────────────────────────────────

_PRICE_PATTERNS = [
    # "€ 450.000" / "€ 450,000" / "€ 450 000"
    r"€\s*([\d]{2,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?)",
    # Raw 5–7 digit number directly after €, no separators: "€450000"
    r"€\s*(\d{5,7})\b",
    # "450.000 €" / "450000€" / "450 000 €"
    r"([\d]{2,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?)\s*€",
    # "450.000 EUR"
    r"([\d]{2,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?)\s*EUR\b",
    # Shorthand: "450K" / "450k"
    r"€?\s*([\d]{2,4})\s*[kK]\b",
]


def _normalize_numeric(raw: str) -> str:
    """Normalise a raw numeric string from a Belgian/Dutch real-estate text.

    Handles the two common notations:
    - Dutch/Belgian: ``12.000`` = twelve thousand (dot is thousands separator)
    - English/SI:    ``12,000`` = twelve thousand (comma is thousands separator)

    Rules applied in order:
    1. Strip spaces (used as thousands separators in French-BE: ``450 000``).
    2. If the string ends with exactly 3 digits after the *last* separator
       → the separator is a thousands separator → remove it.
    3. If the string ends with 1–2 digits after the *last* separator
       → the separator is a decimal point → replace with ``"."``.
    4. Remove all remaining ``","`` and ``"."`` (used as thousands seps).
    """
    cleaned = re.sub(r"\s", "", raw)
    # Detect the last separator
    m = re.search(r"[,.](\d+)$", cleaned)
    if m:
        decimal_digits = len(m.group(1))
        if decimal_digits == 3:
            # Thousands separator — remove it and all remaining seps
            cleaned = re.sub(r"[,.]", "", cleaned)
        else:
            # Decimal separator — normalise to "." and remove other seps
            prefix = cleaned[: m.start()]
            suffix = m.group(1)
            prefix = re.sub(r"[,.]", "", prefix)
            cleaned = f"{prefix}.{suffix}"
    else:
        # No separator at all — strip any stray punctuation
        cleaned = re.sub(r"[,.]", "", cleaned)
    return cleaned


def extract_price(text: str) -> Optional[float]:
    """Return price in EUR extracted from text, or None.

    Handles formats like '€ 450.000', '450,000 €', '450 000 EUR', '€450000',
    '450K'.
    """
    for idx, pattern in enumerate(_PRICE_PATTERNS):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            # Shorthand "450K" → 450_000
            if idx == len(_PRICE_PATTERNS) - 1:
                try:
                    return float(raw) * 1_000
                except ValueError:
                    continue
            cleaned = _normalize_numeric(raw)
            try:
                val = float(cleaned)
                if val > 1_000:  # Sanity check: property prices > €1 000
                    return val
            except ValueError:
                continue
    return None


# ── Area extraction ───────────────────────────────────────────────────────────

# Area values near these words describe secondary spaces, not habitable floor
# area — used by the generic extract_living_area fallback to skip false hits.
_NOISE_AREA_KEYWORDS: frozenset = frozenset({
    "garage", "carport", "berging", "kelder", "kelderverdieping",
    "terras", "balkon", "oprit", "parking",
    "cave", "terrasse", "balcon", "stationnement",
    "zolder", "bijgebouw",
})

def _extract_area_with_keywords(text: str, keywords: List[str]) -> Optional[float]:
    """Generic area extractor anchored to given Dutch/French/English keywords."""
    lower = text.lower()
    kw_alt = "|".join(re.escape(k) for k in keywords)

    # Pattern 1: keyword then area value  →  "perceel: 12.000 m²"
    # Allow any non-digit characters between keyword and value to handle
    # cases like "bewoonbare oppervlakte: 250 m²" where the keyword stem
    # "bewoonbaar" is followed by extra suffix chars before the colon.
    # Allow optional whitespace between "m" and "²/2" because some sites
    # (e.g. Realo) render the superscript as a separate text node, which
    # BeautifulSoup's `get_text(' ')` joins with a space → "215m 2".
    kw_first = re.search(
        rf"(?:{kw_alt})[^\d\n]{{0,30}}?([\d][\d.,\s]*)\s*(?:m\s?[²2]|ha)\b",
        lower,
    )
    # Pattern 2: area value then keyword  →  "12.000 m² perceel"
    val_first = re.search(
        rf"([\d][\d.,\s]*)\s*(?:m\s?[²2]|ha)\b[^\n]{{0,20}}?(?:{kw_alt})",
        lower,
    )

    for m in filter(None, [kw_first, val_first]):
        raw = m.group(1).strip()
        context = m.group(0)
        is_ha = context.endswith("ha")
        cleaned = _normalize_numeric(raw)
        try:
            val = float(cleaned)
            if is_ha:
                val *= 10_000  # convert ha → m²
            if val > 0:
                return val
        except ValueError:
            continue
    return None


def extract_land_area(text: str) -> Optional[float]:
    """Return land/plot area in m² extracted from multilingual text, or None."""
    keywords = [
        "perceel", "perceeloppervlak", "grond", "tuin",
        "terrain", "jardin", "plot", "land area", "grondoppervlak",
    ]
    result = _extract_area_with_keywords(text, keywords)
    if result is not None:
        return result
    # Generic fallback: find all "NNN m²" occurrences and return the largest
    # (land is almost always the biggest area figure on a card)
    matches = re.findall(r"([\d][\d.,\s]*)\s*m\s?[²2]", text.lower())
    values: List[float] = []
    for raw in matches:
        try:
            v = float(_normalize_numeric(raw.strip()))
            if v > 500:  # Ignore tiny areas — land ≥ 500 m²
                values.append(v)
        except ValueError:
            pass
    return max(values) if values else None


def extract_living_area(text: str) -> Optional[float]:
    """Return living/habitable area in m² extracted from multilingual text, or None."""
    keywords = [
        "bewoonbaar", "bewoonbare opp", "leefruimte", "woonoppervlakte",
        "habitable", "surface habitable", "living area", "netto bewoonbaar",
    ]
    result = _extract_area_with_keywords(text, keywords)
    if result is not None:
        return result
    # Generic fallback: scan all m² figures and pick the largest plausible
    # habitable-area value (50–800 m²) that is not accompanied by noise
    # keywords indicating a secondary space (garage, terrace, etc.).
    _lower = text.lower()
    values: List[float] = []
    for m in re.finditer(r"([\d][\d.,\s]*)\s*m\s?[²2]", _lower):
        try:
            v = float(_normalize_numeric(m.group(1).strip()))
        except ValueError:
            continue
        if not (50 <= v <= 800):
            continue
        window = _lower[max(0, m.start() - 60) : m.end() + 30]
        if any(kw in window for kw in _NOISE_AREA_KEYWORDS):
            continue
        values.append(v)
    return max(values) if values else None



def sanitize_areas(land_area, living_area):
    """Cross-validate and correct extracted land_area / living_area values.

    Rules:
    - Both set and land_area < living_area  ->  swap them (plot is always
      bigger than habitable area; values were likely extracted in wrong order).
    - Only living_area set and value > 1000 m2  ->  reassign as land_area
      (implausibly large for habitable surface; almost certainly a plot size).
    """
    if land_area is not None and living_area is not None:
        if land_area < living_area:
            land_area, living_area = living_area, land_area
    elif living_area is not None and living_area > 1_000:
        land_area = living_area
        living_area = None
    return land_area, living_area

# ── Property type classification ──────────────────────────────────────────────

_TYPE_KEYWORDS: dict[PropertyType, List[str]] = {
    PropertyType.FARM: [
        "hoeve", "boerderij", "farm", "ferme", "country-cottage",
        "country cottage", "landgoed", "weiland", "stal", "schuur",
        "agrarisch", "ruraal",
    ],
    PropertyType.VILLA: [
        "villa", "exceptional", "uitzonderlijk", "manoir", "kasteel",
        "château", "chateau",
    ],
    PropertyType.LAND: [
        "bouwgrond", "perceel te koop", "grond te koop",
        "terrain à bâtir", "land for sale",
    ],
}


# ── Postal code extraction ────────────────────────────────────────────────────

# Belgian postal codes are exactly 4 digits (1000–9999).
_POSTAL_CODE_PATTERN = re.compile(r"\b([1-9]\d{3})\b")


def extract_postal_code(*texts: Optional[str]) -> Optional[str]:
    """Return the first Belgian-style 4-digit postal code found in any of the
    given text fragments.

    >>> extract_postal_code("Rue saint-Vincent 36, 1457 Walhain-Saint-Paul")
    '1457'
    >>> extract_postal_code(None, "Brakel 9660")
    '9660'
    >>> extract_postal_code("No code here") is None
    True
    """
    for text in texts:
        if not text:
            continue
        match = _POSTAL_CODE_PATTERN.search(str(text))
        if match:
            return match.group(1)
    return None


def classify_property_type(text: str) -> PropertyType:
    """Classify property type from free-form text.

    Returns the first matching type or ``PropertyType.HOUSE`` as default.
    """
    lower = text.lower()
    for prop_type, keywords in _TYPE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return prop_type
    return PropertyType.HOUSE


# ── Cross-source deduplication ────────────────────────────────────────────────

def _normalize_for_comparison(s: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace."""
    s = s.lower()
    s = re.sub(r"[,.\-/\\]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _significant_title_words(title: str) -> frozenset[str]:
    """Extract meaningful words from a title (strips stop-words and single chars)."""
    stop = {
        "te", "koop", "à", "vendre", "for", "sale", "woning", "huis",
        "maison", "house", "met", "avec", "with", "in", "de", "het",
        "een", "–", "-", "&",
    }
    words = _normalize_for_comparison(title).split()
    return frozenset(w for w in words if len(w) > 2 and w not in stop)


def _are_duplicates(a: Property, b: Property) -> bool:
    """Return True if two properties from *different* sources look like the same listing.

    Two listings are considered duplicates when:
    - They share the same postal code AND
    - Their prices are within 1 % of each other AND
    - Their normalised addresses match OR their titles share > 60 % of words.
    """
    # Must have a postal code and price to compare meaningfully
    if not a.postal_code or not b.postal_code:
        return False
    if a.postal_code != b.postal_code:
        return False
    if not a.price or not b.price:
        return False
    # Price within 1 %
    if abs(a.price - b.price) / max(a.price, b.price) > 0.01:
        return False

    # Address similarity
    addr_a = _normalize_for_comparison(a.address or a.municipality or "")
    addr_b = _normalize_for_comparison(b.address or b.municipality or "")
    if addr_a and addr_b and addr_a == addr_b:
        return True

    # Title word overlap
    words_a = _significant_title_words(a.title)
    words_b = _significant_title_words(b.title)
    min_words = min(len(words_a), len(words_b))
    if words_a and words_b and min_words > 0:
        overlap = len(words_a & words_b) / min_words
        if overlap >= 0.6:
            return True

    return False


def deduplicate_properties(properties: List[Property]) -> List[Property]:
    """Remove near-duplicate listings that appear across multiple sources.

    Keeps the first occurrence (by list order, i.e. the scraper that found
    it first).  Only compares properties from *different* sources to avoid
    accidentally suppressing genuine separate listings from the same platform.

    This is a cross-source deduplication step; the existing seen-cache
    deduplication (per-run) is handled separately by the orchestrator.
    """
    unique: List[Property] = []
    for prop in properties:
        is_dup = False
        for seen in unique:
            if seen.source != prop.source and _are_duplicates(seen, prop):
                is_dup = True
                break
        if not is_dup:
            unique.append(prop)
    return unique
