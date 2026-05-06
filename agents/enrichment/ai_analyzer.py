"""
AI Analyzer agent.

Uses OpenAI's chat API to evaluate how well a property listing matches
the buyer's criteria and to generate a human-readable Dutch summary
with pros, cons and recommended next steps.

If the OPENAI_API_KEY is not set the agent falls back to a simple
rule-based scoring so the pipeline can still run without an API key.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from config.settings import settings
from models.property import AIAnalysis, GovernmentData, Property

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Je bent een Vlaamse vastgoedexpert die een huis zoekt voor een gezin met twee jongens \
en een Australische cattle dog. Het gezin wil landelijk wonen in de Vlaamse Ardennen \
en heeft nood aan:
- Min. 3 slaapkamers
- Groot perceel (min. 5.000 m²), bij voorkeur met weiland voor vee (koeien)
- Ruimte voor honden en andere dieren
- Mogelijkheid tot B&B exploitatie
- Budget: max. € 600.000
- Landelijke / agrarische ligging, natuur en rust

Geef je analyse in het Nederlands als JSON met deze velden:
{
  "score": <float 0-10>,
  "summary": "<korte samenvatting>",
  "pros": ["...", ...],
  "cons": ["...", ...],
  "recommendations": ["...", ...]
}
Geef ENKEL de JSON terug, geen extra tekst.
"""


