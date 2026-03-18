"""
Application configuration via environment variables.
Uses pydantic-settings for type-safe config loading.

In production, these values come from environment variables or a .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings.

    All values can be overridden via environment variables.
    Example: DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/wind_db
    """

    # --- Application ---
    APP_NAME: str = "ASCE 7 Wind Load API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/wind_loads"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO_SQL: bool = False

    # --- CORS (for React frontend) ---
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]

    # --- Engineering defaults ---
    # Minimum design pressure per ASCE 7-10+ §27.1.5 (psf)
    MIN_DESIGN_PRESSURE_PSF: float = 16.0
    # Default directionality factor for buildings
    DEFAULT_KD: float = 0.85

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    """Singleton access to application settings."""
    return Settings()
