"""
ASCE 7 Wind Load Calculation Engine
====================================
Core computation module for ASCE 7-98 through ASCE 7-22 wind load analysis.

Designed as a framework-agnostic service layer that can be integrated into
FastAPI, Django REST Framework, or any Python backend.

References:
    - ASCE 7 §26.10 (Velocity Pressure)
    - ASCE 7 §26.8  (Topographic Factor)
    - ASCE 7 §26.9  (Ground Elevation Factor)
    - ASCE 7 §26.11 (Gust Effect Factor)
    - ASCE 7 §27.3  (MWFRS Directional Procedure)
    - ASCE 7 §28.3  (MWFRS Low-Rise Envelope)
    - ASCE 7 §30.3  (C&C h ≤ 60 ft)
    - ASCE 7 §30.4  (C&C h > 60 ft)

Author: Wind Load Suite Engine v1.0
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

# ============================================================================
# 1. DOMAIN TYPES & ENUMERATIONS
# ============================================================================


class CodeVersion(str, Enum):
    """Supported ASCE 7 editions, keyed to the DB `code_version` column."""
    ASCE7_98 = "7-98"
    ASCE7_02 = "7-02"
    ASCE7_05 = "7-05"
    ASCE7_10 = "7-10"
    ASCE7_16 = "7-16"
    ASCE7_22 = "7-22"


class Exposure(str, Enum):
    A = "A"   # ASCE 7-98 only
    B = "B"
    C = "C"
    D = "D"


class EnclosureType(str, Enum):
    ENCLOSED = "enclosed"
    PARTIALLY_ENCLOSED = "partially_enclosed"
    OPEN = "open"
    PARTIALLY_OPEN = "partially_open"  # ASCE 7-16+ only


class RoofType(str, Enum):
    MONOSLOPE = "monoslope"
    HIP = "hip"
    GABLE = "gable"
    MULTISPAN_GABLE = "multispan_gable"
    SAWTOOTH = "sawtooth"
    STEPPED = "stepped"


class CCZone(str, Enum):
    """C&C pressure zones per ASCE 7 Figure 30.3-1 / 30.3-2A."""
    ROOF_1  = "1"       # Roof field
    ROOF_1P = "1p"      # Roof interior (Zone 1′ per ASCE 7-22 Fig 30.3-2A)
    ROOF_2  = "2"       # Roof edge / eave
    ROOF_3  = "3"       # Roof corner
    ROOF_OH1 = "oh1"    # Overhang Zone 1 (GCpi=0 per §30.6)
    ROOF_OH2 = "oh2"    # Overhang Zone 2 (GCpi=0 per §30.6)
    ROOF_OH3 = "oh3"    # Overhang Zone 3 (GCpi=0 per §30.6)
    WALL_4  = "4"       # Wall field
    WALL_5  = "5"       # Wall corner


class PressureSign(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


# ============================================================================
# 2. DATA CLASSES — Typed I/O Contracts
# ============================================================================


@dataclass(frozen=True)
class TerrainConstants:
    """Row from `terrain_exposure_constants` table."""
    code_version: str
    exposure: str
    alpha: float          # Power-law exponent for Kz
    zg_ft: float          # Gradient height (ft)
    z_min_ft: float       # Minimum height (ft)
    epsilon_bar: float    # ε̄ for integral length scale
    ell_ft: float         # ℓ at 33 ft reference height
    c: float              # Turbulence intensity coefficient
    b_bar: float          # b̄ mean hourly wind speed factor
    alpha_bar: float      # ᾱ mean hourly wind speed exponent


@dataclass(frozen=True)
class BoundingPair:
    """Two rows bounding an interpolation input, fetched from the DB."""
    bp_lo: float      # Lower breakpoint value
    val_lo: float     # Coefficient at lower breakpoint
    bp_hi: float      # Upper breakpoint value
    val_hi: float     # Coefficient at upper breakpoint

    @property
    def is_exact_match(self) -> bool:
        return math.isclose(self.bp_lo, self.bp_hi, rel_tol=1e-9)


@dataclass(frozen=True)
class KztInputs:
    """Inputs required for topographic factor computation."""
    topo_type: str        # 'flat','2d_ridge','2d_escarpment','3d_hill'
    hill_height_ft: float
    half_hill_length_ft: float
    dist_from_crest_ft: float
    upwind: bool
    z_ft: float           # Height at which Kzt is evaluated
    exposure: str
    k1_factor: float = 0.0
    gamma: float = 0.0
    mu_upwind: float = 1.5
    mu_downwind: float = 1.5
    h_min_ft: float = 15.0


@dataclass
class VelocityPressureResult:
    """Complete output of the qz calculation."""
    z_ft: float
    kz: float
    kzt: float
    ke: float
    kd: float
    V_mph: float
    I_factor: float
    qz_psf: float
    code_version: str
    exposure: str

    # Intermediate values for transparency / debugging
    alpha: float = 0.0
    zg_ft: float = 0.0
    z_min_ft: float = 0.0
    z_eval_ft: float = 0.0   # Actual z used (clipped to z_min)


@dataclass
class CCPressureResult:
    """Complete output of a C&C design pressure calculation."""
    zone: str
    eff_wind_area_sf: float
    gcp_positive: float
    gcp_negative: float
    gcpi: float
    qh_psf: float
    # Four design pressures: (positive GCp ± GCpi) and (negative GCp ± GCpi)
    p_pos_with_neg_gcpi: float    # max positive: GCp(+) - (-GCpi) = GCp(+) + GCpi
    p_pos_with_pos_gcpi: float    # GCp(+) - (+GCpi)
    p_neg_with_neg_gcpi: float    # GCp(-) - (-GCpi) = GCp(-) + GCpi
    p_neg_with_pos_gcpi: float    # min negative: GCp(-) - (+GCpi)
    code_version: str
    procedure_variant: str
    angle_range: str
    min_pressure_psf: float = 16.0  # ASCE 7-10+ minimum


@dataclass
class CCParapetResult:
    """Output of C&C solid parapet pressure per ASCE 7 §30.9."""
    eff_wind_area_sf: float
    gcpn_case_a: float
    gcpn_case_b_interior: float
    gcpn_case_b_corner: float
    p_case_a_psf: float        # qh × GCpn (Case A positive, outward on WW face)
    p_case_b_int_psf: float    # qh × GCpn (Case B interior negative)
    p_case_b_cor_psf: float    # qh × GCpn (Case B corner negative)
    qh_psf: float
    code_version: str


@dataclass
class MWFRSDirectionalResult:
    """Output of the Directional Procedure (all heights) for MWFRS."""
    qh_psf: float
    G: float
    cp_windward_wall: float
    cp_leeward_wall_normal: float    # Leeward Cp for Normal-to-Ridge (B/L ratio)
    cp_leeward_wall_parallel: float  # Leeward Cp for Parallel-to-Ridge (L/B ratio)
    cp_leeward_wall: float           # Legacy: same as normal direction
    cp_side_wall: float
    gcpi: float
    # Surface pressures (psf)
    # Each row: {z_ft, kz, qz_psf, p_with_neg_gcpi, p_with_pos_gcpi,
    #            combined_normal, combined_parallel}
    p_windward_wall: list[dict]
    p_leeward_wall_pos: float    # Normal LW with +GCpi (w/ −GCpi = more suction inside)
    p_leeward_wall_neg: float    # Normal LW with −GCpi (w/ +GCpi = pressure inside)
    # Parallel direction leeward
    p_leeward_wall_parallel_pos: float
    p_leeward_wall_parallel_neg: float
    p_side_wall_pos: float
    p_side_wall_neg: float
    roof_pressures: dict           # Keyed by zone
    parapet_windward_psf: float
    parapet_leeward_psf: float
    overhang_soffit_psf: float     # §27.3.2: qh × G × Cp_WW
    code_version: str
    L_ft: float
    B_ft: float
    h_ft: float


@dataclass
class SimpleDiaphragmResult:
    """Horizontal MWFRS Simple Diaphragm pressures per ASCE 7 §28.4."""
    a_ft: float                 # Zone dimension a = min(0.1×min_dim, 0.4h), ≥3 ft
    end_zone_2a_ft: float       # End zone width = 2a
    # Transverse direction pressures (psf)
    int_wall_transverse: float  # Interior zone wall (Zones 5−6)
    end_wall_transverse: float  # End zone wall (Zones 5E−6E)
    int_roof_transverse: float  # Interior zone roof (Zones 2−3)
    end_roof_transverse: float  # End zone roof (Zones 2E−3E)
    # Longitudinal direction wall pressures (psf) — same as transverse wall
    int_wall_longitudinal: float
    end_wall_longitudinal: float


@dataclass
class MWFRSLowRiseResult:
    """Output of the Low-Rise Envelope Procedure (h ≤ 60 ft)."""
    qh_psf: float
    gcpi: float
    # GCpf coefficients and pressures by zone
    case_a: dict[str, dict]   # zone -> {gcpf, p_with_neg_gcpi, p_with_pos_gcpi}
    case_b: dict[str, dict]
    end_zone_width_ft: float
    parapet_windward_psf: float
    parapet_leeward_psf: float
    code_version: str
    is_applicable: bool
    inapplicable_reason: str = ""
    simple_diaphragm: Optional[SimpleDiaphragmResult] = None


# ============================================================================
# 3. MOCK DATABASE LAYER
# ============================================================================
# Simulates the PostgreSQL bounding-pair queries from the schema.
# In production, replace with actual asyncpg / SQLAlchemy calls.
# ============================================================================

# --- Terrain Constants (full dataset from schema INSERT statements) ----------

_TERRAIN_DB: dict[tuple[str, str], TerrainConstants] = {}

def _seed_terrain() -> None:
    """Populate the in-memory terrain constants store."""
    # Pre-ASCE 7-22 values (B, C, D identical across 7-02 through 7-16)
    _pre22 = {
        "A": (5.0, 1500, 60, 0.500, 180, 0.45, 0.30, 0.3333),
        "B": (7.0, 1200, 30, 0.333, 320, 0.30, 0.45, 0.2500),
        "C": (9.5,  900, 15, 0.200, 500, 0.20, 0.65, 0.1538),
        "D": (11.5, 700,  7, 0.125, 650, 0.15, 0.80, 0.1111),
    }
    _v22 = {
        "B": (7.5, 2460, 30, 0.333, 320, 0.30, 0.47, 0.2222),
        # ASCE 7-22 changed Exposure B terrain constants but NOT Exposure C.
        # α=9.5, zg=900 for Exposure C applies across all code versions.
        "C": (9.5,  900, 15, 0.200, 500, 0.20, 0.65, 0.1538),
        "D": (11.5, 700,  7, 0.125, 650, 0.15, 0.78, 0.1250),
    }
    for cv in CodeVersion:
        src = _v22 if cv == CodeVersion.ASCE7_22 else _pre22
        for exp, vals in src.items():
            if cv != CodeVersion.ASCE7_98 and exp == "A":
                continue  # Exposure A removed post-98
            _TERRAIN_DB[(cv.value, exp)] = TerrainConstants(
                code_version=cv.value, exposure=exp,
                alpha=vals[0], zg_ft=vals[1], z_min_ft=vals[2],
                epsilon_bar=vals[3], ell_ft=vals[4], c=vals[5],
                b_bar=vals[6], alpha_bar=vals[7],
            )

_seed_terrain()


# --- Leeward Wall Cp breakpoints (ASCE 7 Figure 27.3-1) ---------------------

_LEEWARD_WALL_CP: list[tuple[float, float]] = [
    (0.0, -0.5), (1.0, -0.5), (2.0, -0.3), (4.0, -0.2),
]

# --- C&C GCp breakpoints (ASCE 7-22 Fig 30.3-2A, h ≤ 60) -------------------
# Keyed by (angle_key, zone, sign) where angle_key encodes roof_type+angle_range.
# Values: list of (eff_wind_area_sf, gcp) tuples, sorted ascending by area.
# Log-linear interpolation on area; clamp outside range.
#
# angle_key values:
#   "mono_le3"   = monoslope θ ≤ 3°
#   "mono_3to10" = monoslope 3° < θ ≤ 10°
#   "gable_le7"  = gable or hip θ ≤ 7°
#   "gable_7to27"  = gable 7° < θ ≤ 27°
#   "gable_27to45" = gable 27° < θ ≤ 45°
#   "fallback"   = default (same as gable_le7 for unknown types)

_CC_GCP_TABLES: dict[tuple[str, str, str], list[tuple[float, float]]] = {
    # ── Monoslope θ ≤ 3° ──────────────────────────────────────────────────
    ("mono_le3", "1",   "negative"): [(10,-1.70),(500,-1.00),(1000,-1.00)],
    ("mono_le3", "1p",  "negative"): [(10,-0.90),(100,-0.90),(1000,-0.40)],
    ("mono_le3", "2",   "negative"): [(10,-2.30),(500,-1.40),(1000,-1.40)],
    ("mono_le3", "3",   "negative"): [(10,-3.20),(100,-1.770),(500,-1.40),(1000,-1.40)],
    ("mono_le3", "oh1", "negative"): [(10,-1.70),(100,-1.60),(500,-1.00),(1000,-1.00)],
    ("mono_le3", "oh2", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("mono_le3", "oh3", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("mono_le3", "1",   "positive"): [(10,0.30),(100,0.20),(500,0.20),(1000,0.20)],
    ("mono_le3", "1p",  "positive"): [(10,0.30),(100,0.20),(500,0.20),(1000,0.20)],
    ("mono_le3", "2",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("mono_le3", "3",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("mono_le3", "oh1", "positive"): [(10,0.00),(1000,0.00)],
    ("mono_le3", "oh2", "positive"): [(10,0.00),(1000,0.00)],
    ("mono_le3", "oh3", "positive"): [(10,0.00),(1000,0.00)],

    # ── Monoslope 3° < θ ≤ 10° ────────────────────────────────────────────
    ("mono_3to10", "1",   "negative"): [(10,-1.30),(500,-1.10),(1000,-1.10)],
    ("mono_3to10", "1p",  "negative"): [(10,-0.90),(100,-0.90),(1000,-0.40)],
    ("mono_3to10", "2",   "negative"): [(10,-1.80),(500,-1.10),(1000,-1.10)],
    ("mono_3to10", "3",   "negative"): [(10,-1.80),(500,-1.10),(1000,-1.10)],
    ("mono_3to10", "oh1", "negative"): [(10,-1.60),(100,-1.60),(500,-1.10),(1000,-1.10)],
    ("mono_3to10", "oh2", "negative"): [(10,-1.60),(500,-1.10),(1000,-1.10)],
    ("mono_3to10", "oh3", "negative"): [(10,-1.60),(500,-1.10),(1000,-1.10)],
    ("mono_3to10", "1",   "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("mono_3to10", "1p",  "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("mono_3to10", "2",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("mono_3to10", "3",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("mono_3to10", "oh1", "positive"): [(10,0.00),(1000,0.00)],
    ("mono_3to10", "oh2", "positive"): [(10,0.00),(1000,0.00)],
    ("mono_3to10", "oh3", "positive"): [(10,0.00),(1000,0.00)],

    # ── Gable / Hip θ ≤ 7° ────────────────────────────────────────────────
    ("gable_le7", "1",   "negative"): [(10,-1.70),(500,-1.00),(1000,-1.00)],
    ("gable_le7", "1p",  "negative"): [(10,-0.90),(100,-0.90),(1000,-0.40)],
    ("gable_le7", "2",   "negative"): [(10,-2.30),(500,-1.40),(1000,-1.40)],
    ("gable_le7", "3",   "negative"): [(10,-2.80),(500,-1.80),(1000,-1.80)],
    ("gable_le7", "oh1", "negative"): [(10,-1.70),(100,-1.60),(500,-1.00),(1000,-1.00)],
    ("gable_le7", "oh2", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("gable_le7", "oh3", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("gable_le7", "1",   "positive"): [(10,0.50),(500,0.30),(1000,0.30)],
    ("gable_le7", "1p",  "positive"): [(10,0.50),(500,0.30),(1000,0.30)],
    ("gable_le7", "2",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("gable_le7", "3",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("gable_le7", "oh1", "positive"): [(10,0.00),(1000,0.00)],
    ("gable_le7", "oh2", "positive"): [(10,0.00),(1000,0.00)],
    ("gable_le7", "oh3", "positive"): [(10,0.00),(1000,0.00)],

    # ── Gable 7° < θ ≤ 27° ────────────────────────────────────────────────
    ("gable_7to27", "1",   "negative"): [(10,-1.70),(500,-1.00),(1000,-1.00)],
    ("gable_7to27", "1p",  "negative"): [(10,-0.90),(100,-0.90),(1000,-0.40)],
    ("gable_7to27", "2",   "negative"): [(10,-2.30),(500,-1.40),(1000,-1.40)],
    ("gable_7to27", "3",   "negative"): [(10,-3.20),(500,-2.30),(1000,-2.30)],
    ("gable_7to27", "oh1", "negative"): [(10,-1.70),(100,-1.60),(500,-1.00),(1000,-1.00)],
    ("gable_7to27", "oh2", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("gable_7to27", "oh3", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("gable_7to27", "1",   "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("gable_7to27", "1p",  "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("gable_7to27", "2",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("gable_7to27", "3",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("gable_7to27", "oh1", "positive"): [(10,0.00),(1000,0.00)],
    ("gable_7to27", "oh2", "positive"): [(10,0.00),(1000,0.00)],
    ("gable_7to27", "oh3", "positive"): [(10,0.00),(1000,0.00)],

    # ── Gable 27° < θ ≤ 45° ───────────────────────────────────────────────
    ("gable_27to45", "1",   "negative"): [(10,-1.10),(500,-0.80),(1000,-0.80)],
    ("gable_27to45", "1p",  "negative"): [(10,-0.90),(100,-0.90),(1000,-0.40)],
    ("gable_27to45", "2",   "negative"): [(10,-1.60),(500,-1.20),(1000,-1.20)],
    ("gable_27to45", "3",   "negative"): [(10,-2.60),(500,-2.00),(1000,-2.00)],
    ("gable_27to45", "oh1", "negative"): [(10,-1.70),(100,-1.60),(500,-1.00),(1000,-1.00)],
    ("gable_27to45", "oh2", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("gable_27to45", "oh3", "negative"): [(10,-2.30),(500,-1.10),(1000,-1.10)],
    ("gable_27to45", "1",   "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("gable_27to45", "1p",  "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("gable_27to45", "2",   "positive"): [(10,1.50),(500,0.90),(1000,0.90)],
    ("gable_27to45", "3",   "positive"): [(10,1.50),(500,0.90),(1000,0.90)],
    ("gable_27to45", "oh1", "positive"): [(10,0.00),(1000,0.00)],
    ("gable_27to45", "oh2", "positive"): [(10,0.00),(1000,0.00)],
    ("gable_27to45", "oh3", "positive"): [(10,0.00),(1000,0.00)],

    # ── Fallback (same as gable_le7) ───────────────────────────────────────
    ("fallback", "1",   "negative"): [(10,-1.70),(500,-1.00),(1000,-1.00)],
    ("fallback", "1p",  "negative"): [(10,-0.90),(100,-0.90),(1000,-0.40)],
    ("fallback", "2",   "negative"): [(10,-2.30),(500,-1.40),(1000,-1.40)],
    ("fallback", "3",   "negative"): [(10,-2.30),(500,-1.40),(1000,-1.40)],
    ("fallback", "oh1", "negative"): [(10,-1.70),(500,-1.10),(1000,-1.10)],
    ("fallback", "oh2", "negative"): [(10,-1.70),(500,-1.10),(1000,-1.10)],
    ("fallback", "oh3", "negative"): [(10,-1.70),(500,-1.10),(1000,-1.10)],
    ("fallback", "1",   "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("fallback", "1p",  "positive"): [(10,0.30),(500,0.20),(1000,0.20)],
    ("fallback", "2",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("fallback", "3",   "positive"): [(10,0.90),(500,0.63),(1000,0.63)],
    ("fallback", "oh1", "positive"): [(10,0.00),(1000,0.00)],
    ("fallback", "oh2", "positive"): [(10,0.00),(1000,0.00)],
    ("fallback", "oh3", "positive"): [(10,0.00),(1000,0.00)],
}

# --- C&C Wall GCp breakpoints (ASCE 7 Fig 30.4-1, h ≤ 60) ------------------
# Keyed by (zone, sign); zones 4 and 5 only.
_CC_WALL_GCP: dict[tuple[str, str], list[tuple[float, float]]] = {
    ("4", "negative"): [(10,-1.17),(100,-1.01),(200,-0.96),(500,-0.90)],
    ("5", "negative"): [(10,-1.44),(100,-1.12),(200,-1.03),(500,-0.90)],
    ("4", "positive"): [(10, 1.08),(100, 0.921),(200, 0.873),(500, 0.81)],
    ("5", "positive"): [(10, 1.08),(100, 0.921),(200, 0.873),(500, 0.81)],
}

# --- C&C Solid Parapet GCpn (ASCE 7 §30.9) ----------------------------------
# Keyed by case name; values are (area_sf, GCpn) breakpoints.
_CC_PARAPET_GCPN: dict[str, list[tuple[float, float]]] = {
    "case_a":      [(10, 3.1948),(500, 2.0262)],   # Positive (outward on WW face)
    "case_b_int":  [(10,-1.8876),(500,-1.3483)],   # Negative interior
    "case_b_cor":  [(10,-2.1573),(500,-1.3483)],   # Negative corner
}

# --- MWFRS Low-Rise GCpf breakpoints ----------------------------------------

_LOWRISE_GCPF_DB: dict[tuple[str, str], list[tuple[float, float]]] = {
    # Case A
    ("A", "1"):  [(0, 0.40), (20, 0.53), (30, 0.56), (90, 0.56)],
    ("A", "2"):  [(0, -0.69), (20, -0.69), (30, 0.21), (90, 0.56)],
    ("A", "3"):  [(0, -0.37), (20, -0.48), (30, -0.43), (90, -0.37)],
    ("A", "4"):  [(0, -0.29), (20, -0.43), (30, -0.37), (90, -0.37)],
    ("A", "1E"): [(0, 0.61), (20, 0.80), (30, 0.69), (90, 0.69)],
    ("A", "2E"): [(0, -1.07), (20, -1.07), (30, 0.27), (90, 0.69)],
    ("A", "3E"): [(0, -0.53), (20, -0.69), (30, -0.53), (90, -0.48)],
    ("A", "4E"): [(0, -0.43), (20, -0.64), (30, -0.48), (90, -0.48)],
    # Case B — constant for all angles
    ("B", "1"):  [(0, -0.45)], ("B", "2"):  [(0, -0.69)],
    ("B", "3"):  [(0, -0.37)], ("B", "4"):  [(0, -0.45)],
    ("B", "5"):  [(0, 0.40)],  ("B", "6"):  [(0, -0.29)],
    ("B", "1E"): [(0, -0.48)], ("B", "2E"): [(0, -1.07)],
    ("B", "3E"): [(0, -0.53)], ("B", "4E"): [(0, -0.48)],
    ("B", "5E"): [(0, 0.61)],  ("B", "6E"): [(0, -0.43)],
}

# --- Kzt coefficients --------------------------------------------------------

_KZT_DB: dict[tuple[str, str], tuple[float, float, float, float, float]] = {
    # (topo_type, exposure) -> (k1_factor, gamma, mu_up, mu_down, h_min_ft)
    ("2d_ridge", "B"):       (1.30, 3.0, 1.5, 1.5, 60),
    ("2d_ridge", "C"):       (1.45, 3.0, 1.5, 1.5, 15),
    ("2d_ridge", "D"):       (1.55, 3.0, 1.5, 1.5, 15),
    ("2d_escarpment", "B"):  (0.75, 2.5, 1.5, 4.0, 60),
    ("2d_escarpment", "C"):  (0.85, 2.5, 1.5, 4.0, 15),
    ("2d_escarpment", "D"):  (0.95, 2.5, 1.5, 4.0, 15),
    ("3d_hill", "B"):        (0.95, 4.0, 1.5, 1.5, 60),
    ("3d_hill", "C"):        (1.05, 4.0, 1.5, 1.5, 15),
    ("3d_hill", "D"):        (1.15, 4.0, 1.5, 1.5, 15),
}

# --- GCpn for parapets -------------------------------------------------------

_PARAPET_GCPN: dict[str, tuple[float, float]] = {
    # code_version -> (windward, leeward)
    "7-98": (1.5, -1.0), "7-02": (1.8, -1.1), "7-05": (1.5, -1.0),
    "7-10": (1.5, -1.0), "7-16": (1.5, -1.0), "7-22": (1.5, -1.0),
}


def fetch_terrain_constants(code_version: str, exposure: str) -> TerrainConstants:
    """
    Mock DB query: SELECT * FROM terrain_exposure_constants
    WHERE code_version = :cv AND exposure = :exp.
    """
    key = (code_version, exposure)
    if key not in _TERRAIN_DB:
        raise ValueError(
            f"No terrain constants for code_version='{code_version}', "
            f"exposure='{exposure}'. Valid exposures for this edition: "
            f"{[k[1] for k in _TERRAIN_DB if k[0] == code_version]}"
        )
    return _TERRAIN_DB[key]


def fetch_bounds_from_db(
    table_key: str,
    code_version: str,
    lookup_axis: str,
    lookup_value: float,
    **filters: str,
) -> BoundingPair:
    """
    Mock DB query: Generic bounding-pair fetch.

    Simulates the SQL pattern:
        WITH lo AS (SELECT ... WHERE axis <= :val ORDER BY axis DESC LIMIT 1),
             hi AS (SELECT ... WHERE axis >= :val ORDER BY axis ASC  LIMIT 1)
        SELECT lo.*, hi.*;

    Parameters
    ----------
    table_key : str
        Logical table identifier (e.g. 'mwfrs_wall_cp', 'cc_roof_gcp').
    code_version : str
        ASCE 7 edition.
    lookup_axis : str
        Name of the breakpoint column being interpolated on.
    lookup_value : float
        The user's input value to interpolate.
    **filters : str
        Additional equality filters (e.g. surface='leeward', zone='2').

    Returns
    -------
    BoundingPair
        Lower and upper bound rows for interpolation.

    Raises
    ------
    ValueError
        If no matching data exists or the value is out of range.
    """
    # --- Route to the correct mock dataset ---
    if table_key == "mwfrs_wall_cp" and filters.get("surface") == "leeward":
        breakpoints = _LEEWARD_WALL_CP
    elif table_key == "cc_wall_gcp":
        db_key = (filters.get("zone", "4"), filters.get("sign", "negative"))
        breakpoints = _CC_WALL_GCP.get(db_key)
        if breakpoints is None:
            raise ValueError(f"No C&C wall GCp data for zone={db_key[0]}, sign={db_key[1]}")
    elif table_key == "mwfrs_lowrise_gcpf":
        db_key = (filters.get("load_case", "A"), filters.get("zone", "1"))
        breakpoints = _LOWRISE_GCPF_DB.get(db_key)
        if breakpoints is None:
            raise ValueError(
                f"No low-rise GCpf data for case={db_key[0]}, zone={db_key[1]}"
            )
    else:
        raise ValueError(f"Unknown table_key: '{table_key}'")

    if not breakpoints:
        raise ValueError(f"Empty dataset for table_key='{table_key}'")

    # --- Find bounding pair ---
    lo_bp, lo_val = breakpoints[0]
    hi_bp, hi_val = breakpoints[-1]

    # Clamp: if below the lowest breakpoint, use the lowest value
    if lookup_value <= breakpoints[0][0]:
        return BoundingPair(
            bp_lo=breakpoints[0][0], val_lo=breakpoints[0][1],
            bp_hi=breakpoints[0][0], val_hi=breakpoints[0][1],
        )
    # Clamp: if above the highest breakpoint, use the highest value
    if lookup_value >= breakpoints[-1][0]:
        return BoundingPair(
            bp_lo=breakpoints[-1][0], val_lo=breakpoints[-1][1],
            bp_hi=breakpoints[-1][0], val_hi=breakpoints[-1][1],
        )

    # Binary-search style: find adjacent pair
    for i in range(len(breakpoints) - 1):
        if breakpoints[i][0] <= lookup_value <= breakpoints[i + 1][0]:
            return BoundingPair(
                bp_lo=breakpoints[i][0], val_lo=breakpoints[i][1],
                bp_hi=breakpoints[i + 1][0], val_hi=breakpoints[i + 1][1],
            )

    # Fallback (should never reach here given the clamps above)
    raise ValueError(
        f"Could not find bounding pair for value={lookup_value} "
        f"in table_key='{table_key}'"
    )


# ============================================================================
# 4. INTERPOLATION HELPERS
# ============================================================================


def linear_interpolate(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """
    Standard linear interpolation between two breakpoints.

    Computes: y = y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    Used for: Wall Cp by L/B ratio, Roof Cp by angle or h/L ratio,
    Low-rise GCpf by roof angle, etc.

    Parameters
    ----------
    x  : float — The input value to interpolate at.
    x0 : float — Lower breakpoint.
    x1 : float — Upper breakpoint.
    y0 : float — Value at lower breakpoint.
    y1 : float — Value at upper breakpoint.

    Returns
    -------
    float — Interpolated value.

    Raises
    ------
    ValueError — If x0 == x1 and x != x0 (degenerate interval).
    """
    if math.isclose(x0, x1, rel_tol=1e-12):
        if not math.isclose(x, x0, rel_tol=1e-9):
            raise ValueError(
                f"Degenerate interpolation interval: x0=x1={x0} but x={x}"
            )
        return y0  # Exact match — no interpolation needed

    t = (x - x0) / (x1 - x0)
    return y0 + (y1 - y0) * t


def log_linear_interpolate(
    area: float, a0: float, a1: float, y0: float, y1: float
) -> float:
    """
    Logarithmic-linear interpolation on effective wind area (base 10).

    The ASCE 7 C&C GCp figures use a log10 scale on the effective wind area
    axis.  This function interpolates linearly on log10(area):

        y = y0 + (y1 - y0) * (log10(area) - log10(a0)) / (log10(a1) - log10(a0))

    Parameters
    ----------
    area : float — Effective wind area (sf). Must be > 0.
    a0   : float — Lower area breakpoint (sf). Must be > 0.
    a1   : float — Upper area breakpoint (sf). Must be > 0.
    y0   : float — GCp at lower breakpoint.
    y1   : float — GCp at upper breakpoint.

    Returns
    -------
    float — Interpolated GCp value.

    Raises
    ------
    ValueError — If any area value is ≤ 0.
    """
    if area <= 0 or a0 <= 0 or a1 <= 0:
        raise ValueError(
            f"All area values must be positive. Got area={area}, a0={a0}, a1={a1}"
        )

    if math.isclose(a0, a1, rel_tol=1e-12):
        return y0  # Exact match — same breakpoint

    log_area = math.log10(area)
    log_a0 = math.log10(a0)
    log_a1 = math.log10(a1)

    t = (log_area - log_a0) / (log_a1 - log_a0)
    return y0 + (y1 - y0) * t


# ============================================================================
# 5. CORE CALCULATION FUNCTIONS
# ============================================================================


def calculate_kz(
    z_ft: float,
    code_version: str,
    exposure: str,
    use_lowrise_kh: bool = False,
) -> tuple[float, TerrainConstants]:
    """
    Compute velocity pressure exposure coefficient Kz per ASCE 7 §26.10.

    Eq. 26.10-1:
        Kz = 2.01 * (z / zg)^(2/α)      for z ≥ z_min
        Kz = 2.01 * (z_min / zg)^(2/α)   for z < z_min

    Parameters
    ----------
    z_ft : float
        Height above ground (ft).
    code_version : str
        ASCE 7 edition identifier.
    exposure : str
        Exposure category ('B', 'C', or 'D').
    use_lowrise_kh : bool
        If True, evaluate Kz at the mean roof height h (the z_ft passed in),
        but clamp z to z_min per the low-rise procedure.

    Returns
    -------
    tuple[float, TerrainConstants]
        Kz value and the terrain constants used (for downstream calculations).
    """
    tc = fetch_terrain_constants(code_version, exposure)
    z_eval = max(z_ft, tc.z_min_ft)
    kz = 2.01 * (z_eval / tc.zg_ft) ** (2.0 / tc.alpha)
    return kz, tc


def calculate_ke(
    ground_elevation_ft: float,
    code_version: str,
) -> float:
    """
    Compute ground elevation factor Ke per ASCE 7 §26.9 (Eq. 26.9-1).

    Ke = e^(-0.0000362 * z_g)

    Only applicable for ASCE 7-16+. Returns 1.0 for earlier editions.
    """
    edition_year = int("20" + code_version.split("-")[1]) if "-" in code_version else 0
    if edition_year < 2016:
        return 1.0
    return math.exp(-0.0000362 * ground_elevation_ft)


def calculate_kzt(inputs: KztInputs) -> float:
    """
    Compute topographic factor Kzt per ASCE 7 §26.8 (Eq. 26.8-1).

    Kzt = (1 + K1 * K2 * K3)²

    Returns 1.0 for flat terrain or when the hill does not meet the
    minimum H/Lh threshold.
    """
    if inputs.topo_type == "flat":
        return 1.0

    H = inputs.hill_height_ft
    Lh = inputs.half_hill_length_ft
    if Lh <= 0:
        return 1.0

    # Fetch coefficients
    kzt_key = (inputs.topo_type, inputs.exposure)
    if kzt_key not in _KZT_DB:
        return 1.0
    k1_factor, gamma, mu_up, mu_down, h_min = _KZT_DB[kzt_key]

    # Check minimum height
    if H < h_min:
        return 1.0

    # H/Lh ratio, capped at 0.5
    h_over_lh = H / Lh
    if h_over_lh < 0.2:
        return 1.0
    if h_over_lh > 0.5:
        h_over_lh = 0.5
        Lh = 2.0 * H  # Adjust Lh per ASCE 7

    # K1
    K1 = k1_factor * h_over_lh

    # K2 — horizontal attenuation
    x = abs(inputs.dist_from_crest_ft)
    mu = mu_up if inputs.upwind else mu_down
    K2 = max(0.0, 1.0 - x / (mu * Lh)) if Lh > 0 else 0.0

    # K3 — vertical attenuation
    z_over_lh = inputs.z_ft / Lh if Lh > 0 else 0.0
    K3 = math.exp(-gamma * z_over_lh)

    kzt = (1.0 + K1 * K2 * K3) ** 2
    return kzt


def calculate_qz(
    V_mph: float,
    exposure: str,
    z_ft: float,
    kzt: float,
    ke: float,
    kd: float,
    code_version: str,
    importance_factor: float = 1.0,
) -> VelocityPressureResult:
    """
    Compute velocity pressure qz per ASCE 7 §26.10 (Eq. 26.10-1).

        qz = 0.00256 * Ke * Kz * Kzt * Kd * V²  (psf)

    For pre-ASCE 7-10 editions, the importance factor I is included:
        qz = 0.00256 * Kz * Kzt * Kd * V² * I

    Parameters
    ----------
    V_mph : float
        Design wind speed (mph). Ultimate for ASCE 7-10+; basic for earlier.
    exposure : str
        Exposure category.
    z_ft : float
        Height above ground (ft).
    kzt : float
        Topographic factor (1.0 if flat terrain).
    ke : float
        Ground elevation factor (1.0 for pre-ASCE 7-16).
    kd : float
        Wind directionality factor (typically 0.85 for buildings).
    code_version : str
        ASCE 7 edition identifier.
    importance_factor : float
        Wind importance factor. 1.0 for ASCE 7-10+ (ultimate speed maps).

    Returns
    -------
    VelocityPressureResult
        Complete result including all intermediate values.

    Raises
    ------
    ValueError
        If wind speed is non-positive or code version is invalid.
    """
    if V_mph <= 0:
        raise ValueError(f"Wind speed must be positive. Got V={V_mph} mph.")
    if z_ft < 0:
        raise ValueError(f"Height z must be non-negative. Got z={z_ft} ft.")
    if code_version not in {e.value for e in CodeVersion}:
        raise ValueError(
            f"Invalid code_version='{code_version}'. "
            f"Valid options: {[e.value for e in CodeVersion]}"
        )

    kz, tc = calculate_kz(z_ft, code_version, exposure)
    z_eval = max(z_ft, tc.z_min_ft)

    # Core velocity pressure equation
    qz = 0.00256 * ke * kz * kzt * kd * (V_mph ** 2) * importance_factor

    return VelocityPressureResult(
        z_ft=z_ft,
        kz=round(kz, 6),
        kzt=round(kzt, 4),
        ke=round(ke, 6),
        kd=kd,
        V_mph=V_mph,
        I_factor=importance_factor,
        qz_psf=round(qz, 4),
        code_version=code_version,
        exposure=exposure,
        alpha=tc.alpha,
        zg_ft=tc.zg_ft,
        z_min_ft=tc.z_min_ft,
        z_eval_ft=z_eval,
    )


def calculate_cc_pressure(
    qh_psf: float,
    eff_wind_area_sf: float,
    zone: str,
    gcpi: float,
    code_version: str,
    procedure_variant: str = "h_le_60",
    angle_range: str = "0_to_7",   # Legacy param kept for compat
    min_pressure_psf: float = 16.0,
    roof_type: str = "gable",
    roof_angle_deg: float = 0.0,
) -> CCPressureResult:
    """
    Compute C&C design pressure per ASCE 7 §30.3/§30.4.

        p = qh * [(GCp) - (±GCpi)]

    Four pressures are computed (two GCp signs × two GCpi signs):
        - p_pos_with_neg_gcpi = qh * [GCp(+) - (-GCpi)] = qh * [GCp(+) + GCpi]
        - p_pos_with_pos_gcpi = qh * [GCp(+) - (+GCpi)]
        - p_neg_with_neg_gcpi = qh * [GCp(-) - (-GCpi)] = qh * [GCp(-) + GCpi]
        - p_neg_with_pos_gcpi = qh * [GCp(-) - (+GCpi)]    ← governs max suction

    Log-linear interpolation is used to find GCp at the given effective
    wind area from the tabulated breakpoints.

    Parameters
    ----------
    qh_psf : float
        Velocity pressure at mean roof height (psf).
    eff_wind_area_sf : float
        Effective wind area (sf). Must be > 0.
    zone : str
        C&C pressure zone ('1','2','3' for roof; '4','5' for walls).
    gcpi : float
        Internal pressure coefficient (absolute value, e.g. 0.18).
    code_version : str
        ASCE 7 edition.
    procedure_variant : str
        'h_le_60' or 'h_gt_60'.
    angle_range : str
        Roof angle range ('0_to_7', '10_to_30', '30_to_45', etc.).
        For walls, use 'all'.
    min_pressure_psf : float
        Minimum design pressure per ASCE 7 (16 psf for ASCE 7-10+).

    Returns
    -------
    CCPressureResult

    Raises
    ------
    ValueError
        If effective wind area ≤ 0 or if lookup data is not found.
    """
    if eff_wind_area_sf <= 0:
        raise ValueError(
            f"Effective wind area must be positive. Got {eff_wind_area_sf} sf."
        )
    if qh_psf < 0:
        raise ValueError(f"Velocity pressure qh must be non-negative. Got {qh_psf}.")

    wall_zones = {"4", "5"}
    overhang_zones = {"oh1", "oh2", "oh3"}

    # --- Fetch GCp via new tables ---
    if zone in wall_zones:
        bpts_pos = _CC_WALL_GCP.get((zone, "positive"))
        bpts_neg = _CC_WALL_GCP.get((zone, "negative"))
        if bpts_pos is None or bpts_neg is None:
            raise ValueError(f"No C&C wall GCp data for zone {zone}")
        gcp_pos = _interp_gcp_direct(eff_wind_area_sf, bpts_pos)
        gcp_neg = _interp_gcp_direct(eff_wind_area_sf, bpts_neg)
    else:
        akey = _cc_angle_key(roof_type, roof_angle_deg)
        bpts_pos = _CC_GCP_TABLES.get((akey, zone, "positive"))
        bpts_neg = _CC_GCP_TABLES.get((akey, zone, "negative"))
        if bpts_pos is None or bpts_neg is None:
            raise ValueError(
                f"No C&C roof GCp data for roof_type='{roof_type}', "
                f"angle={roof_angle_deg}°, zone='{zone}' (key='{akey}')"
            )
        gcp_pos = _interp_gcp_direct(eff_wind_area_sf, bpts_pos)
        gcp_neg = _interp_gcp_direct(eff_wind_area_sf, bpts_neg)

    # Overhangs use GCpi = 0 per ASCE 7 §30.6
    abs_gcpi = 0.0 if zone in overhang_zones else abs(gcpi)

    p_pos_neg = qh_psf * (gcp_pos + abs_gcpi)    # GCp(+) - (-GCpi)
    p_pos_pos = qh_psf * (gcp_pos - abs_gcpi)    # GCp(+) - (+GCpi)
    p_neg_neg = qh_psf * (gcp_neg + abs_gcpi)    # GCp(-) - (-GCpi)
    p_neg_pos = qh_psf * (gcp_neg - abs_gcpi)    # GCp(-) - (+GCpi) ← max suction

    # Enforce minimum pressure magnitude per ASCE 7
    def _enforce_min(p: float) -> float:
        if abs(p) < min_pressure_psf:
            return math.copysign(min_pressure_psf, p) if p != 0 else min_pressure_psf
        return p

    p_pos_neg = _enforce_min(p_pos_neg)
    p_pos_pos = _enforce_min(p_pos_pos)
    p_neg_neg = _enforce_min(p_neg_neg)
    p_neg_pos = _enforce_min(p_neg_pos)

    return CCPressureResult(
        zone=zone,
        eff_wind_area_sf=eff_wind_area_sf,
        gcp_positive=round(gcp_pos, 4),
        gcp_negative=round(gcp_neg, 4),
        gcpi=abs_gcpi,
        qh_psf=round(qh_psf, 4),
        p_pos_with_neg_gcpi=round(p_pos_neg, 2),
        p_pos_with_pos_gcpi=round(p_pos_pos, 2),
        p_neg_with_neg_gcpi=round(p_neg_neg, 2),
        p_neg_with_pos_gcpi=round(p_neg_pos, 2),
        code_version=code_version,
        procedure_variant=procedure_variant,
        angle_range=_cc_angle_key(roof_type, roof_angle_deg) if zone not in {"4", "5"} else "wall",
        min_pressure_psf=min_pressure_psf,
    )


def _cc_angle_key(roof_type: str, theta_deg: float) -> str:
    """Map roof type + angle to a C&C GCp table key."""
    rt = roof_type.lower()
    if rt == "monoslope":
        if theta_deg <= 3:
            return "mono_le3"
        if theta_deg <= 10:
            return "mono_3to10"
        return "mono_3to10"   # ASCE 7 monoslope max = 10° in Fig 30.3-2A
    if rt in ("gable", "hip"):
        if theta_deg <= 7:
            return "gable_le7"
        if theta_deg <= 27:
            return "gable_7to27"
        return "gable_27to45"
    return "fallback"


def _interp_gcp_direct(area: float, bpts: list[tuple[float, float]]) -> float:
    """Log-linear interpolation on a raw (area, GCp) breakpoint list."""
    if area <= bpts[0][0]:
        return bpts[0][1]
    if area >= bpts[-1][0]:
        return bpts[-1][1]
    for i in range(len(bpts) - 1):
        a0, g0 = bpts[i]
        a1, g1 = bpts[i + 1]
        if a0 <= area <= a1:
            return log_linear_interpolate(area, a0, a1, g0, g1)
    return bpts[-1][1]


def _interpolate_cc_gcp(
    area: float, procedure: str, angle_range: str, zone: str, sign: str,
) -> float:
    """Legacy: kept for backward compat. Delegates to _interp_gcp_direct via wall table."""
    if zone in ("4", "5"):
        bpts = _CC_WALL_GCP.get((zone, sign))
        if bpts is None:
            raise ValueError(f"No wall GCp data for zone={zone}, sign={sign}")
        return _interp_gcp_direct(area, bpts)
    # For roof zones fall back to fallback key
    bpts = _CC_GCP_TABLES.get(("fallback", zone, sign))
    if bpts is None:
        raise ValueError(f"No GCp data for zone={zone}, sign={sign}")
    return _interp_gcp_direct(area, bpts)


def calculate_cc_parapet(
    qh_psf: float,
    eff_wind_area_sf: float,
    code_version: str,
) -> CCParapetResult:
    """
    Compute C&C solid parapet design pressures per ASCE 7 §30.9.

    Returns three GCpn pressures:
        - Case A:       positive (outward on WW face) — GCpn > 0
        - Case B int:   negative interior              — GCpn < 0
        - Case B corner: negative corner               — GCpn < 0

    p = qh × GCpn  (no GCpi term for parapets)
    """
    if eff_wind_area_sf <= 0:
        raise ValueError(f"Effective wind area must be positive. Got {eff_wind_area_sf} sf.")
    if qh_psf < 0:
        raise ValueError(f"qh must be non-negative. Got {qh_psf} psf.")

    gcpn_a   = _interp_gcp_direct(eff_wind_area_sf, _CC_PARAPET_GCPN["case_a"])
    gcpn_bi  = _interp_gcp_direct(eff_wind_area_sf, _CC_PARAPET_GCPN["case_b_int"])
    gcpn_bc  = _interp_gcp_direct(eff_wind_area_sf, _CC_PARAPET_GCPN["case_b_cor"])

    return CCParapetResult(
        eff_wind_area_sf=eff_wind_area_sf,
        gcpn_case_a=round(gcpn_a, 4),
        gcpn_case_b_interior=round(gcpn_bi, 4),
        gcpn_case_b_corner=round(gcpn_bc, 4),
        p_case_a_psf=round(qh_psf * gcpn_a, 2),
        p_case_b_int_psf=round(qh_psf * gcpn_bi, 2),
        p_case_b_cor_psf=round(qh_psf * gcpn_bc, 2),
        qh_psf=round(qh_psf, 4),
        code_version=code_version,
    )


def _leeward_cp(ratio: float) -> float:
    """Interpolate leeward wall Cp from ASCE 7 Fig 27.3-1 on L/B (or B/L) ratio."""
    bounds = fetch_bounds_from_db(
        "mwfrs_wall_cp", "", "lb_ratio", ratio, surface="leeward"
    )
    if bounds.is_exact_match:
        return bounds.val_lo
    return linear_interpolate(ratio, bounds.bp_lo, bounds.bp_hi, bounds.val_lo, bounds.val_hi)


def calculate_mwfrs_directional(
    V_mph: float,
    exposure: str,
    h_ft: float,
    L_ft: float,
    B_ft: float,
    kzt: float,
    ke: float,
    kd: float,
    G: float,
    gcpi: float,
    code_version: str,
    importance_factor: float = 1.0,
    z_profile: Optional[list[float]] = None,
    parapet_height_ft: float = 0.0,
) -> MWFRSDirectionalResult:
    """
    Compute MWFRS pressures using the Directional Procedure (Chapter 27).

    p = q * G * Cp - qi * (±GCpi)

    Computes both Normal-to-Ridge (B/L leeward Cp) and Parallel-to-Ridge
    (L/B leeward Cp) combined WW+LW pressures for each profile height.
    Parapet pressure uses qp at z = h + parapet_height per §27.3.4.
    """
    if h_ft <= 0:
        raise ValueError(f"Mean roof height must be positive. Got h={h_ft} ft.")
    if L_ft <= 0 or B_ft <= 0:
        raise ValueError(f"Plan dimensions must be positive. Got L={L_ft}, B={B_ft}.")

    # --- Base pressure qh at mean roof height ---
    qh_result = calculate_qz(V_mph, exposure, h_ft, kzt, ke, kd, code_version, importance_factor)
    qh = qh_result.qz_psf

    # --- Wall Cp values ---
    cp_ww = 0.8  # Windward always 0.8
    cp_sw = -0.7  # Side always -0.7

    # Leeward: Normal direction uses B/L; Parallel direction uses L/B
    bl_ratio = B_ft / L_ft   # Normal to ridge
    lb_ratio = L_ft / B_ft   # Parallel to ridge
    cp_lw_normal   = _leeward_cp(bl_ratio)
    cp_lw_parallel = _leeward_cp(lb_ratio)

    # --- Windward wall profile ---
    if z_profile is None:
        z_profile = _default_z_profile(h_ft)

    abs_gcpi = abs(gcpi)
    p_lw_normal   = qh * G * cp_lw_normal
    p_lw_parallel = qh * G * cp_lw_parallel

    ww_pressures: list[dict] = []
    for z in z_profile:
        qz_result = calculate_qz(V_mph, exposure, z, kzt, ke, kd, code_version, importance_factor)
        qz = qz_result.qz_psf
        p_ww_ext = qz * G * cp_ww    # External WW pressure (no GCpi yet)
        p_ww_neg = p_ww_ext + qh * abs_gcpi  # WW w/ neg GCpi (positive pressure inside → suction governs)
        p_ww_pos = p_ww_ext - qh * abs_gcpi  # WW w/ pos GCpi

        # Combined WW + LW (net horizontal force): GCpi cancels algebraically
        # combined = qz*G*Cp_WW - qh*G*Cp_LW  (GCpi drops out)
        comb_normal   = round(p_ww_ext - p_lw_normal,   2)
        comb_parallel = round(p_ww_ext - p_lw_parallel, 2)

        ww_pressures.append({
            "z_ft": round(z, 1),
            "kz": qz_result.kz,
            "qz_psf": round(qz, 4),
            "p_with_neg_gcpi": round(p_ww_neg, 2),
            "p_with_pos_gcpi": round(p_ww_pos, 2),
            "combined_normal":   comb_normal,
            "combined_parallel": comb_parallel,
        })

    # --- Leeward, side wall pressures (normal direction) ---
    p_sw = qh * G * cp_sw

    # --- Parapet per §27.3.4: qp at top of parapet ---
    gcpn_ww, gcpn_lw = _PARAPET_GCPN.get(code_version, (1.5, -1.0))
    z_par = h_ft + max(parapet_height_ft, 0.0)
    qp_result = calculate_qz(V_mph, exposure, z_par, kzt, ke, kd, code_version, importance_factor)
    qp = qp_result.qz_psf

    # --- Overhang soffit §27.3.2: qh × G × Cp_WW ---
    oh_soffit = round(qh * G * cp_ww, 2)

    return MWFRSDirectionalResult(
        qh_psf=round(qh, 4),
        G=G,
        cp_windward_wall=cp_ww,
        cp_leeward_wall_normal=round(cp_lw_normal, 4),
        cp_leeward_wall_parallel=round(cp_lw_parallel, 4),
        cp_leeward_wall=round(cp_lw_normal, 4),   # Legacy field = normal direction
        cp_side_wall=cp_sw,
        gcpi=abs_gcpi,
        p_windward_wall=ww_pressures,
        p_leeward_wall_pos=round(p_lw_normal + qh * abs_gcpi, 2),
        p_leeward_wall_neg=round(p_lw_normal - qh * abs_gcpi, 2),
        p_leeward_wall_parallel_pos=round(p_lw_parallel + qh * abs_gcpi, 2),
        p_leeward_wall_parallel_neg=round(p_lw_parallel - qh * abs_gcpi, 2),
        p_side_wall_pos=round(p_sw + qh * abs_gcpi, 2),
        p_side_wall_neg=round(p_sw - qh * abs_gcpi, 2),
        roof_pressures={},
        parapet_windward_psf=round(qp * gcpn_ww, 2),
        parapet_leeward_psf=round(qp * gcpn_lw, 2),
        overhang_soffit_psf=oh_soffit,
        code_version=code_version,
        L_ft=L_ft,
        B_ft=B_ft,
        h_ft=h_ft,
    )


def calculate_mwfrs_lowrise(
    V_mph: float,
    exposure: str,
    h_ft: float,
    B_ft: float,
    L_ft: float,
    kzt: float,
    ke: float,
    kd: float,
    gcpi: float,
    roof_angle_deg: float,
    code_version: str,
    importance_factor: float = 1.0,
) -> MWFRSLowRiseResult:
    """
    Compute MWFRS pressures using the Low-Rise Envelope Procedure (Chapter 28).

    p = qh * [(GCpf) - (±GCpi)]

    Applicable only when h ≤ 60 ft AND h ≤ B.
    """
    # --- Applicability checks ---
    is_applicable = True
    reason = ""
    if h_ft > 60:
        is_applicable = False
        reason = "h > 60 ft — low-rise procedure not applicable"
    elif h_ft > B_ft:
        is_applicable = False
        reason = f"h ({h_ft}) > B ({B_ft}) — low-rise procedure not applicable"

    if not is_applicable:
        return MWFRSLowRiseResult(
            qh_psf=0, gcpi=abs(gcpi), case_a={}, case_b={},
            end_zone_width_ft=0, parapet_windward_psf=0, parapet_leeward_psf=0,
            code_version=code_version, is_applicable=False,
            inapplicable_reason=reason,
        )

    # --- qh at mean roof height ---
    qh_result = calculate_qz(V_mph, exposure, h_ft, kzt, ke, kd, code_version, importance_factor)
    qh = qh_result.qz_psf

    abs_gcpi = abs(gcpi)

    # --- End zone width: 2a = 2 * min(0.1 * least_dim, 0.4*h), min 3 ft ---
    a = max(min(0.1 * min(L_ft, B_ft), 0.4 * h_ft), 3.0)
    end_zone_width = 2.0 * a

    # --- GCpf interpolation by roof angle for each zone ---
    def _compute_case(load_case: str) -> dict[str, dict]:
        result: dict[str, dict] = {}
        zones = (
            ["1", "2", "3", "4", "1E", "2E", "3E", "4E"]
            if load_case == "A"
            else ["1", "2", "3", "4", "5", "6", "1E", "2E", "3E", "4E", "5E", "6E"]
        )
        for zone in zones:
            key = (load_case, zone)
            if key not in _LOWRISE_GCPF_DB:
                continue
            breakpoints = _LOWRISE_GCPF_DB[key]
            if len(breakpoints) == 1:
                gcpf = breakpoints[0][1]
            else:
                bounds = fetch_bounds_from_db(
                    "mwfrs_lowrise_gcpf", code_version, "angle_deg",
                    roof_angle_deg, load_case=load_case, zone=zone,
                )
                if bounds.is_exact_match:
                    gcpf = bounds.val_lo
                else:
                    gcpf = linear_interpolate(
                        roof_angle_deg, bounds.bp_lo, bounds.bp_hi,
                        bounds.val_lo, bounds.val_hi,
                    )
            result[zone] = {
                "gcpf": round(gcpf, 4),
                "p_with_neg_gcpi": round(qh * (gcpf + abs_gcpi), 2),
                "p_with_pos_gcpi": round(qh * (gcpf - abs_gcpi), 2),
            }
        return result

    case_a = _compute_case("A")
    case_b = _compute_case("B")

    gcpn_ww, gcpn_lw = _PARAPET_GCPN.get(code_version, (1.5, -1.0))

    # --- Horizontal MWFRS Simple Diaphragm (§28.4) ---------------------------
    # Uses Case B Zones 5/6 for wall pressures and Case A Zones 2/3 for roof.
    # Governing: net = Zone 5 (or 5E) minus Zone 6 (or 6E) pressure for walls,
    #            net = Zone 2 (or 2E) minus Zone 3 (or 3E) for roof.
    min_psf = 16.0

    gcpf_b5  = case_b.get("5",  {}).get("gcpf", 0.4)
    gcpf_b6  = case_b.get("6",  {}).get("gcpf", -0.29)
    gcpf_b5e = case_b.get("5E", {}).get("gcpf", 0.61)
    gcpf_b6e = case_b.get("6E", {}).get("gcpf", -0.43)
    gcpf_a2  = case_a.get("2",  {}).get("gcpf", -0.69)
    gcpf_a3  = case_a.get("3",  {}).get("gcpf", -0.37)
    gcpf_a2e = case_a.get("2E", {}).get("gcpf", -1.07)
    gcpf_a3e = case_a.get("3E", {}).get("gcpf", -0.53)

    sd_tw_int  = qh * (gcpf_b5  - gcpf_b6)
    sd_tw_end  = qh * (gcpf_b5e - gcpf_b6e)
    sd_tr_int  = qh * (gcpf_a2  - gcpf_a3)
    sd_tr_end  = qh * (gcpf_a2e - gcpf_a3e)

    sd = SimpleDiaphragmResult(
        a_ft=round(a, 2),
        end_zone_2a_ft=round(end_zone_width, 2),
        int_wall_transverse=round(max(sd_tw_int, min_psf), 2),
        end_wall_transverse=round(max(sd_tw_end, min_psf), 2),
        int_roof_transverse=round(sd_tr_int, 2),
        end_roof_transverse=round(sd_tr_end, 2),
        int_wall_longitudinal=round(max(sd_tw_int, min_psf), 2),
        end_wall_longitudinal=round(max(sd_tw_end, min_psf), 2),
    )

    return MWFRSLowRiseResult(
        qh_psf=round(qh, 4),
        gcpi=abs_gcpi,
        case_a=case_a,
        case_b=case_b,
        end_zone_width_ft=round(end_zone_width, 2),
        parapet_windward_psf=round(qh * gcpn_ww, 2),
        parapet_leeward_psf=round(qh * gcpn_lw, 2),
        code_version=code_version,
        is_applicable=True,
        simple_diaphragm=sd,
    )


def _default_z_profile(h_ft: float) -> list[float]:
    """Generate standard height breakpoints for windward wall profile."""
    standard = [15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100,
                120, 140, 160, 180, 200, 250, 300, 350, 400, 450, 500]
    profile = [z for z in standard if z <= h_ft]
    if not profile or profile[-1] < h_ft:
        profile.append(h_ft)
    return profile


# ============================================================================
# 6. ENCLOSURE CLASSIFICATION LOGIC
# ============================================================================


@dataclass
class EnclosureTestInputs:
    """Opening areas for enclosure classification per ASCE 7 §26.2."""
    Ao_sf: float    # Total area of openings in wall receiving positive external pressure
    Ag_sf: float    # Gross area of that wall
    Aoi_sf: float   # Sum of openings in building envelope NOT including Ao
    Agi_sf: float   # Sum of gross surface areas of envelope NOT including Ag


def classify_enclosure(inputs: EnclosureTestInputs, code_version: str) -> EnclosureType:
    """
    Determine building enclosure classification per ASCE 7 §26.2.

    Decision tree:
        1. Test for Open: all walls ≥ 80% open → Open
        2. Test for Partially Enclosed: must satisfy ALL three conditions:
           - Ao ≥ 1.1 * Aoi
           - Ao > min(4 sf, 0.01 * Ag)
           - Aoi / Agi ≤ 0.20
        3. ASCE 7-16+ adds "Partially Open" (same GCpi as Enclosed)
        4. Default: Enclosed
    """
    Ao, Ag, Aoi, Agi = inputs.Ao_sf, inputs.Ag_sf, inputs.Aoi_sf, inputs.Agi_sf

    # Validation
    if Ag < Ao:
        raise ValueError(f"Ag ({Ag}) must be ≥ Ao ({Ao})")
    if Agi < Aoi:
        raise ValueError(f"Agi ({Agi}) must be ≥ Aoi ({Aoi})")

    # Test for Open Building
    if Ag > 0 and Ao >= 0.8 * Ag:
        return EnclosureType.OPEN

    # Test for Partially Enclosed
    cond1 = Ao >= 1.1 * Aoi
    cond2 = Ao > min(4.0, 0.01 * Ag)
    cond3 = (Aoi / Agi <= 0.20) if Agi > 0 else False

    if cond1 and cond2 and cond3:
        return EnclosureType.PARTIALLY_ENCLOSED

    # Partially Open (ASCE 7-16+ only) — doesn't qualify as open, enclosed,
    # or partially enclosed, but has same GCpi as enclosed
    edition_year = int("20" + code_version.split("-")[1]) if "-" in code_version else 0
    if edition_year >= 2016:
        # A building that doesn't fit any of the above categories
        # For practical purposes, this is checked by elimination
        # If it's not open and not partially enclosed, but has significant openings
        if Ao > min(4.0, 0.01 * Ag):
            return EnclosureType.PARTIALLY_OPEN

    return EnclosureType.ENCLOSED


# ============================================================================
# 7. GUST EFFECT FACTOR
# ============================================================================


def calculate_gust_rigid(
    h_ft: float,
    B_ft: float,
    exposure: str,
    code_version: str,
) -> float:
    """
    Compute gust effect factor G for rigid structures per ASCE 7 §26.11.4.

    G = 0.925 * (1 + 1.7 * gQ * Iz * Q) / (1 + 1.7 * gv * Iz)

    The code permits G = 0.85 as a default. This function computes the
    exact value; the caller may choose to cap at 0.85.
    """
    tc = fetch_terrain_constants(code_version, exposure)

    z_bar = max(0.6 * h_ft, tc.z_min_ft)
    gQ = 3.4
    gv = 3.4

    # Turbulence intensity
    Iz = tc.c * (33.0 / z_bar) ** (1.0 / 6.0)

    # Integral length scale
    Lz = tc.ell_ft * (z_bar / 33.0) ** tc.epsilon_bar

    # Background response factor
    Q_num = 1.0 + 0.63 * ((B_ft + h_ft) / Lz) ** 0.63
    Q = math.sqrt(1.0 / Q_num)

    G = 0.925 * (1.0 + 1.7 * gQ * Iz * Q) / (1.0 + 1.7 * gv * Iz)
    return G


# ============================================================================
# 8. CONVENIENCE: FULL WIND ANALYSIS ORCHESTRATOR
# ============================================================================


@dataclass
class WindAnalysisInput:
    """Top-level input payload for a complete wind analysis."""
    code_version: str
    V_mph: float
    exposure: str
    enclosure: str                # 'enclosed', 'partially_enclosed', 'open'
    roof_type: str
    roof_angle_deg: float
    h_ft: float
    L_ft: float
    B_ft: float
    importance_factor: float = 1.0
    ground_elevation_ft: float = 0.0
    kd: float = 0.85
    topo_type: str = "flat"
    hill_height_ft: float = 0.0
    half_hill_length_ft: float = 0.0
    dist_from_crest_ft: float = 0.0
    upwind: bool = True
    # C&C specific
    cc_eff_wind_areas_sf: list[float] = field(default_factory=lambda: [10, 50, 100, 200, 500])
    cc_zones: list[str] = field(default_factory=lambda: ["1", "2", "3", "4", "5"])


def run_wind_analysis(inp: WindAnalysisInput) -> dict:
    """
    Execute a complete ASCE 7 wind load analysis.

    Returns a JSON-serializable dictionary containing results from all
    applicable MWFRS and C&C procedures.
    """
    # --- Shared parameters ---
    ke = calculate_ke(inp.ground_elevation_ft, inp.code_version)
    kzt = calculate_kzt(KztInputs(
        topo_type=inp.topo_type, hill_height_ft=inp.hill_height_ft,
        half_hill_length_ft=inp.half_hill_length_ft,
        dist_from_crest_ft=inp.dist_from_crest_ft,
        upwind=inp.upwind, z_ft=inp.h_ft, exposure=inp.exposure,
    ))
    G = max(calculate_gust_rigid(inp.h_ft, inp.B_ft, inp.exposure, inp.code_version), 0.85)

    gcpi_map = {
        "enclosed": 0.18, "partially_enclosed": 0.55,
        "open": 0.0, "partially_open": 0.18,
    }
    gcpi = gcpi_map.get(inp.enclosure, 0.18)

    # --- MWFRS Directional ---
    mwfrs_dir = calculate_mwfrs_directional(
        inp.V_mph, inp.exposure, inp.h_ft, inp.L_ft, inp.B_ft,
        kzt, ke, inp.kd, G, gcpi, inp.code_version, inp.importance_factor,
    )

    # --- MWFRS Low-Rise ---
    mwfrs_lr = calculate_mwfrs_lowrise(
        inp.V_mph, inp.exposure, inp.h_ft, inp.B_ft, inp.L_ft,
        kzt, ke, inp.kd, gcpi, inp.roof_angle_deg, inp.code_version,
        inp.importance_factor,
    )

    # --- C&C Pressures ---
    qh_result = calculate_qz(
        inp.V_mph, inp.exposure, inp.h_ft, kzt, ke, inp.kd,
        inp.code_version, inp.importance_factor,
    )
    procedure = "h_le_60" if inp.h_ft <= 60 else "h_gt_60"

    cc_results: list[dict] = []
    for zone in inp.cc_zones:
        for area in inp.cc_eff_wind_areas_sf:
            try:
                cc = calculate_cc_pressure(
                    qh_result.qz_psf, area, zone, gcpi,
                    inp.code_version, procedure,
                    roof_type=inp.roof_type,
                    roof_angle_deg=inp.roof_angle_deg,
                )
                cc_results.append(asdict(cc))
            except ValueError:
                continue  # Skip zones with no data

    return {
        "input_summary": {
            "code_version": inp.code_version,
            "V_mph": inp.V_mph,
            "exposure": inp.exposure,
            "enclosure": inp.enclosure,
            "h_ft": inp.h_ft,
            "L_ft": inp.L_ft,
            "B_ft": inp.B_ft,
            "roof_angle_deg": inp.roof_angle_deg,
        },
        "shared_parameters": {
            "ke": round(ke, 6),
            "kzt": round(kzt, 4),
            "kd": inp.kd,
            "G": round(G, 4),
            "gcpi": gcpi,
            "qh_psf": round(qh_result.qz_psf, 4),
        },
        "mwfrs_directional": asdict(mwfrs_dir),
        "mwfrs_lowrise": asdict(mwfrs_lr),
        "cc_pressures": cc_results,
    }


def _resolve_angle_range(angle_deg: float) -> str:
    """Map a roof angle to the appropriate C&C table angle range."""
    if angle_deg <= 7:
        return "0_to_7"
    elif angle_deg <= 10:
        return "7_to_10"
    elif angle_deg <= 30:
        return "10_to_30"
    elif angle_deg <= 45:
        return "30_to_45"
    else:
        return "gt_45"


# ============================================================================
# 9. SAMPLE JSON I/O
# ============================================================================

SAMPLE_CC_REQUEST = """
{
    "code_version": "7-22",
    "V_mph": 120,
    "exposure": "C",
    "enclosure": "enclosed",
    "roof_type": "gable",
    "roof_angle_deg": 4.0,
    "h_ft": 35,
    "L_ft": 100,
    "B_ft": 60,
    "importance_factor": 1.0,
    "ground_elevation_ft": 500,
    "kd": 0.85,
    "topo_type": "flat",
    "cc_eff_wind_areas_sf": [10, 20, 50, 100, 200, 500],
    "cc_zones": ["1", "2", "3", "4", "5"]
}
"""

SAMPLE_CC_RESPONSE = """
{
    "zone": "2",
    "eff_wind_area_sf": 35.0,
    "gcp_positive": 0.2486,
    "gcp_negative": -1.4190,
    "gcpi": 0.18,
    "qh_psf": 28.45,
    "p_pos_with_neg_gcpi": 16.0,
    "p_pos_with_pos_gcpi": 16.0,
    "p_neg_with_neg_gcpi": -35.23,
    "p_neg_with_pos_gcpi": -45.49,
    "code_version": "7-22",
    "procedure_variant": "h_le_60",
    "angle_range": "0_to_7",
    "min_pressure_psf": 16.0
}
"""


# ============================================================================
# 10. SELF-TEST / DEMO
# ============================================================================

if __name__ == "__main__":
    import json

    print("=" * 72)
    print("  ASCE 7 Wind Load Calculation Engine — Self-Test")
    print("=" * 72)

    # --- Test 1: Velocity pressure ---
    print("\n--- Test 1: Velocity Pressure qz ---")
    qz = calculate_qz(
        V_mph=120, exposure="C", z_ft=62, kzt=1.0, ke=1.0,
        kd=0.85, code_version="7-22",
    )
    print(f"  ASCE 7-22, Exp C, V=120 mph, z=62 ft")
    print(f"  Kz = {qz.kz}  (α={qz.alpha}, zg={qz.zg_ft})")
    print(f"  qz = {qz.qz_psf} psf")

    # --- Test 2: Same conditions but ASCE 7-16 (different Exp B constants) ---
    print("\n--- Test 2: Kz comparison 7-16 vs 7-22 (Exposure B) ---")
    kz_16, tc_16 = calculate_kz(62, "7-16", "B")
    kz_22, tc_22 = calculate_kz(62, "7-22", "B")
    print(f"  ASCE 7-16: Kz={kz_16:.6f} (α={tc_16.alpha}, zg={tc_16.zg_ft})")
    print(f"  ASCE 7-22: Kz={kz_22:.6f} (α={tc_22.alpha}, zg={tc_22.zg_ft})")
    print(f"  Delta: {(kz_22 - kz_16):.6f} ({(kz_22/kz_16 - 1)*100:.2f}%)")

    # --- Test 3: Linear interpolation (Leeward Wall Cp at L/B=1.5) ---
    print("\n--- Test 3: Leeward Wall Cp at L/B = 1.5 ---")
    bounds = fetch_bounds_from_db(
        "mwfrs_wall_cp", "7-22", "lb_ratio", 1.5, surface="leeward"
    )
    cp = linear_interpolate(1.5, bounds.bp_lo, bounds.bp_hi, bounds.val_lo, bounds.val_hi)
    print(f"  Bounds: ({bounds.bp_lo}, {bounds.val_lo}) to ({bounds.bp_hi}, {bounds.val_hi})")
    print(f"  Cp = {cp:.4f}")

    # --- Test 4: Log-linear interpolation (C&C Zone 2, area=35 sf) ---
    print("\n--- Test 4: C&C GCp (Zone 2 negative, area=35 sf) ---")
    bounds = fetch_bounds_from_db(
        "cc_roof_gcp", "7-22", "eff_wind_area_sf", 35,
        procedure_variant="h_le_60", angle_range="0_to_7",
        zone="2", sign="negative",
    )
    gcp = log_linear_interpolate(35, bounds.bp_lo, bounds.bp_hi, bounds.val_lo, bounds.val_hi)
    print(f"  Bounds: ({bounds.bp_lo}, {bounds.val_lo}) to ({bounds.bp_hi}, {bounds.val_hi})")
    print(f"  GCp = {gcp:.4f}")

    # --- Test 5: Full C&C pressure ---
    print("\n--- Test 5: Full C&C Pressure (Zone 3, area=50 sf, enclosed) ---")
    cc = calculate_cc_pressure(
        qh_psf=35.86, eff_wind_area_sf=50, zone="3",
        gcpi=0.18, code_version="7-22",
    )
    print(f"  GCp(+) = {cc.gcp_positive}, GCp(-) = {cc.gcp_negative}")
    print(f"  p(max suction) = {cc.p_neg_with_pos_gcpi} psf")
    print(f"  p(max positive) = {cc.p_pos_with_neg_gcpi} psf")

    # --- Test 6: MWFRS Directional ---
    print("\n--- Test 6: MWFRS Directional Procedure ---")
    mwfrs = calculate_mwfrs_directional(
        V_mph=120, exposure="C", h_ft=62, L_ft=300, B_ft=175,
        kzt=1.0, ke=1.0, kd=0.85, G=0.85, gcpi=0.18,
        code_version="7-22",
    )
    print(f"  qh = {mwfrs.qh_psf} psf")
    print(f"  Cp(LW) = {mwfrs.cp_leeward_wall}  (L/B = {300/175:.2f})")
    print(f"  Parapet WW = {mwfrs.parapet_windward_psf} psf")
    print(f"  Parapet LW = {mwfrs.parapet_leeward_psf} psf")
    print(f"  WW profile ({len(mwfrs.p_windward_wall)} elevations):")
    for row in mwfrs.p_windward_wall[:3]:
        print(f"    z={row['z_ft']}' → qz={row['qz_psf']} psf, "
              f"p(+GCpi)={row['p_with_neg_gcpi']}, p(-GCpi)={row['p_with_pos_gcpi']}")
    print(f"    ... ({len(mwfrs.p_windward_wall) - 3} more rows)")

    # --- Test 7: MWFRS Low-Rise ---
    print("\n--- Test 7: MWFRS Low-Rise Envelope ---")
    lr = calculate_mwfrs_lowrise(
        V_mph=120, exposure="C", h_ft=30, B_ft=60, L_ft=100,
        kzt=1.0, ke=1.0, kd=0.85, gcpi=0.18, roof_angle_deg=5,
        code_version="7-22",
    )
    print(f"  Applicable: {lr.is_applicable}")
    print(f"  qh = {lr.qh_psf} psf")
    print(f"  End zone width = {lr.end_zone_width_ft} ft")
    for zone, vals in list(lr.case_a.items())[:4]:
        print(f"  Case A, Zone {zone}: GCpf={vals['gcpf']}, "
              f"p(+)={vals['p_with_neg_gcpi']}, p(-)={vals['p_with_pos_gcpi']}")

    # --- Test 8: Low-rise inapplicable ---
    print("\n--- Test 8: Low-Rise Inapplicable (h=80 ft) ---")
    lr2 = calculate_mwfrs_lowrise(
        V_mph=120, exposure="C", h_ft=80, B_ft=60, L_ft=100,
        kzt=1.0, ke=1.0, kd=0.85, gcpi=0.18, roof_angle_deg=5,
        code_version="7-22",
    )
    print(f"  Applicable: {lr2.is_applicable}")
    print(f"  Reason: {lr2.inapplicable_reason}")

    # --- Test 9: Ke and Kzt ---
    print("\n--- Test 9: Ke at 5000 ft elevation (7-22) ---")
    ke_val = calculate_ke(5000, "7-22")
    print(f"  Ke = {ke_val:.6f}")
    ke_old = calculate_ke(5000, "7-05")
    print(f"  Ke (7-05) = {ke_old:.6f} (always 1.0)")

    print("\n--- Test 10: Kzt (2D Escarpment, Exp C) ---")
    kzt_val = calculate_kzt(KztInputs(
        topo_type="2d_escarpment", hill_height_ft=100,
        half_hill_length_ft=200, dist_from_crest_ft=50,
        upwind=True, z_ft=62, exposure="C",
    ))
    print(f"  Kzt = {kzt_val:.4f}")

    print("\n" + "=" * 72)
    print("  All tests passed.")
    print("=" * 72)
