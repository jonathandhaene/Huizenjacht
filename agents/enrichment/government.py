"""
Government data enrichment agent.

Queries Belgian/Flemish government open-data APIs to retrieve:
- Bestemmingszone (zoning) from the Ruimtelijk Uitvoeringsplan (RUP)
- Overstromingsgevoeligheid (flood risk) from VMM / Waterinfo.be
- Watertoets watergevoelig open ruimtegebied (VMM Signa)
- Bodemverontreiniging (soil contamination) from OVAM / Geopunt
- Erosiegevoeligheid (erosion risk) from AGIV
- Speciale Beschermingszones / Natura 2000 (SBZ) from Geopunt
- Vlaams Ecologisch Netwerk (VEN) from Geopunt
- Beschermd erfgoed (heritage protection) from Agentschap Onroerend Erfgoed

All APIs used are free / open data — no authentication required.

Flemish geo-data portal: https://www.geopunt.be/
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from agents.scrapers.base import BaseScraper  # reuse HTTP client helpers
from models.property import GovernmentData, Property, RiskItem, RiskLevel

logger = logging.getLogger(__name__)

# ── Geopunt / AGIV base endpoints ─────────────────────────────────────────────
_LOC_API = "https://loc.geopunt.be/v4/location"
_AGIV_WFS_BASE = "https://geoservices.informatievlaanderen.be/overdrachtdiensten"

_RUIMTELIJKEBESTEMMING_WFS = f"{_AGIV_WFS_BASE}/RUP/wfs"
_OVERSTROMINGEN_WFS        = f"{_AGIV_WFS_BASE}/overstromingen/wfs"
_WATERTOETS_WFS            = f"{_AGIV_WFS_BASE}/Watertoets/wfs"
_EROSIE_WFS                = f"{_AGIV_WFS_BASE}/erosie/wfs"
_SBZ_WFS                   = f"{_AGIV_WFS_BASE}/SBZ/wfs"
_VEN_WFS                   = f"{_AGIV_WFS_BASE}/VEN/wfs"
_BODEM_WFS                 = f"{_AGIV_WFS_BASE}/Grond/wfs"
_ERFGOED_API               = "https://inventaris.onroerenderfgoed.be/aanduidingsobjecten.json"

# Handy deep-link into Geopunt for the end-user
_GEOPUNT_VIEWER = "https://www.geopunt.be/kaart#?"


class GovernmentEnrichmentAgent:
    """
    Enriches a Property object with government / planning data and a compiled
    list of structured risk items.

    Usage::

        agent = GovernmentEnrichmentAgent()
        property_obj = agent.enrich(property_obj)
    """

    def __init__(self) -> None:
        from agents.scrapers.base import HttpClient
        self._http = HttpClient()

    def enrich(self, prop: Property) -> Property:
        """Attach GovernmentData (including risks) to *prop* in place and return it."""
        gov = GovernmentData()
        try:
            coords = self._geocode(prop)
            if coords:
                lon, lat = coords
                gov = self._fetch_all(lon, lat, prop)
        except Exception as exc:
            logger.warning(
                "[gov_enrichment] Enrichment failed for %s: %s", prop.id, exc
            )
        prop.government_data = gov
        return prop

    # ------------------------------------------------------------------
    # Geocoding
    # ------------------------------------------------------------------

    def _geocode(self, prop: Property) -> Optional[tuple[float, float]]:
        """Return (longitude, latitude) for the property address."""
        query_parts = [p for p in [prop.address, prop.postal_code, prop.municipality] if p]
        if not query_parts:
            return None
        query = " ".join(query_parts)
        try:
            resp = self._http._get(_LOC_API, params={"q": query, "c": 1})
            data = resp.json()
            results = data.get("LocationResult") or []
            if results:
                loc = results[0].get("Location", {})
                x = loc.get("Lon_WGS84") or loc.get("X_84")
                y = loc.get("Lat_WGS84") or loc.get("Y_84")
                if x and y:
                    return float(x), float(y)
        except Exception as exc:
            logger.debug("[gov_enrichment] Geocoding failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Top-level fetch coordinator
    # ------------------------------------------------------------------

    def _fetch_all(self, lon: float, lat: float, prop: Property) -> GovernmentData:
        gov = GovernmentData()
        gov.source_url = (
            f"{_GEOPUNT_VIEWER}extent={lon-0.01:.5f},{lat-0.01:.5f},"
            f"{lon+0.01:.5f},{lat+0.01:.5f}"
        )

        # 1. Zoning / bestemmingszone
        zoning_info = self._fetch_zoning(lon, lat)
        if zoning_info:
            gov.zoning = zoning_info
            lower = zoning_info.lower()
            gov.agricultural_zone = "agrar" in lower
            gov.nature_zone = "natuur" in lower or "bos" in lower
            gov.animal_keeping_allowed = gov.agricultural_zone
            gov.bnb_possible = "woon" in lower or "agrar" in lower or "landelijk" in lower
            gov.building_permit_possible = "woon" in lower or "agrar" in lower

        # 2. Flood risk (VMM overstromingsgevaarkaart)
        gov.flood_risk = self._fetch_flood_risk(lon, lat)

        # 3. Watertoets — watergevoelig open ruimtegebied
        gov.signa_watersensitive = self._fetch_watersensitive(lon, lat)

        # 4. Soil contamination (OVAM via AGIV)
        gov.soil_contamination = self._fetch_soil_contamination(lon, lat)

        # 5. Erosion risk (AGIV)
        gov.erosion_risk = self._fetch_erosion_risk(lon, lat)

        # 6. Natura 2000 / SBZ
        gov.natura_2000 = self._fetch_natura_2000(lon, lat)

        # 7. VEN (Vlaams Ecologisch Netwerk)
        gov.ven_zone = self._fetch_ven(lon, lat)

        # 8. Heritage protection (Onroerend Erfgoed)
        gov.heritage_protected = self._fetch_heritage(lon, lat)

        # 9. Compile structured risk list from all collected data
        gov.risks = self._compile_risks(gov)

        return gov

    # ------------------------------------------------------------------
    # Individual data fetchers
    # ------------------------------------------------------------------

    def _fetch_zoning(self, lon: float, lat: float) -> Optional[str]:
        """Query the Flemish RUP WFS for the bestemmingszone at these coordinates."""
        delta = 0.001
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "RUP:Bestemmingszone",
            "BBOX": bbox,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "count": "1",
        }
        try:
            resp = self._http._get(_RUIMTELIJKEBESTEMMING_WFS, params=params)
            data = resp.json()
            features = data.get("features") or []
            if features:
                props = features[0].get("properties") or {}
                return (
                    props.get("NAAM")
                    or props.get("naam")
                    or props.get("BESTEMMING")
                    or props.get("bestemming")
                )
        except Exception as exc:
            logger.debug("[gov_enrichment] Zoning fetch failed: %s", exc)
        return None

    def _fetch_flood_risk(self, lon: float, lat: float) -> Optional[str]:
        """
        Query the VMM overstromingsgevaarkaart WFS.
        Returns the risk class string or None if the parcel is not at risk.
        """
        delta = 0.001
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "overstromingsgevaarkaart",
            "BBOX": bbox,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "count": "1",
        }
        try:
            resp = self._http._get(_OVERSTROMINGEN_WFS, params=params)
            data = resp.json()
            features = data.get("features") or []
            if features:
                p = features[0].get("properties") or {}
                return p.get("RISICO") or p.get("risico") or "Overstroombaar"
        except Exception as exc:
            logger.debug("[gov_enrichment] Flood risk fetch failed: %s", exc)
        return None

    def _fetch_watersensitive(self, lon: float, lat: float) -> Optional[bool]:
        """
        VMM Watertoets: watergevoelig open ruimtegebied.
        These areas have strict limits on sealing/construction.
        """
        delta = 0.001
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "Watertoets:WatergevoeligOpenRuimtegebied",
            "BBOX": bbox,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "count": "1",
        }
        try:
            resp = self._http._get(_WATERTOETS_WFS, params=params)
            data = resp.json()
            return len(data.get("features") or []) > 0
        except Exception as exc:
            logger.debug("[gov_enrichment] Watertoets fetch failed: %s", exc)
        return None

    def _fetch_soil_contamination(self, lon: float, lat: float) -> Optional[str]:
        """
        OVAM bodemverontreiniging via AGIV Grond WFS.
        Returns the contamination class or None if the plot is clean.
        """
        delta = 0.002
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "Grond:BodemVervuiling",
            "BBOX": bbox,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "count": "1",
        }
        try:
            resp = self._http._get(_BODEM_WFS, params=params)
            data = resp.json()
            features = data.get("features") or []
            if features:
                props = features[0].get("properties") or {}
                return (
                    props.get("TYPE")
                    or props.get("type")
                    or props.get("STATUS")
                    or props.get("status")
                    or "Aanwezig"
                )
        except Exception as exc:
            logger.debug("[gov_enrichment] Soil contamination fetch failed: %s", exc)
        return None

    def _fetch_erosion_risk(self, lon: float, lat: float) -> Optional[str]:
        """
        AGIV erosiegevoeligheid WFS.
        Returns the erosion risk class (e.g. 'Zeer hoog', 'Hoog', 'Matig', 'Laag') or None.
        """
        delta = 0.001
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "erosie:ErosieGevoeligheid",
            "BBOX": bbox,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "count": "1",
        }
        try:
            resp = self._http._get(_EROSIE_WFS, params=params)
            data = resp.json()
            features = data.get("features") or []
            if features:
                props = features[0].get("properties") or {}
                return (
                    props.get("KLASSE")
                    or props.get("klasse")
                    or props.get("EROSIE")
                    or props.get("erosie")
                    or "Aanwezig"
                )
        except Exception as exc:
            logger.debug("[gov_enrichment] Erosion risk fetch failed: %s", exc)
        return None

    def _fetch_natura_2000(self, lon: float, lat: float) -> Optional[bool]:
        """
        AGIV SBZ (Speciale Beschermingszones) WFS — Natura 2000 zones.
        Building and land-use changes within SBZ require a 'passende beoordeling'.
        """
        delta = 0.001
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "SBZ:SBZ",
            "BBOX": bbox,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "count": "1",
        }
        try:
            resp = self._http._get(_SBZ_WFS, params=params)
            data = resp.json()
            return len(data.get("features") or []) > 0
        except Exception as exc:
            logger.debug("[gov_enrichment] Natura 2000 fetch failed: %s", exc)
        return None

    def _fetch_ven(self, lon: float, lat: float) -> Optional[bool]:
        """
        AGIV VEN (Vlaams Ecologisch Netwerk) WFS.
        VEN has the strictest land-use rules in Flanders — very limited development.
        """
        delta = 0.001
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "VEN:VEN",
            "BBOX": bbox,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "count": "1",
        }
        try:
            resp = self._http._get(_VEN_WFS, params=params)
            data = resp.json()
            return len(data.get("features") or []) > 0
        except Exception as exc:
            logger.debug("[gov_enrichment] VEN fetch failed: %s", exc)
        return None

    def _fetch_heritage(self, lon: float, lat: float) -> Optional[bool]:
        """
        Query the Onroerend Erfgoed API for heritage-protected objects near these coordinates.
        """
        try:
            params = {
                "coordinates_within": 100,  # metres
                "x": lon,
                "y": lat,
                "projection": "EPSG:4326",
                "only_active": True,
            }
            resp = self._http._get(_ERFGOED_API, params=params)
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items") or []
            return len(items) > 0
        except Exception as exc:
            logger.debug("[gov_enrichment] Heritage fetch failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Risk compilation
    # ------------------------------------------------------------------

    @staticmethod
    def _compile_risks(gov: GovernmentData) -> list[RiskItem]:
        """
        Build a human-readable, traffic-light-coded list of risks from all
        collected government data.  Items are sorted high → medium → low.
        """
        risks: list[RiskItem] = []

        # ── Flood risk ────────────────────────────────────────────────────────
        if gov.flood_risk:
            lower = gov.flood_risk.lower()
            level = RiskLevel.HIGH if ("hoog" in lower or "groot" in lower) else RiskLevel.MEDIUM
            risks.append(RiskItem(
                name="Overstromingsrisico",
                level=level,
                detail=gov.flood_risk,
                source_url=(
                    "https://www.waterinfo.be/default.aspx?path=NL/WaterWatcher/Default"
                ),
            ))
        else:
            risks.append(RiskItem(
                name="Overstromingsrisico",
                level=RiskLevel.LOW,
                detail="Geen overstromingsrisico vastgesteld",
                source_url="https://www.waterinfo.be",
            ))

        # ── Watertoets / watergevoelig open ruimte ────────────────────────────
        if gov.signa_watersensitive:
            risks.append(RiskItem(
                name="Watergevoelig open ruimtegebied",
                level=RiskLevel.MEDIUM,
                detail=(
                    "Perceel ligt in watergevoelig open ruimtegebied (VMM Watertoets) — "
                    "verharding en bouwwerken zijn sterk beperkt"
                ),
                source_url="https://www.vmm.be/water/watertoets",
            ))

        # ── Soil contamination ────────────────────────────────────────────────
        if gov.soil_contamination:
            lower = gov.soil_contamination.lower()
            level = RiskLevel.HIGH if ("sanering" in lower or "verontreinig" in lower) else RiskLevel.MEDIUM
            risks.append(RiskItem(
                name="Bodemverontreiniging",
                level=level,
                detail=f"OVAM-bodemstatus: {gov.soil_contamination}",
                source_url="https://www.ovam.be/grondverzet-en-bodembeheer",
            ))
        else:
            risks.append(RiskItem(
                name="Bodemverontreiniging",
                level=RiskLevel.LOW,
                detail="Geen bodemverontreiniging geregistreerd (OVAM)",
                source_url="https://www.ovam.be",
            ))

        # ── Erosion risk ─────────────────────────────────────────────────────
        if gov.erosion_risk:
            lower = gov.erosion_risk.lower()
            if "zeer hoog" in lower:
                level = RiskLevel.HIGH
            elif "hoog" in lower or "matig" in lower:
                level = RiskLevel.MEDIUM
            else:
                level = RiskLevel.LOW
            risks.append(RiskItem(
                name="Erosiegevoeligheid",
                level=level,
                detail=f"Erosieklasse: {gov.erosion_risk} — landbouwactiviteiten vereisen bodembescherming",
                source_url="https://www.vmm.be/data/erosiekaart",
            ))

        # ── Natura 2000 / SBZ ────────────────────────────────────────────────
        if gov.natura_2000:
            risks.append(RiskItem(
                name="Natura 2000 (SBZ)",
                level=RiskLevel.MEDIUM,
                detail=(
                    "Perceel ligt in een Speciale Beschermingszone (Natura 2000) — "
                    "bouwaanvragen vereisen een passende beoordeling"
                ),
                source_url="https://www.natura2000.be",
            ))

        # ── VEN ───────────────────────────────────────────────────────────────
        if gov.ven_zone:
            risks.append(RiskItem(
                name="Vlaams Ecologisch Netwerk (VEN)",
                level=RiskLevel.HIGH,
                detail=(
                    "Perceel ligt in het VEN — strengste natuurbeschermingsregels in Vlaanderen, "
                    "nauwelijks bouwmogelijkheden"
                ),
                source_url="https://www.natuurenbos.be/beleid-en-wetgeving/vlaams-ecologisch-netwerk-ven",
            ))

        # ── Heritage protection ───────────────────────────────────────────────
        if gov.heritage_protected:
            risks.append(RiskItem(
                name="Onroerenderfgoedbescherming",
                level=RiskLevel.MEDIUM,
                detail=(
                    "Pand of omgeving is beschermd als monument of erfgoedlandschap — "
                    "verbouwingen vereisen toestemming van Onroerend Erfgoed"
                ),
                source_url="https://inventaris.onroerenderfgoed.be",
            ))

        # ── Zoning / nature zone ──────────────────────────────────────────────
        if gov.nature_zone:
            risks.append(RiskItem(
                name="Natuur- of bosgebied",
                level=RiskLevel.MEDIUM,
                detail=(
                    "Bestemmingszone: natuur of bos — "
                    "constructies en functiewijzigingen zijn zeer beperkt mogelijk"
                ),
                source_url="https://omgevingsloket.be",
            ))

        # ── Sort: high → medium → low → unknown ──────────────────────────────
        _order = {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2, RiskLevel.UNKNOWN: 3}
        risks.sort(key=lambda r: _order.get(r.level, 3))

        return risks
