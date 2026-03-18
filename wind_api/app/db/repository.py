"""
Database ORM models and repository query methods.

Maps directly to the PostgreSQL schema from Step 2.
Each public function performs a single, typed query and returns
a domain dataclass (or raises ValueError if no data is found).

In the current build, these functions fall back to the in-memory mock
data from the engine module when no DB session is available. This allows
the API to run without PostgreSQL for development and testing.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, String, SmallInteger, Numeric, Boolean, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

# Import domain types from the engine
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
from engine import (
    TerrainConstants,
    BoundingPair,
    fetch_terrain_constants as _mock_fetch_terrain,
    fetch_bounds_from_db as _mock_fetch_bounds,
)


# ============================================================================
# ORM Models (map to the PostgreSQL schema from Step 2)
# ============================================================================

class Base(DeclarativeBase):
    pass


class TerrainExposureORM(Base):
    """Maps to `terrain_exposure_constants` table."""
    __tablename__ = "terrain_exposure_constants"

    id           = Column(SmallInteger, primary_key=True, autoincrement=True)
    code_version = Column(String(8), nullable=False)
    exposure     = Column(String(1), nullable=False)
    alpha        = Column(Numeric(6, 4), nullable=False)
    zg_ft        = Column(Numeric(8, 2), nullable=False)
    z_min_ft     = Column(Numeric(6, 2), nullable=False)
    epsilon_bar  = Column(Numeric(8, 5), nullable=False)
    ell_ft       = Column(Numeric(8, 2), nullable=False)
    c            = Column(Numeric(6, 4), nullable=False)
    b_bar        = Column(Numeric(8, 5), nullable=False)
    alpha_bar    = Column(Numeric(8, 5), nullable=False)


class MWFRSWallCpORM(Base):
    """Maps to `mwfrs_wall_cp` table."""
    __tablename__ = "mwfrs_wall_cp"

    id           = Column(SmallInteger, primary_key=True, autoincrement=True)
    code_version = Column(String(8), nullable=False)
    surface      = Column(String(20), nullable=False)
    lb_ratio     = Column(Numeric(6, 3), nullable=True)
    cp           = Column(Numeric(6, 4), nullable=False)


class CCRoofGCpORM(Base):
    """Maps to `cc_roof_gcp` table."""
    __tablename__ = "cc_roof_gcp"

    id                = Column(SmallInteger, primary_key=True, autoincrement=True)
    code_version      = Column(String(8), nullable=False)
    procedure_variant = Column(String(20), nullable=False)
    roof_type_id      = Column(SmallInteger, nullable=True)
    angle_range       = Column(String(15), nullable=False)
    zone              = Column(String(4), nullable=False)
    sign              = Column(String(8), nullable=False)
    eff_wind_area_sf  = Column(Numeric(8, 2), nullable=False)
    gcp               = Column(Numeric(7, 4), nullable=False)


# ============================================================================
# Repository Functions (async queries with mock fallback)
# ============================================================================

async def fetch_terrain_constants_db(
    db: Optional[AsyncSession],
    code_version: str,
    exposure: str,
) -> TerrainConstants:
    """
    Fetch terrain exposure constants from the database.

    Falls back to in-memory mock data if db is None (development mode).
    """
    # --- Live DB path ---
    if db is not None:
        stmt = (
            select(TerrainExposureORM)
            .where(
                and_(
                    TerrainExposureORM.code_version == code_version,
                    TerrainExposureORM.exposure == exposure,
                )
            )
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(
                f"No terrain constants in DB for code_version='{code_version}', "
                f"exposure='{exposure}'."
            )
        return TerrainConstants(
            code_version=row.code_version,
            exposure=row.exposure,
            alpha=float(row.alpha),
            zg_ft=float(row.zg_ft),
            z_min_ft=float(row.z_min_ft),
            epsilon_bar=float(row.epsilon_bar),
            ell_ft=float(row.ell_ft),
            c=float(row.c),
            b_bar=float(row.b_bar),
            alpha_bar=float(row.alpha_bar),
        )

    # --- Mock fallback ---
    return _mock_fetch_terrain(code_version, exposure)


async def fetch_bounding_pair_db(
    db: Optional[AsyncSession],
    table_name: str,
    code_version: str,
    lookup_axis: str,
    lookup_value: float,
    **filters: str,
) -> BoundingPair:
    """
    Fetch bounding pair for interpolation from the database.

    Implements the SQL pattern:
        lo = SELECT ... WHERE axis <= :val ORDER BY axis DESC LIMIT 1
        hi = SELECT ... WHERE axis >= :val ORDER BY axis ASC  LIMIT 1

    Falls back to in-memory mock data if db is None.
    """
    # --- Live DB path (example for mwfrs_wall_cp) ---
    if db is not None and table_name == "mwfrs_wall_cp":
        surface = filters.get("surface", "leeward")

        # Lower bound
        lo_stmt = (
            select(MWFRSWallCpORM.lb_ratio, MWFRSWallCpORM.cp)
            .where(
                and_(
                    MWFRSWallCpORM.code_version == code_version,
                    MWFRSWallCpORM.surface == surface,
                    MWFRSWallCpORM.lb_ratio.isnot(None),
                    MWFRSWallCpORM.lb_ratio <= lookup_value,
                )
            )
            .order_by(MWFRSWallCpORM.lb_ratio.desc())
            .limit(1)
        )
        # Upper bound
        hi_stmt = (
            select(MWFRSWallCpORM.lb_ratio, MWFRSWallCpORM.cp)
            .where(
                and_(
                    MWFRSWallCpORM.code_version == code_version,
                    MWFRSWallCpORM.surface == surface,
                    MWFRSWallCpORM.lb_ratio.isnot(None),
                    MWFRSWallCpORM.lb_ratio >= lookup_value,
                )
            )
            .order_by(MWFRSWallCpORM.lb_ratio.asc())
            .limit(1)
        )

        lo_result = await db.execute(lo_stmt)
        hi_result = await db.execute(hi_stmt)
        lo_row = lo_result.first()
        hi_row = hi_result.first()

        if lo_row is None or hi_row is None:
            raise ValueError(
                f"No bounding data in {table_name} for "
                f"code_version='{code_version}', value={lookup_value}"
            )

        return BoundingPair(
            bp_lo=float(lo_row.lb_ratio),
            val_lo=float(lo_row.cp),
            bp_hi=float(hi_row.lb_ratio),
            val_hi=float(hi_row.cp),
        )

    # --- Mock fallback ---
    return _mock_fetch_bounds(
        table_key=table_name,
        code_version=code_version,
        lookup_axis=lookup_axis,
        lookup_value=lookup_value,
        **filters,
    )
