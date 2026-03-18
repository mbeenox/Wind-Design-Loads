"""
MWFRS Wind Pressure Endpoints

POST /calculate/wind/mwfrs/directional — Directional Procedure (Ch. 27)
POST /calculate/wind/mwfrs/lowrise     — Low-Rise Envelope (Ch. 28)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.requests import MWFRSDirectionalRequest, MWFRSLowRiseRequest
from app.models.responses import (
    MWFRSDirectionalResponse,
    MWFRSLowRiseResponse,
    WindwardWallPressure,
    LowRiseZonePressure,
)
from app.services.engine import (
    calculate_mwfrs_directional,
    calculate_mwfrs_lowrise,
    calculate_ke,
    calculate_kzt,
    calculate_gust_rigid,
    KztInputs,
)
from app.models.responses import SimpleDiaphragmPressure

router = APIRouter(prefix="/calculate/wind/mwfrs", tags=["MWFRS"])

_GCPI_MAP: dict[str, float] = {
    "enclosed": 0.18, "partially_enclosed": 0.55,
    "open": 0.00, "partially_open": 0.18,
}


# ============================================================================
# Directional Procedure (Chapter 27, Part 1)
# ============================================================================

@router.post(
    "/directional",
    response_model=MWFRSDirectionalResponse,
    summary="MWFRS Directional Procedure — wall and roof pressures at all heights",
)
async def compute_mwfrs_directional(
    payload: MWFRSDirectionalRequest,
    db: AsyncSession = Depends(get_db),
) -> MWFRSDirectionalResponse:
    """
    **ASCE 7 Chapter 27, Part 1 — Directional Procedure**

    Applicable to all enclosed, partially enclosed, and partially open buildings
    of any height. Computes:
    - Wall pressures: `p = q·G·Cp − qi·(±GCpi)`
    - Windward wall pressure profile at multiple elevations
    - Parapet pressures: `pp = qp·GCpn`
    """
    proj = payload.project
    geo = payload.geometry
    topo = payload.topography

    try:
        ke = calculate_ke(topo.ground_elevation_ft, proj.code_version.value)
        kzt = calculate_kzt(KztInputs(
            topo_type=topo.topo_type.value,
            hill_height_ft=topo.hill_height_ft,
            half_hill_length_ft=topo.half_hill_length_ft,
            dist_from_crest_ft=topo.dist_from_crest_ft,
            upwind=topo.is_upwind,
            z_ft=geo.h_ft,
            exposure=proj.exposure.value,
        ))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # Gust effect factor: user override or computed
    if payload.gust_factor_G is not None:
        G = payload.gust_factor_G
    else:
        G = max(
            calculate_gust_rigid(
                geo.h_ft, geo.B_ft, proj.exposure.value, proj.code_version.value
            ),
            0.85,
        )

    gcpi = _GCPI_MAP.get(proj.enclosure.value, 0.18)

    try:
        result = calculate_mwfrs_directional(
            V_mph=proj.V_mph,
            exposure=proj.exposure.value,
            h_ft=geo.h_ft,
            L_ft=geo.L_ft,
            B_ft=geo.B_ft,
            kzt=kzt,
            ke=ke,
            kd=payload.kd,
            G=G,
            gcpi=gcpi,
            code_version=proj.code_version.value,
            importance_factor=proj.importance_factor,
            z_profile=payload.z_heights_ft if payload.z_heights_ft else None,
            parapet_height_ft=geo.parapet_height_ft,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # Transform engine output → response model
    ww_profile = [
        WindwardWallPressure(
            z_ft=row["z_ft"],
            kz=row["kz"],
            qz_psf=row["qz_psf"],
            p_with_neg_gcpi=row["p_with_neg_gcpi"],
            p_with_pos_gcpi=row["p_with_pos_gcpi"],
            combined_normal=row["combined_normal"],
            combined_parallel=row["combined_parallel"],
        )
        for row in result.p_windward_wall
    ]

    return MWFRSDirectionalResponse(
        code_version=result.code_version,
        qh_psf=result.qh_psf,
        G=result.G,
        gcpi=result.gcpi,
        L_ft=result.L_ft,
        B_ft=result.B_ft,
        h_ft=result.h_ft,
        cp_windward_wall=result.cp_windward_wall,
        cp_leeward_wall=result.cp_leeward_wall,
        cp_leeward_wall_normal=result.cp_leeward_wall_normal,
        cp_leeward_wall_parallel=result.cp_leeward_wall_parallel,
        cp_side_wall=result.cp_side_wall,
        windward_wall_profile=ww_profile,
        leeward_wall_pos_psf=result.p_leeward_wall_pos,
        leeward_wall_neg_psf=result.p_leeward_wall_neg,
        leeward_wall_parallel_pos_psf=result.p_leeward_wall_parallel_pos,
        leeward_wall_parallel_neg_psf=result.p_leeward_wall_parallel_neg,
        side_wall_pos_psf=result.p_side_wall_pos,
        side_wall_neg_psf=result.p_side_wall_neg,
        parapet_windward_psf=result.parapet_windward_psf,
        parapet_leeward_psf=result.parapet_leeward_psf,
        overhang_soffit_psf=result.overhang_soffit_psf,
    )


# ============================================================================
# Low-Rise Envelope Procedure (Chapter 28)
# ============================================================================

@router.post(
    "/lowrise",
    response_model=MWFRSLowRiseResponse,
    summary="MWFRS Low-Rise Envelope Procedure (h ≤ 60 ft, h ≤ B)",
)
async def compute_mwfrs_lowrise(
    payload: MWFRSLowRiseRequest,
    db: AsyncSession = Depends(get_db),
) -> MWFRSLowRiseResponse:
    """
    **ASCE 7 Chapter 28 — Low-Rise Envelope Procedure**

    Applicable only when h ≤ 60 ft AND h ≤ least horizontal dimension B.
    Returns zone-based GCpf pressures for Cases A (transverse) and B (longitudinal).

    If the building does not qualify, returns `is_applicable: false` with
    a reason string — the response is still 200 OK.
    """
    proj = payload.project
    geo = payload.geometry
    topo = payload.topography

    try:
        ke = calculate_ke(topo.ground_elevation_ft, proj.code_version.value)
        kzt = calculate_kzt(KztInputs(
            topo_type=topo.topo_type.value,
            hill_height_ft=topo.hill_height_ft,
            half_hill_length_ft=topo.half_hill_length_ft,
            dist_from_crest_ft=topo.dist_from_crest_ft,
            upwind=topo.is_upwind,
            z_ft=geo.h_ft,
            exposure=proj.exposure.value,
        ))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    gcpi = _GCPI_MAP.get(proj.enclosure.value, 0.18)

    try:
        result = calculate_mwfrs_lowrise(
            V_mph=proj.V_mph,
            exposure=proj.exposure.value,
            h_ft=geo.h_ft,
            B_ft=geo.B_ft,
            L_ft=geo.L_ft,
            kzt=kzt,
            ke=ke,
            kd=payload.kd,
            gcpi=gcpi,
            roof_angle_deg=geo.roof_angle_deg,
            code_version=proj.code_version.value,
            importance_factor=proj.importance_factor,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # Transform dict-of-dicts → list of response models
    def _to_zone_list(case_dict: dict[str, dict]) -> list[LowRiseZonePressure]:
        return [
            LowRiseZonePressure(
                zone=zone,
                gcpf=vals["gcpf"],
                p_with_neg_gcpi=vals["p_with_neg_gcpi"],
                p_with_pos_gcpi=vals["p_with_pos_gcpi"],
            )
            for zone, vals in case_dict.items()
        ]

    # Convert SimpleDiaphragmResult → SimpleDiaphragmPressure if present
    sd_response = None
    if result.simple_diaphragm is not None:
        sd = result.simple_diaphragm
        sd_response = SimpleDiaphragmPressure(
            a_ft=sd.a_ft,
            end_zone_2a_ft=sd.end_zone_2a_ft,
            int_wall_transverse=sd.int_wall_transverse,
            end_wall_transverse=sd.end_wall_transverse,
            int_roof_transverse=sd.int_roof_transverse,
            end_roof_transverse=sd.end_roof_transverse,
            int_wall_longitudinal=sd.int_wall_longitudinal,
            end_wall_longitudinal=sd.end_wall_longitudinal,
        )

    return MWFRSLowRiseResponse(
        code_version=result.code_version,
        is_applicable=result.is_applicable,
        inapplicable_reason=result.inapplicable_reason,
        qh_psf=result.qh_psf,
        gcpi=result.gcpi,
        end_zone_width_ft=result.end_zone_width_ft,
        case_a=_to_zone_list(result.case_a),
        case_b=_to_zone_list(result.case_b),
        parapet_windward_psf=result.parapet_windward_psf,
        parapet_leeward_psf=result.parapet_leeward_psf,
        simple_diaphragm=sd_response,
    )
