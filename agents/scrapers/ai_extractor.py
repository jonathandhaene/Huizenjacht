"""
AI-powered property data extraction.

Uses OpenAI GPT to extract structured property listings from raw HTML or
unstructured text when static CSS selectors and JSON parsers fail.  Also
provides AI-assisted error analysis to help diagnose scraping failures.

Design principles
-----------------
* **Fallback only** — only called when standard parsers return nothing.
* **Fail-safe** — returns an empty list and logs a warning when the API is
  unavailable or the response cannot be parsed as JSON.
* **Cheap model by default** — uses ``gpt-4o-mini`` unless ``OPENAI_MODEL``
  is explicitly configured; the extraction prompt is compact enough that the
  mini model gives reliable results at a fraction of the cost.

Usage example::

    extractor = AIPropertyExtractor()
    if extractor.available:
        props = extractor.extract_from_html(html, source_url, "realo")
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from models.property import Property, PropertyType

logger = logging.getLogger(__name__)

# ── System prompts ────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM_PROMPT = """\
Je bent een data-extractie-assistent voor Belgische vastgoedwebsites.
Je krijgt ruwe HTML of tekst van een vastgoedzoekpagina en moet alle
woningvermeldingen extraheren die je kunt vinden.

Geef ENKEL een JSON-array terug (geen extra tekst of uitleg).
Elk element heeft de volgende velden (laat velden weg als niet aanwezig):
{
  "title": "<string>",
  "price": <number or null>,
  "address": "<string or null>",
  "postal_code": "<4-digit string or null>",
  "municipality": "<string or null>",
  "bedrooms": <number or null>,
  "land_area": <number in m² or null>,
  "living_area": <number in m² or null>,
  "description": "<string or null>",
  "source_url": "<string or null>",
  "property_type": "<house|farm|villa|land|other>"
}

Als er geen vermeldingen zijn, geef dan een lege array [].
"""

_ERROR_ANALYSIS_SYSTEM_PROMPT = """\
Je bent een web-scraping diagnose-assistent.
Analyseer de gegeven HTTP-fout bij het scrapen van een Belgische vastgoedwebsite
en geef concrete suggesties om de fout te omzeilen.

