"""
Government data enrichment agent.

Queries Belgian/Flemish government open-data APIs to retrieve:
- Bestemmingszone (zoning) from the Ruimtelijk Uitvoeringsplan (RUP)
- Overstromingsgevoeligheid (flood risk) from Waterinfo.be
- Beschermd erfgoed (heritage protection) from the Agentschap Onroerend Erfgoed
- Stedenbouwkundige vergunningen (building permits) from MAGDA/GIPOD

All APIs used are free / open data — no authentication required.

Flemish geo-data portal: https://www.geopunt.be/
PDOK / Geopunt WMS/WFS services are used via their REST query endpoints.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from agents.scrapers.base import BaseScraper  # reuse HTTP client helpers
from models.property import GovernmentData, Property

logger = logging.getLogger(__name__)

# ── Geopunt / AGIV endpoints ──────────────────────────────────────────────────
_GEOPUNT_GEOCODER = "https://loc.geopunt.be/geolocation/location"
_GEOPUNT_ZONING = "https://geoservices.informatievlaanderen.be/overdrachtdiensten/VRBG/wfs"
_WATERINFO_WMS = "https://www.waterinfo.be/api/waterlopen/getCapabilities"
_ERFGOED_API = "https://inventaris.onroerenderfgoed.be/aanduidingsobjecten.json"
_RUIMTELIJKEBESTEMMING_WFS = (
    "https://geoservices.informatievlaanderen.be/overdrachtdiensten/RUP/wfs"
)

# Geolocation lookup (Geopunt)
_LOC_API = "https://loc.geopunt.be/v4/location"


class GovernmentEnrichmentAgent:
    """
    Enriches a Property object with government / planning data.

    Usage::

        agent = GovernmentEnrichmentAgent()
        property_obj = agent.enrich(property_obj)
    """

    def __init__(self) -> None:
        # Re-use the HttpClient for HTTP requests
        from agents.scrapers.base import HttpClient
        self._http = HttpClient()

    def enrich(self, prop: Property) -> Property:
        """Attach GovernmentData to *prop* in place and return it."""
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
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_all(self, lon: float, lat: float, prop: Property) -> GovernmentData:
        gov = GovernmentData()
        gov.source_url = f"https://www.geopunt.be/kaart#?extent={lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}"

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

        # 2. Flood risk
        flood = self._fetch_flood_risk(lon, lat)
        if flood:
            gov.flood_risk = flood

        # 3. Heritage protection
        heritage = self._fetch_heritage(lon, lat)
        gov.heritage_protected = heritage

        return gov

    # ------------------------------------------------------------------

    def _fetch_zoning(self, lon: float, lat: float) -> Optional[str]:
        """
        Query the Flemish RUP WFS for the bestemmingszone at these coordinates.
        Uses a simple BBOX / intersects query.
        """
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
        Query the Waterinfo / VMM flood risk service.
        Returns a string like 'Mogelijk overstromingsgebied' or None.
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
            resp = self._http._get(
                "https://geoservices.informatievlaanderen.be/overdrachtdiensten/overstromingen/wfs",
                params=params,
            )
            data = resp.json()
            features = data.get("features") or []
            if features:
                p = features[0].get("properties") or {}
                return p.get("RISICO") or p.get("risico") or "Overstroombaar"
        except Exception as exc:
            logger.debug("[gov_enrichment] Flood risk fetch failed: %s", exc)
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
