"""
Pydantic request (inbound) schemas for the Wind Load API.

Every user-facing input is validated here with engineering-meaningful
constraints. FastAPI auto-generates the OpenAPI spec from these models,
so the frontend team gets self-documenting validation rules.

Validator philosophy:
    - Reject nonsense early (negative heights, zero wind speed)
    - Provide domain-specific error messages ("ASCE 7 maps start at 85 mph")
    - Allow reasonable engineering flexibility (don't over-constrain)
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.common import (
    CodeVersionEnum,
    ExposureEnum,
    RiskCategoryEnum,
    EnclosureEnum,
    RoofTypeEnum,
    CCZoneEnum,
    TopographyEnum,
)


# ============================================================================
# Reusable Sub-Models
# ============================================================================


class WindProjectSetup(BaseModel):
    """
    Global project-level wind parameters.

    Mirrors the inputs on the spreadsheet's 'Code' and 'Wind' sheets:
    building code edition, risk category, wind speed, exposure, enclosure.
    """

    code_version: CodeVersionEnum = Field(
        ...,
        description="ASCE 7 edition (e.g. '7-22')",
        examples=["7-22"],
    )
    risk_category: RiskCategoryEnum = Field(
        ...,
        description="Building Risk Category per Table 1.5-1",
        examples=["II"],
    )
    V_mph: float = Field(
        ...,
        gt=0,
        description="Design wind speed (mph). Ultimate for ASCE 7-10+; basic for earlier.",
        examples=[120],
    )
    exposure: ExposureEnum = Field(
        ...,
        description="Terrain exposure category per §26.7",
        examples=["C"],
    )
    enclosure: EnclosureEnum = Field(
        default=EnclosureEnum.ENCLOSED,
        description="Building enclosure classification per §26.2",
        examples=["enclosed"],
    )
    importance_factor: float = Field(
        default=1.0,
        ge=0.5,
        le=1.5,
        description=(
            "Wind importance factor I. Always 1.0 for ASCE 7-10+ "
            "(ultimate wind speed maps). Variable for 7-05 and earlier."
        ),
    )

    @field_validator("V_mph")
    @classmethod
    def wind_speed_engineering_range(cls, v: float) -> float:
        """
        ASCE 7 wind speed maps range from 85 mph (Risk Cat I, low-wind)
        to 300 mph (tornado). Warn on implausible values but only reject
        clear errors.
        """
        if v < 85:
            raise ValueError(
                f"Wind speed {v} mph is below the minimum ASCE 7 mapped value "
                f"of 85 mph. Check your input."
            )
        if v > 300:
            raise ValueError(
                f"Wind speed {v} mph exceeds any ASCE 7 mapped value (max ~300 mph "
                f"for tornado regions). Check your input."
            )
        return v

    @model_validator(mode="after")
    def validate_importance_for_edition(self) -> "WindProjectSetup":
        """ASCE 7-10+ uses ultimate wind speeds, so I should be 1.0."""
        edition_year = int("20" + self.code_version.value.split("-")[1])
        if edition_year >= 2010 and self.importance_factor != 1.0:
            raise ValueError(
                f"For ASCE {self.code_version.value} (ultimate wind speed maps), "
                f"the importance factor must be 1.0, not {self.importance_factor}. "
                f"The wind speed maps already account for risk category."
            )
        return self


class BuildingGeometry(BaseModel):
    """
    Building dimensions and roof geometry.

    Mirrors the 'Building Geometry' section on the spreadsheet's Code sheet.
    """

    L_ft: float = Field(
        ...,
        gt=0,
        description="Building length in plan (ft), parallel to ridge for gable roofs",
        examples=[300],
    )
    B_ft: float = Field(
        ...,
        gt=0,
        description="Building least horizontal dimension (ft)",
        examples=[175],
    )
    h_ft: float = Field(
        ...,
        gt=0,
        description="Mean roof height above ground (ft)",
        examples=[62],
    )
    roof_type: RoofTypeEnum = Field(
        default=RoofTypeEnum.GABLE,
        description="Roof geometry type",
        examples=["gable"],
    )
    roof_angle_deg: float = Field(
        default=0.0,
        ge=0,
        le=90,
        description="Roof slope angle θ (degrees from horizontal)",
        examples=[4.0],
    )
    parapet_height_ft: float = Field(
        default=0.0,
        ge=0,
        description="Parapet height above mean roof level (ft). 0 = no parapet.",
        examples=[3.0],
    )

    @field_validator("h_ft")
    @classmethod
    def height_practical_range(cls, v: float) -> float:
        if v > 1500:
            raise ValueError(
                f"Mean roof height {v} ft exceeds practical limits for "
                f"ASCE 7 analytical procedures (max ~1500 ft for supertall)."
            )
        return v

    @model_validator(mode="after")
    def validate_roof_angle_for_type(self) -> "BuildingGeometry":
        """Check that the roof angle falls within the valid range for the roof type."""
        limits = {
            RoofTypeEnum.MONOSLOPE:       (0, 30),
            RoofTypeEnum.HIP:             (7, 27),
            RoofTypeEnum.GABLE:           (0, 45),
            RoofTypeEnum.MULTISPAN_GABLE: (10, 45),
            RoofTypeEnum.SAWTOOTH:        (0, 45),
            RoofTypeEnum.STEPPED:         (0, 7),
        }
        min_a, max_a = limits.get(self.roof_type, (0, 90))
        if not (min_a <= self.roof_angle_deg <= max_a):
            raise ValueError(
                f"Roof angle {self.roof_angle_deg}° is outside the valid range "
                f"[{min_a}°, {max_a}°] for {self.roof_type.value} roofs. "
                f"ASCE 7 does not provide tabulated data beyond this range."
            )
        return self


class TopographyInput(BaseModel):
    """
    Topographic feature parameters for Kzt computation per §26.8.

    All fields default to flat terrain (Kzt = 1.0).
    """

    topo_type: TopographyEnum = Field(
        default=TopographyEnum.FLAT,
        description="Site topography classification",
    )
    hill_height_ft: float = Field(
        default=0.0,
        ge=0,
        description="Height of hill or escarpment H (ft)",
    )
    half_hill_length_ft: float = Field(
        default=0.0,
        ge=0,
        description="Half-length of hill Lh (ft). Measured at half-height.",
    )
    dist_from_crest_ft: float = Field(
        default=0.0,
        description="Horizontal distance from crest x (ft). Positive = downwind.",
    )
    is_upwind: bool = Field(
        default=True,
        description="True if the building is on the upwind side of the crest.",
    )
    ground_elevation_ft: float = Field(
        default=0.0,
        ge=0,
        description="Ground elevation above mean sea level (ft). Used for Ke in ASCE 7-16+.",
    )


# ============================================================================
# Endpoint-Specific Request Payloads
# ============================================================================


class VelocityPressureRequest(BaseModel):
    """
    POST /calculate/wind/qz

    Compute velocity pressure qz at one or more heights.
    """

    project: WindProjectSetup
    geometry: BuildingGeometry
    topography: TopographyInput = TopographyInput()

    kd: float = Field(
        default=0.85,
        gt=0,
        le=1.0,
        description="Directionality factor Kd (Table 26.6-1). Default 0.85 for buildings.",
    )
    z_heights_ft: list[float] = Field(
        default=[],
        description=(
            "List of heights (ft) at which to compute qz. "
            "If empty, computes only at mean roof height h."
        ),
        examples=[[15, 20, 30, 40, 50, 62]],
    )

    @field_validator("z_heights_ft")
    @classmethod
    def validate_heights(cls, v: list[float]) -> list[float]:
        for z in v:
            if z < 0:
                raise ValueError(f"Height {z} ft is negative. All heights must be ≥ 0.")
        return sorted(set(v))


class CCPressureRequest(BaseModel):
    """
    POST /calculate/wind/cc

    Compute C&C design pressures for specified zones and effective wind areas.
    """

    project: WindProjectSetup
    geometry: BuildingGeometry
    topography: TopographyInput = TopographyInput()

    kd: float = Field(
        default=0.85,
        gt=0,
        le=1.0,
        description="Directionality factor Kd",
    )
    zones: list[CCZoneEnum] = Field(
        ...,
        min_length=1,
        description="C&C zones to analyze",
        examples=[["1", "2", "3", "4", "5"]],
    )
    eff_wind_areas_sf: list[float] = Field(
        ...,
        min_length=1,
        description="Effective wind areas to evaluate (sf)",
        examples=[[10, 20, 50, 100, 200, 500]],
    )

    @field_validator("eff_wind_areas_sf")
    @classmethod
    def validate_areas(cls, v: list[float]) -> list[float]:
        for a in v:
            if a <= 0:
                raise ValueError(
                    f"Effective wind area {a} sf is not positive. "
                    f"All areas must be > 0."
                )
        return sorted(set(v))


class MWFRSDirectionalRequest(BaseModel):
    """
    POST /calculate/wind/mwfrs/directional

    MWFRS Directional Procedure pressures (all building heights).
    """

    project: WindProjectSetup
    geometry: BuildingGeometry
    topography: TopographyInput = TopographyInput()

    kd: float = Field(default=0.85, gt=0, le=1.0)
    gust_factor_G: Optional[float] = Field(
        default=None,
        ge=0.80,
        le=2.0,
        description=(
            "Gust effect factor G. If null, computed per §26.11 "
            "(rigid default = 0.85)."
        ),
    )
    z_heights_ft: list[float] = Field(
        default=[],
        description="Custom windward wall elevation profile. If empty, auto-generated.",
    )


class MWFRSLowRiseRequest(BaseModel):
    """
    POST /calculate/wind/mwfrs/lowrise

    MWFRS Low-Rise Envelope Procedure (h ≤ 60 ft, h ≤ B).
    """

    project: WindProjectSetup
    geometry: BuildingGeometry
    topography: TopographyInput = TopographyInput()

    kd: float = Field(default=0.85, gt=0, le=1.0)


class FullWindAnalysisRequest(BaseModel):
    """
    POST /calculate/wind/full-analysis

    Run all applicable MWFRS and C&C procedures in a single request.
    """

    project: WindProjectSetup
    geometry: BuildingGeometry
    topography: TopographyInput = TopographyInput()

    kd: float = Field(default=0.85, gt=0, le=1.0)
    gust_factor_G: Optional[float] = Field(default=None, ge=0.80, le=2.0)
    cc_zones: list[CCZoneEnum] = Field(
        default=[
            CCZoneEnum.ROOF_FIELD, CCZoneEnum.ROOF_EDGE, CCZoneEnum.ROOF_CORNER,
            CCZoneEnum.WALL_FIELD, CCZoneEnum.WALL_CORNER,
        ],
    )
    cc_eff_wind_areas_sf: list[float] = Field(
        default=[10, 20, 50, 100, 200, 500],
    )