Geef je antwoord als JSON (geen extra tekst):
{
  "error_type": "<string>",
  "likely_cause": "<string>",
  "suggestions": ["<string>", ...],
  "retry_strategy": "<none|cloudflare_bypass|playwright|wait_and_retry>"
}
"""


class AIPropertyExtractor:
    """
    Extracts property listings from raw HTML / text via OpenAI GPT.

    Falls back gracefully when:
    - ``OPENAI_API_KEY`` is not configured
    - The OpenAI API call fails for any reason
    - The response cannot be parsed as a JSON array
    """

    # Truncate input to avoid exceeding token limits (~15 K chars ≈ ~4 K tokens)
    _MAX_INPUT_CHARS = 15_000

    def __init__(self) -> None:
        from agents.llm_client import get_chat_client

        try:
            self._client, self._model, backend = get_chat_client(prefer_cheap=True)
            if backend == "github":
                logger.info("[ai_extractor] using GitHub Models (%s)", self._model)
            elif backend == "openai":
                logger.info("[ai_extractor] using OpenAI (%s)", self._model)
        except Exception as exc:
            self._client = None
            self._model = ""
            logger.debug("[ai_extractor] Could not initialise LLM client: %s", exc)

    @property
    def available(self) -> bool:
        """Return True if the extractor has a working OpenAI client."""
        return self._client is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_html(
        self,
        html: str,
        source_url: str,
        source_name: str,
    ) -> List[Property]:
        """Extract property listings from raw HTML using GPT.

        Strips ``<script>`` / ``<style>`` blocks first to reduce noise and
        keep the input well within token limits.

        Parameters
        ----------
        html:
            Raw HTML of a search-results page.
        source_url:
            Canonical URL of the page (used as fallback ``source_url`` on
            extracted properties and to give the model context).
        source_name:
            Scraper name written into ``Property.source``.

        Returns
        -------
        List[Property]
            Extracted properties, possibly empty if nothing was found.
        """
        if not self._client:
            return []

        clean = _strip_html_noise(html)[: self._MAX_INPUT_CHARS]
        raw_items = self._call_extraction_api(clean, source_url)
        return self._parse_raw_items(raw_items, source_url, source_name)

    def extract_from_text(
        self,
        text: str,
        source_url: str,
        source_name: str,
    ) -> List[Property]:
        """Extract property data from plain / unstructured text using GPT.

        Useful for social-media posts or scraped text where HTML structure
        is absent or not meaningful.
        """
        if not self._client:
            return []

        truncated = text[: self._MAX_INPUT_CHARS]
        raw_items = self._call_extraction_api(truncated, source_url)
        return self._parse_raw_items(raw_items, source_url, source_name)

    def analyze_error(
        self,
        url: str,
        status_code: int,
        response_text: str,
    ) -> dict:
        """Diagnose a scraping failure and suggest workarounds.

        Returns a structured dict with keys:
        ``error_type``, ``likely_cause``, ``suggestions``, ``retry_strategy``.

        Falls back to a static, rule-based dict when the API is unavailable.
        """
        if not self._client:
            return _static_error_analysis(status_code)

        snippet = response_text[:2_000]
        user_msg = (
            f"URL: {url}\n"
            f"HTTP status: {status_code}\n"
            f"Response snippet:\n{snippet}"
        )
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _ERROR_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                max_tokens=400,
            )
            raw = resp.choices[0].message.content or "{}"
            return _parse_json_response(raw)
        except Exception as exc:
            logger.debug("[ai_extractor] Error analysis API call failed: %s", exc)
            return _static_error_analysis(status_code)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_extraction_api(self, content: str, source_url: str) -> List[dict]:
        """Call GPT and return the parsed list of raw property dicts."""
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Bron-URL: {source_url}\n\n"
                            f"Inhoud:\n{content}"
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=2_000,
            )
            raw = resp.choices[0].message.content or "[]"
            result = _parse_json_response(raw)
            return result if isinstance(result, list) else []
        except Exception as exc:
            logger.warning("[ai_extractor] GPT extraction call failed: %s", exc)
            return []

    def _parse_raw_items(
        self,
        raw_items: List[dict],
        source_url: str,
        source_name: str,
    ) -> List[Property]:
        """Convert raw GPT-output dicts into ``Property`` objects."""
        results: List[Property] = []
        for idx, item in enumerate(raw_items):
            try:
                prop = _dict_to_property(item, idx, source_url, source_name)
                if prop:
                    results.append(prop)
            except Exception as exc:
                logger.debug("[ai_extractor] Could not parse GPT item %d: %s", idx, exc)

        if results:
            logger.info(
                "[ai_extractor] Extracted %d properties from %s via AI",
                len(results),
                source_name,
            )
        return results


# ── Module-level helpers ──────────────────────────────────────────────────────

def _strip_html_noise(html: str) -> str:
    """Remove ``<script>``, ``<style>`` blocks and all remaining HTML tags."""
    html = re.sub(
        r"<script[^>]*>.*?</script[^>]*>", " ", html, flags=re.DOTALL | re.IGNORECASE
    )
    html = re.sub(
        r"<style[^>]*>.*?</style[^>]*>", " ", html, flags=re.DOTALL | re.IGNORECASE
    )
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _parse_json_response(raw: str) -> Any:
    """Strip markdown code fences and parse JSON."""
    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    clean = re.sub(r"\s*```$", "", clean)
    return json.loads(clean)


_PROP_TYPE_MAP: Dict[str, PropertyType] = {
    "farm": PropertyType.FARM,
    "villa": PropertyType.VILLA,
    "land": PropertyType.LAND,
    "other": PropertyType.OTHER,
}


def _dict_to_property(
    item: Dict[str, Any],
    idx: int,
    source_url: str,
    source_name: str,
) -> Optional[Property]:
    """Build a ``Property`` from a raw dict returned by GPT."""
    title = str(item.get("title") or "Woning").strip()
    if not title:
        return None

    price_raw = item.get("price")
    price = float(price_raw) if price_raw is not None else None

    prop_type_raw = (item.get("property_type") or "house").lower()
    prop_type = _PROP_TYPE_MAP.get(prop_type_raw, PropertyType.HOUSE)

    # Build a stable-ish ID: source + sequential index + title hash
    prop_id = f"{source_name}_ai_{idx}_{hash(title + str(price)) & 0xFFFFFFFF}"

    return Property(
        id=prop_id,
        source=source_name,
        source_url=item.get("source_url") or source_url,
        title=title,
        description=item.get("description"),
        property_type=prop_type,
        price=price,
        address=item.get("address"),
        postal_code=str(item.get("postal_code") or ""),
        municipality=item.get("municipality"),
        land_area=_to_float(item.get("land_area")),
        living_area=_to_float(item.get("living_area")),
        bedrooms=_to_int(item.get("bedrooms")),
    )


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _static_error_analysis(status_code: int) -> dict:
    """Return a rule-based error analysis dict (no API call required)."""
    if status_code == 403:
        return {
            "error_type": "bot_detection",
            "likely_cause": "Cloudflare or custom bot-detection blocked the request",
            "suggestions": [
                "Use Playwright with a realistic browser context",
                "Add random delays between requests",
                "Rotate User-Agent headers",
                "Consider routing through a residential proxy",
            ],
            "retry_strategy": "playwright",
        }
    if status_code == 400:
        return {
            "error_type": "bad_request",
            "likely_cause": "Query parameters or request format may have changed",
            "suggestions": [
                "Inspect network traffic on the live site for the correct parameter names",
                "The site may now require authentication or an API token",
            ],
            "retry_strategy": "none",
        }
    if status_code == 429:
        return {
            "error_type": "rate_limited",
            "likely_cause": "Too many requests sent in a short time",
            "suggestions": [
                "Add exponential back-off between requests",
                "Spread requests over a longer time window",
            ],
            "retry_strategy": "wait_and_retry",
        }
    return {
        "error_type": "unknown",
        "likely_cause": f"HTTP {status_code} response",
        "suggestions": ["Inspect the response body and check the site manually"],
        "retry_strategy": "none",
    }
