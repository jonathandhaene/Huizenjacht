"""
Pydantic data models for the Huizenjacht property pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class PropertyType(str, Enum):
    HOUSE = "house"
    FARM = "farm"
    VILLA = "villa"
    LAND = "land"
    OTHER = "other"


class RiskLevel(str, Enum):
    """Traffic-light severity for a single risk item."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class RiskItem(BaseModel):
    """One structured risk associated with a property."""
    name: str = Field(..., description="Short name of the risk (Dutch)")
    level: RiskLevel = Field(..., description="Severity of the risk")
    detail: str = Field(..., description="One-line Dutch explanation")
    source_url: Optional[str] = Field(None, description="Link to the authoritative source")


class GovernmentData(BaseModel):
    """Planning, permit and risk information fetched from government sources."""

    # ── Zoning & permits ──────────────────────────────────────────────────────
    zoning: Optional[str] = Field(None, description="Bestemmingszone (e.g. agrarisch gebied)")
    agricultural_zone: Optional[bool] = Field(None, description="Is the plot in an agricultural zone?")
    nature_zone: Optional[bool] = Field(None, description="Is the plot in a nature protection zone?")
    building_permit_possible: Optional[bool] = Field(
        None, description="Is additional construction likely permitted?"
    )
    animal_keeping_allowed: Optional[bool] = Field(
        None, description="Is keeping livestock/animals allowed on the parcel?"
    )
    bnb_possible: Optional[bool] = Field(
        None, description="Is operating a B&B likely feasible on this parcel?"
    )

    # ── Standard risks (already in original model) ────────────────────────────
    flood_risk: Optional[str] = Field(None, description="Flood risk classification (VMM)")
    heritage_protected: Optional[bool] = Field(None, description="Monument / heritage protection")

    # ── Additional risk fields ─────────────────────────────────────────────────
    soil_contamination: Optional[str] = Field(
        None,
        description=(
            "OVAM soil-contamination status: None = clean / investigatiegebied / "
            "saneringsgebied / historisch"
        ),
    )
    erosion_risk: Optional[str] = Field(
        None, description="AGIV erosion risk class (e.g. 'Zeer hoog', 'Hoog', 'Matig')"
    )
    natura_2000: Optional[bool] = Field(
        None, description="Within a Natura 2000 Special Protection Zone (SBZ)"
    )
    ven_zone: Optional[bool] = Field(
        None, description="Within the Flemish Ecological Network (VEN) — very strict land-use rules"
    )
    signa_watersensitive: Optional[bool] = Field(
        None, description="Watertoets: water-sensitive open space (VMM Signa)"
    )

    # ── Compiled risk summary (filled in by GovernmentEnrichmentAgent) ─────────
    risks: list[RiskItem] = Field(
        default_factory=list,
        description="Structured risk items derived from all government data",
    )

    # ── Meta ──────────────────────────────────────────────────────────────────
    source_url: Optional[str] = Field(None, description="URL where government data was retrieved")
    raw_notes: Optional[str] = Field(None, description="Free-text notes from government sources")


class AIAnalysis(BaseModel):
    """AI-generated analysis of how well a property matches the search criteria."""

    score: float = Field(..., ge=0, le=10, description="Match score 0–10 (10 = perfect match)")
    summary: str = Field(..., description="Short Dutch summary of why this property matches")
    pros: list[str] = Field(default_factory=list, description="Positive aspects")
    cons: list[str] = Field(default_factory=list, description="Negative aspects or unknowns")
    recommendations: list[str] = Field(
        default_factory=list, description="Suggested follow-up actions"
    )


class Property(BaseModel):
    """A real-estate listing discovered by one of the scraper agents."""

    id: str = Field(..., description="Unique identifier (source + internal id)")
    source: str = Field(..., description="Origin of the listing (immoweb, zimmo, realo, …)")
    source_url: str = Field(..., description="Direct URL to the listing")
    title: str = Field(..., description="Listing title")
    description: Optional[str] = Field(None, description="Full listing description")
    property_type: PropertyType = Field(PropertyType.HOUSE)
    price: Optional[float] = Field(None, description="Asking price in EUR")
    address: Optional[str] = Field(None, description="Full address string")
    postal_code: Optional[str] = None
    municipality: Optional[str] = None
    land_area: Optional[float] = Field(None, description="Land/plot area in m²")
    living_area: Optional[float] = Field(None, description="Living area in m²")
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    images: list[str] = Field(default_factory=list, description="Image URLs")
    images_remote: list[str] = Field(
        default_factory=list,
        description="Original scraped image URLs (preserved after local caching)",
    )
    images_local: list[str] = Field(
        default_factory=list,
        description="Locally cached image paths relative to docs/ (served via GitHub Pages)",
    )
    images_cached_at: Optional[datetime] = Field(
        None, description="ISO timestamp when images were last cached locally"
    )
    features: list[str] = Field(default_factory=list, description="Extra features / tags")
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Enrichment results — populated later in the pipeline
    government_data: Optional[GovernmentData] = None
    ai_analysis: Optional[AIAnalysis] = None

    model_config = {"use_enum_values": True}
