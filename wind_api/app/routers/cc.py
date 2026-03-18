"""
POST /calculate/wind/cc — Components & Cladding Pressure Endpoint

Computes C&C design pressures per ASCE 7 §30.3 (h ≤ 60 ft) or
§30.4 (h > 60 ft) for user-specified zones and effective wind areas.

The endpoint:
  - Computes qh at mean roof height
  - Resolves the correct roof angle range for GCp table lookup
  - Log-linear interpolates GCp at each effective wind area
  - Returns four design pressures per zone/area combination
    (positive/negative GCp × positive/negative GCpi)
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.requests import CCPressureRequest
from app.models.responses import CCPressureResponse, CCZonePressure, CCParapetPressure
from app.services.engine import (
    calculate_qz,
    calculate_ke,
    calculate_kzt,
    calculate_cc_pressure,
    calculate_cc_parapet,
    KztInputs,
    _resolve_angle_range,
)

router = APIRouter(prefix="/calculate/wind", tags=["Components & Cladding"])


# --- GCpi lookup by enclosure classification ---
_GCPI_MAP: dict[str, float] = {
    "enclosed": 0.18,
    "partially_enclosed": 0.55,
    "open": 0.00,
    "partially_open": 0.18,
}


@router.post(
    "/cc",
    response_model=CCPressureResponse,
    summary="Compute C&C design pressures by zone and effective wind area",
    response_description=(
        "Tabulated C&C pressures with GCp coefficients, "
        "four design pressure combinations per zone/area"
    ),
)
async def compute_cc(
    payload: CCPressureRequest,
    db: AsyncSession = Depends(get_db),
) -> CCPressureResponse:
    """
    **ASCE 7 §30.3/§30.4 — Components & Cladding**

    Computes: `p = qh · [(GCp) − (±GCpi)]`

    GCp is determined by **log-linear interpolation** on effective wind area
    from the tabulated ASCE 7 figures (e.g., Figure 30.3-1 for h ≤ 60').

    Four pressures are returned per zone/area:
    - **p_pos_with_neg_gcpi**: max positive = qh·[GCp(+) + |GCpi|]
    - **p_pos_with_pos_gcpi**: = qh·[GCp(+) − |GCpi|]
    - **p_neg_with_neg_gcpi**: = qh·[GCp(−) + |GCpi|]
    - **p_neg_with_pos_gcpi**: max suction = qh·[GCp(−) − |GCpi|]
    """
    proj = payload.project
    geo = payload.geometry
    topo = payload.topography

    # --- Shared factors ---
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

    # --- qh at mean roof height ---
    qh_result = calculate_qz(
        V_mph=proj.V_mph,
        exposure=proj.exposure.value,
        z_ft=geo.h_ft,
        kzt=kzt,
        ke=ke,
        kd=payload.kd,
        code_version=proj.code_version.value,
        importance_factor=proj.importance_factor,
    )
    qh = qh_result.qz_psf

    # --- Resolve parameters ---
    gcpi = _GCPI_MAP.get(proj.enclosure.value, 0.18)
    procedure = "h_le_60" if geo.h_ft <= 60 else "h_gt_60"
    angle_range = _resolve_angle_range(geo.roof_angle_deg)

    # --- C&C zone dimension 'a' per §26.2 ---
    a = max(min(0.1 * min(geo.L_ft, geo.B_ft), 0.4 * geo.h_ft), 3.0)

    # --- Compute pressures for each zone × area combination ---
    pressures: list[CCZonePressure] = []
    errors: list[str] = []

    for zone in payload.zones:
        for area in payload.eff_wind_areas_sf:
            try:
                result = calculate_cc_pressure(
                    qh_psf=qh,
                    eff_wind_area_sf=area,
                    zone=zone.value,
                    gcpi=gcpi,
                    code_version=proj.code_version.value,
                    procedure_variant=procedure,
                    roof_type=geo.roof_type.value,
                    roof_angle_deg=geo.roof_angle_deg,
                )
                pressures.append(CCZonePressure(
                    zone=result.zone,
                    eff_wind_area_sf=result.eff_wind_area_sf,
                    gcp_positive=result.gcp_positive,
                    gcp_negative=result.gcp_negative,
                    gcpi_used=result.gcpi,
                    p_pos_with_neg_gcpi=result.p_pos_with_neg_gcpi,
                    p_pos_with_pos_gcpi=result.p_pos_with_pos_gcpi,
                    p_neg_with_neg_gcpi=result.p_neg_with_neg_gcpi,
                    p_neg_with_pos_gcpi=result.p_neg_with_pos_gcpi,
                ))
            except ValueError as e:
                errors.append(f"Zone {zone.value}, area {area} sf: {e}")

    if not pressures:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No valid C&C data for the given inputs. Errors: {errors}",
        )

    # --- C&C Solid Parapet §30.9 (when parapet height > 0) ---
    parapet_pressures: list[CCParapetPressure] = []
    if geo.parapet_height_ft > 0:
        par_areas = [10, 20, 50, 100, 200, 500]
        for ar in par_areas:
            try:
                pr = calculate_cc_parapet(qh, ar, proj.code_version.value)
                parapet_pressures.append(CCParapetPressure(
                    eff_wind_area_sf=pr.eff_wind_area_sf,
                    gcpn_case_a=pr.gcpn_case_a,
                    gcpn_case_b_interior=pr.gcpn_case_b_interior,
                    gcpn_case_b_corner=pr.gcpn_case_b_corner,
                    p_case_a_psf=pr.p_case_a_psf,
                    p_case_b_int_psf=pr.p_case_b_int_psf,
                    p_case_b_cor_psf=pr.p_case_b_cor_psf,
                ))
            except ValueError:
                pass

    return CCPressureResponse(
        code_version=proj.code_version.value,
        procedure_variant=procedure,
        angle_range=angle_range,
        qh_psf=round(qh, 4),
        gcpi=gcpi,
        min_pressure_psf=16.0,
        zone_dimension_a_ft=round(a, 2),
        pressures=pressures,
        parapet_pressures=parapet_pressures,
    )