class AIAnalyzerAgent:
    """
    Analyses a Property using the OpenAI API and returns an AIAnalysis.
    Falls back to rule-based scoring when no API key is configured.
    """

    def __init__(self) -> None:
        from agents.llm_client import get_chat_client

        self._client, self._model, backend = get_chat_client()
        if backend == "github":
            logger.info("[ai_analyzer] using GitHub Models (%s)", self._model)
        elif backend == "openai":
            logger.info("[ai_analyzer] using OpenAI (%s)", self._model)
        else:
            logger.info("[ai_analyzer] no LLM credentials configured; using fallback scorer")

    def analyze(self, prop: Property) -> Property:
        """Attach AIAnalysis to *prop* in place and return it."""
        if self._client:
            analysis = self._openai_analyze(prop)
        else:
            analysis = self._fallback_analyze(prop)
        prop.ai_analysis = analysis
        return prop

    # ------------------------------------------------------------------
    # OpenAI path
    # ------------------------------------------------------------------

    def _openai_analyze(self, prop: Property) -> AIAnalysis:
        user_message = self._build_user_message(prop)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
                max_tokens=800,
            )
            raw = response.choices[0].message.content or ""
            return self._parse_response(raw)
        except Exception as exc:
            logger.warning("[ai_analyzer] OpenAI call failed: %s — falling back", exc)
            return self._fallback_analyze(prop)

    def _build_user_message(self, prop: Property) -> str:
        gov = prop.government_data
        gov_info = ""
        if gov:
            risk_lines = ""
            if gov.risks:
                risk_lines = "\nRisico-overzicht:\n" + "\n".join(
                    f"  [{r.level.upper()}] {r.name}: {r.detail}" for r in gov.risks
                )
            gov_info = (
                f"\nBestemmingszone: {gov.zoning or 'onbekend'}"
                f"\nAgrarisch: {gov.agricultural_zone}"
                f"\nDieren houden: {gov.animal_keeping_allowed}"
                f"\nB&B mogelijk: {gov.bnb_possible}"
                f"\nOverstromingsrisico: {gov.flood_risk or 'geen'}"
                f"\nBodemverontreiniging: {gov.soil_contamination or 'geen'}"
                f"\nErosierisico: {gov.erosion_risk or 'onbekend'}"
                f"\nNatura 2000 (SBZ): {gov.natura_2000}"
                f"\nVEN zone: {gov.ven_zone}"
                f"\nWatergevoelig open ruimtegebied: {gov.signa_watersensitive}"
                f"\nErfgoedbescherming: {gov.heritage_protected}"
                f"{risk_lines}"
            )

        return (
            f"Eigendom: {prop.title}\n"
            f"Prijs: € {prop.price:,.0f}\n" if prop.price else f"Prijs: onbekend\n"
            f"Gemeente: {prop.municipality or 'onbekend'}\n"
            f"Perceeloppervlakte: {prop.land_area or 'onbekend'} m²\n"
            f"Bewoonbare oppervlakte: {prop.living_area or 'onbekend'} m²\n"
            f"Slaapkamers: {prop.bedrooms or 'onbekend'}\n"
            f"Type: {prop.property_type}\n"
            f"Beschrijving: {(prop.description or '')[:800]}\n"
            f"Kenmerken: {', '.join(prop.features)}\n"
            f"{gov_info}"
        )

    @staticmethod
    def _parse_response(raw: str) -> AIAnalysis:
        # Strip markdown code fences if present
        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        clean = re.sub(r"\s*```$", "", clean)
        data = json.loads(clean)
        return AIAnalysis(
            score=float(data.get("score", 5)),
            summary=data.get("summary", ""),
            pros=data.get("pros", []),
            cons=data.get("cons", []),
            recommendations=data.get("recommendations", []),
        )

    # ------------------------------------------------------------------
    # Rule-based fallback (no API key required)
    # ------------------------------------------------------------------

    def _fallback_analyze(self, prop: Property) -> AIAnalysis:
        """Simple heuristic scorer — no external API call."""
        score = 5.0
        pros: list[str] = []
        cons: list[str] = []
        recommendations: list[str] = []

        # Price
        if prop.price:
            if prop.price <= settings.max_price:
                pros.append(f"Prijs (€ {prop.price:,.0f}) past binnen budget")
                score += 0.5
            else:
                cons.append(f"Prijs (€ {prop.price:,.0f}) overschrijdt budget van € {settings.max_price:,}")
                score -= 2

        # Bedrooms
        if prop.bedrooms:
            if prop.bedrooms >= settings.min_bedrooms:
                pros.append(f"{prop.bedrooms} slaapkamers — voldoende voor gezin")
                score += 0.5
            else:
                cons.append(f"Slechts {prop.bedrooms} slaapkamers (min. {settings.min_bedrooms} vereist)")
                score -= 1

        # Land area
        if prop.land_area:
            if prop.land_area >= settings.min_land_area:
                pros.append(f"Groot perceel van {prop.land_area:,.0f} m²")
                score += 1
            else:
                cons.append(f"Perceel ({prop.land_area:,.0f} m²) kleiner dan minimum ({settings.min_land_area:,} m²)")
                score -= 1

        # Keywords in description/title
        text = ((prop.title or "") + " " + (prop.description or "")).lower()
        for kw in ["hoeve", "boerderij", "landelijk", "weiland", "stal", "schuur"]:
            if kw in text:
                pros.append(f'Vermeldt "{kw}" — mogelijks geschikt voor landelijke levensstijl')
                score += 0.5
        for kw in ["b&b", "gastenverblijf", "logies"]:
            if kw in text:
                pros.append(f'Vermeldt "{kw}" — B&B potentieel aanwezig')
                score += 0.5

        # Government data
        gov = prop.government_data
        if gov:
            if gov.agricultural_zone:
                pros.append("Agrarische zone — dieren houden waarschijnlijk toegelaten")
                score += 1
            if gov.flood_risk:
                cons.append(f"Overstromingsrisico: {gov.flood_risk}")
                score -= 0.5
            if gov.soil_contamination:
                cons.append(f"Bodemverontreiniging geregistreerd: {gov.soil_contamination}")
                score -= 1
                recommendations.append("Vraag een bodemattest op bij OVAM vóór aankoop")
            if gov.ven_zone:
                cons.append("Perceel in VEN — strengste natuurbescherming, nauwelijks bouwmogelijkheden")
                score -= 1.5
                recommendations.append("Raadpleeg Agentschap Natuur & Bos over bouw- en gebruiksmogelijkheden")
            if gov.natura_2000:
                cons.append("Natura 2000 SBZ — bouwaanvragen vereisen passende beoordeling")
                score -= 0.5
                recommendations.append("Controleer impact op SBZ via www.natura2000.be")
            if gov.signa_watersensitive:
                cons.append("Watergevoelig open ruimtegebied (VMM) — verharding en constructies sterk beperkt")
                score -= 0.5
            if gov.erosion_risk:
                lower = gov.erosion_risk.lower()
                if "zeer hoog" in lower or "hoog" in lower:
                    cons.append(f"Erosierisico {gov.erosion_risk} — bodembeschermende maatregelen vereist")
                    score -= 0.5
            if gov.heritage_protected:
                cons.append("Erfgoedbescherming — mogelijke bouwbeperkingen")
                score -= 0.5
                recommendations.append("Raadpleeg het Agentschap Onroerend Erfgoed voor bouwmogelijkheden")

        # Clamp
        score = max(0.0, min(10.0, score))

        recommendations += [
            "Vraag het RUP-attest op bij de gemeente",
            "Controleer stedenbouwkundige bestemming via www.omgevingsloket.be",
            "Informeer bij de gemeente naar vergunning voor B&B / logies",
            "Laat de bodem- en waterhuishouding onderzoeken als u vee wil houden",
        ]

        summary = (
            f"{prop.title} in {prop.municipality or 'de Vlaamse Ardennen'} — "
            f"overeenkoms­score {score:.1f}/10. "
            f"{len(pros)} voordelen, {len(cons)} aandachtspunten."
        )

        return AIAnalysis(
            score=round(score, 1),
            summary=summary,
            pros=pros,
            cons=cons,
            recommendations=recommendations,
        )
