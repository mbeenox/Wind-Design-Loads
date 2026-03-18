"""
Pydantic response (outbound) schemas for the Wind Load API.

These models define the exact JSON shape the frontend receives.
FastAPI serializes the engine's dataclass results through these.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ============================================================================
# Shared / Nested Response Components
# ============================================================================


class SharedParameters(BaseModel):
    """Parameters computed once and shared across all procedures."""
    ke: float = Field(..., description="Ground elevation factor Ke (§26.9)")
    kzt: float = Field(..., description="Topographic factor Kzt (§26.8)")
    kd: float = Field(..., description="Directionality factor Kd (Table 26.6-1)")
    G: float = Field(..., description="Gust effect factor G (§26.11)")
    gcpi: float = Field(..., description="Internal pressure coefficient ±GCpi (§26.13)")
    qh_psf: float = Field(..., description="Velocity pressure at mean roof height (psf)")


class WindwardWallPressure(BaseModel):
    """Windward wall pressure at a single elevation."""
    z_ft: float = Field(..., description="Height above ground (ft)")
    kz: float = Field(..., description="Velocity pressure exposure coefficient at z")
    qz_psf: float = Field(..., description="Velocity pressure at z (psf)")
    p_with_neg_gcpi: float = Field(
        ..., description="qz·G·Cp + qh·|GCpi| — negative internal pressure case (psf)"
    )
    p_with_pos_gcpi: float = Field(
        ..., description="qz·G·Cp − qh·|GCpi| — positive internal pressure case (psf)"
    )
    combined_normal: float = Field(
        ..., description="Combined WW+LW (Normal to Ridge): qz·G·Cp_WW − qh·G·Cp_LW (psf)"
    )
    combined_parallel: float = Field(
        ..., description="Combined WW+LW (Parallel to Ridge): qz·G·Cp_WW − qh·G·Cp_LW_par (psf)"
    )


# ============================================================================
# Velocity Pressure Response
# ============================================================================


class VelocityPressureAtHeight(BaseModel):
    """qz result at a single elevation z."""
    z_ft: float
    z_eval_ft: float = Field(..., description="Actual z used in Kz (clipped to z_min)")
    kz: float
    kzt: float
    ke: float
    kd: float
    qz_psf: float
    alpha: float = Field(..., description="Exposure power-law exponent α")
    zg_ft: float = Field(..., description="Gradient height zg (ft)")
    z_min_ft: float = Field(..., description="Minimum height z_min (ft)")


class VelocityPressureResponse(BaseModel):
    """Response for POST /calculate/wind/qz"""
    code_version: str
    V_mph: float
    exposure: str
    importance_factor: float
    pressures: list[VelocityPressureAtHeight]


# ============================================================================
# C&C Pressure Response
# ============================================================================


class CCZonePressure(BaseModel):
    """C&C design pressure result for one zone at one effective wind area."""
    zone: str = Field(
        ...,
        description=(
            "C&C zone: '1'=roof field, '1p'=roof interior (Zone 1′), "
            "'2'=edge/eave, '3'=corner, "
            "'oh1'/'oh2'/'oh3'=overhangs (GCpi=0), "
            "'4'=wall field, '5'=wall corner"
        )
    )
    eff_wind_area_sf: float = Field(..., description="Effective wind area (sf)")
    gcp_positive: float = Field(..., description="Positive external pressure coefficient GCp(+)")
    gcp_negative: float = Field(..., description="Negative external pressure coefficient GCp(−)")
    gcpi_used: float = Field(
        0.0, description="GCpi actually applied (0 for overhang zones per §30.6)"
    )
    p_pos_with_neg_gcpi: float = Field(
        ..., description="qh·[GCp(+) + |GCpi|] — maximum positive pressure (psf)"
    )
    p_pos_with_pos_gcpi: float = Field(
        ..., description="qh·[GCp(+) − |GCpi|] (psf)"
    )
    p_neg_with_neg_gcpi: float = Field(
        ..., description="qh·[GCp(−) + |GCpi|] (psf)"
    )
    p_neg_with_pos_gcpi: float = Field(
        ..., description="qh·[GCp(−) − |GCpi|] — maximum suction (psf)"
    )


class CCParapetPressure(BaseModel):
    """C&C solid parapet pressure per ASCE 7 §30.9 at one effective wind area."""
    eff_wind_area_sf: float
    gcpn_case_a: float = Field(..., description="GCpn Case A (positive, outward on WW face)")
    gcpn_case_b_interior: float = Field(..., description="GCpn Case B interior (negative)")
    gcpn_case_b_corner: float = Field(..., description="GCpn Case B corner (negative)")
    p_case_a_psf: float = Field(..., description="qh × GCpn Case A (psf)")
    p_case_b_int_psf: float = Field(..., description="qh × GCpn Case B interior (psf)")
    p_case_b_cor_psf: float = Field(..., description="qh × GCpn Case B corner (psf)")


class CCPressureResponse(BaseModel):
    """Response for POST /calculate/wind/cc"""
    code_version: str
    procedure_variant: str = Field(..., description="'h_le_60' or 'h_gt_60'")
    angle_range: str
    qh_psf: float
    gcpi: float
    min_pressure_psf: float
    zone_dimension_a_ft: float = Field(..., description="C&C zone dimension 'a' (ft)")
    pressures: list[CCZonePressure]
    parapet_pressures: list[CCParapetPressure] = Field(
        default_factory=list,
        description="C&C solid parapet §30.9 pressures (populated when parapet_height > 0)"
    )


# ============================================================================
# MWFRS Directional Response
# ============================================================================


class MWFRSDirectionalResponse(BaseModel):
    """Response for POST /calculate/wind/mwfrs/directional"""
    code_version: str
    qh_psf: float
    G: float
    gcpi: float
    L_ft: float
    B_ft: float
    h_ft: float

    cp_windward_wall: float
    cp_leeward_wall: float = Field(..., description="Leeward Cp — Normal to Ridge direction (B/L)")
    cp_leeward_wall_normal: float = Field(..., description="Leeward Cp Normal to Ridge (B/L)")
    cp_leeward_wall_parallel: float = Field(..., description="Leeward Cp Parallel to Ridge (L/B)")
    cp_side_wall: float

    windward_wall_profile: list[WindwardWallPressure]

    leeward_wall_pos_psf: float = Field(..., description="Normal LW with −GCpi (psf)")
    leeward_wall_neg_psf: float = Field(..., description="Normal LW with +GCpi (psf)")
    leeward_wall_parallel_pos_psf: float = Field(..., description="Parallel LW with −GCpi (psf)")
    leeward_wall_parallel_neg_psf: float = Field(..., description="Parallel LW with +GCpi (psf)")
    side_wall_pos_psf: float
    side_wall_neg_psf: float

    parapet_windward_psf: float = Field(
        ..., description="MWFRS WW parapet: qp × GCpn_WW at z=h+parapet_height (psf)"
    )
    parapet_leeward_psf: float = Field(
        ..., description="MWFRS LW parapet: qp × GCpn_LW at z=h+parapet_height (psf)"
    )
    overhang_soffit_psf: float = Field(
        ..., description="Overhang soffit pressure §27.3.2: qh × G × Cp_WW (psf)"
    )


# ============================================================================
# MWFRS Low-Rise Response
# ============================================================================


class LowRiseZonePressure(BaseModel):
    """Pressure for a single zone in the low-rise envelope procedure."""
    zone: str
    gcpf: float = Field(..., description="Combined pressure coefficient GCpf from Figure 28.3-1")
    p_with_neg_gcpi: float = Field(..., description="qh·(GCpf + |GCpi|) (psf)")
    p_with_pos_gcpi: float = Field(..., description="qh·(GCpf − |GCpi|) (psf)")


class SimpleDiaphragmPressure(BaseModel):
    """Horizontal MWFRS Simple Diaphragm pressures per §28.4."""
    a_ft: float = Field(..., description="Zone dimension a (ft)")
    end_zone_2a_ft: float = Field(..., description="End zone width 2a (ft)")
    int_wall_transverse: float = Field(..., description="Interior zone wall, transverse (psf)")
    end_wall_transverse: float = Field(..., description="End zone wall, transverse (psf)")
    int_roof_transverse: float = Field(..., description="Interior zone roof, transverse (psf)")
    end_roof_transverse: float = Field(..., description="End zone roof, transverse (psf)")
    int_wall_longitudinal: float = Field(..., description="Interior zone wall, longitudinal (psf)")
    end_wall_longitudinal: float = Field(..., description="End zone wall, longitudinal (psf)")


class MWFRSLowRiseResponse(BaseModel):
    """Response for POST /calculate/wind/mwfrs/lowrise"""
    code_version: str
    is_applicable: bool
    inapplicable_reason: str = ""
    qh_psf: float
    gcpi: float
    end_zone_width_ft: float = Field(
        ..., description="End zone width = 2a (ft)"
    )
    case_a: list[LowRiseZonePressure] = Field(
        ..., description="Case A (transverse) zone pressures"
    )
    case_b: list[LowRiseZonePressure] = Field(
        ..., description="Case B (longitudinal) zone pressures"
    )
    parapet_windward_psf: float
    parapet_leeward_psf: float
    simple_diaphragm: Optional[SimpleDiaphragmPressure] = Field(
        None, description="§28.4 Horizontal MWFRS Simple Diaphragm pressures (null when not applicable)"
    )


# ============================================================================
# Full Analysis Response
# ============================================================================


class FullWindAnalysisResponse(BaseModel):
    """Response for POST /calculate/wind/full-analysis"""
    shared: SharedParameters
    mwfrs_directional: MWFRSDirectionalResponse
    mwfrs_lowrise: MWFRSLowRiseResponse
    cc: CCPressureResponse
