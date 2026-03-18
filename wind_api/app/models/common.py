"""
Shared enumerations and base types for the Wind Load API.

These mirror the domain types in the engine but are Pydantic-native,
giving us automatic OpenAPI schema generation and JSON serialization.
"""

from __future__ import annotations

from enum import Enum


class CodeVersionEnum(str, Enum):
    """Supported ASCE 7 editions."""
    ASCE7_98 = "7-98"
    ASCE7_02 = "7-02"
    ASCE7_05 = "7-05"
    ASCE7_10 = "7-10"
    ASCE7_16 = "7-16"
    ASCE7_22 = "7-22"


class ExposureEnum(str, Enum):
    """Terrain exposure categories per ASCE 7 §26.7."""
    B = "B"
    C = "C"
    D = "D"


class RiskCategoryEnum(str, Enum):
    """Building risk categories per ASCE 7 Table 1.5-1."""
    I   = "I"
    II  = "II"
    III = "III"
    IV  = "IV"


class EnclosureEnum(str, Enum):
    """Building enclosure classifications per ASCE 7 §26.2."""
    ENCLOSED            = "enclosed"
    PARTIALLY_ENCLOSED  = "partially_enclosed"
    OPEN                = "open"
    PARTIALLY_OPEN      = "partially_open"


class RoofTypeEnum(str, Enum):
    """Roof geometry types."""
    MONOSLOPE       = "monoslope"
    HIP             = "hip"
    GABLE           = "gable"
    MULTISPAN_GABLE = "multispan_gable"
    SAWTOOTH        = "sawtooth"
    STEPPED         = "stepped"


class CCZoneEnum(str, Enum):
    """C&C pressure zones per ASCE 7 Figure 30.3-1 / 30.3-2A."""
    ROOF_FIELD      = "1"    # Roof field
    ROOF_INTERIOR   = "1p"   # Roof interior (Zone 1′ per ASCE 7-22 Fig 30.3-2A)
    ROOF_EDGE       = "2"    # Roof edge / eave
    ROOF_CORNER     = "3"    # Roof corner
    OVERHANG_1      = "oh1"  # Overhang Zone 1 (GCpi=0 per §30.6)
    OVERHANG_2      = "oh2"  # Overhang Zone 2 (GCpi=0 per §30.6)
    OVERHANG_3      = "oh3"  # Overhang Zone 3 (GCpi=0 per §30.6)
    WALL_FIELD      = "4"    # Wall field
    WALL_CORNER     = "5"    # Wall corner


class TopographyEnum(str, Enum):
    """Site topography classification per ASCE 7 §26.8."""
    FLAT            = "flat"
    RIDGE_2D        = "2d_ridge"
    ESCARPMENT_2D   = "2d_escarpment"
    HILL_3D         = "3d_hill"
