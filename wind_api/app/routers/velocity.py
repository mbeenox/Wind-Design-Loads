"""
POST /calculate/wind/qz — Velocity Pressure Endpoint

Computes velocity pressure qz per ASCE 7 Eq. 26.10-1 at one or more
heights above ground. This is the foundational calculation that feeds
every MWFRS and C&C pressure computation.

If no z_heights_ft are specified, qz is computed only at mean roof height h.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.requests import VelocityPressureRequest
from app.models.responses import VelocityPressureResponse, VelocityPressureAtHeight
from app.services.engine import (
    calculate_qz,
    calculate_ke,
    calculate_kzt,
    KztInputs,
)

router = APIRouter(prefix="/calculate/wind", tags=["Velocity Pressure"])


@router.post(
    "/qz",
    response_model=VelocityPressureResponse,
    summary="Compute velocity pressure qz at specified heights",
    response_description="Velocity pressure results with full Kz breakdown",
)
async def compute_qz(
    payload: VelocityPressureRequest,
    db: AsyncSession = Depends(get_db),
) -> VelocityPressureResponse:
    """
    **ASCE 7 §26.10 — Velocity Pressure**

    Computes: `qz = 0.00256 · Ke · Kz · Kzt · Kd · V²`

    The endpoint automatically:
    - Selects the correct terrain constants (α, zg) for the requested ASCE 7 edition
    - Computes Ke from ground elevation (ASCE 7-16+ only)
    - Computes Kzt from topographic inputs
    - Evaluates Kz at each requested height z (clamping to z_min)
    """
    proj = payload.project
    geo = payload.geometry
    topo = payload.topography

    # --- Compute shared factors ---
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

    # --- Build height list ---
    heights = payload.z_heights_ft if payload.z_heights_ft else [geo.h_ft]

    # --- Compute qz at each height ---
    results: list[VelocityPressureAtHeight] = []
    for z in heights:
        try:
            r = calculate_qz(
                V_mph=proj.V_mph,
                exposure=proj.exposure.value,
                z_ft=z,
                kzt=kzt,
                ke=ke,
                kd=payload.kd,
                code_version=proj.code_version.value,
                importance_factor=proj.importance_factor,
            )
            results.append(VelocityPressureAtHeight(
                z_ft=r.z_ft,
                z_eval_ft=r.z_eval_ft,
                kz=r.kz,
                kzt=r.kzt,
                ke=r.ke,
                kd=r.kd,
                qz_psf=r.qz_psf,
                alpha=r.alpha,
                zg_ft=r.zg_ft,
                z_min_ft=r.z_min_ft,
            ))
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Error computing qz at z={z} ft: {e}",
            )

    return VelocityPressureResponse(
        code_version=proj.code_version.value,
        V_mph=proj.V_mph,
        exposure=proj.exposure.value,
        importance_factor=proj.importance_factor,
        pressures=results,
    )
