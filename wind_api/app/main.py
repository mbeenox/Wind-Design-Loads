"""
ASCE 7 Wind Load API — Application Entry Point

Production-ready FastAPI application with:
  - Async lifespan management (DB connect/disconnect)
  - CORS middleware for React frontend
  - Versioned route mounting
  - Health check endpoint
  - Custom exception handlers

Run with:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.session import init_engine, dispose_engine
from app.routers import velocity, cc, mwfrs


# ============================================================================
# Lifespan — startup / shutdown hooks
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manages the database connection pool lifecycle.

    On startup: creates the async engine and connection pool.
    On shutdown: drains connections and disposes the engine.

    In development mode (no real DB), the engine init is wrapped in
    a try/except so the app still boots with mock data.
    """
    settings = get_settings()
    try:
        init_engine()
        print(f"[startup] Database engine initialized: {settings.DATABASE_URL[:40]}...")
    except Exception as e:
        print(
            f"[startup] Database not available ({e}). "
            f"Running with in-memory mock data."
        )

    yield  # Application runs here

    await dispose_engine()
    print("[shutdown] Database engine disposed.")


# ============================================================================
# Application Factory
# ============================================================================

def create_app() -> FastAPI:
    """
    Build and configure the FastAPI application.

    Separated into a factory function so tests can create fresh instances
    without importing module-level singletons.
    """
    settings = get_settings()

    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Production calculation engine for ASCE 7 wind load analysis.\n\n"
            "Supports ASCE 7-98 through ASCE 7-22 with full code-version "
            "routing, terrain exposure constants, topographic effects, "
            "gust effect factors, and all MWFRS / C&C procedures.\n\n"
            "**Endpoints:**\n"
            "- `POST /api/v1/calculate/wind/qz` — Velocity pressure at height z\n"
            "- `POST /api/v1/calculate/wind/cc` — Components & Cladding pressures\n"
            "- `POST /api/v1/calculate/wind/mwfrs/directional` — MWFRS Directional\n"
            "- `POST /api/v1/calculate/wind/mwfrs/lowrise` — MWFRS Low-Rise Envelope\n"
        ),
        lifespan=lifespan,
        docs_url="/docs",       # Swagger UI
        redoc_url="/redoc",     # ReDoc
    )

    # --- CORS middleware for React frontend ---
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Mount routers under /api/v1 prefix ---
    application.include_router(velocity.router, prefix="/api/v1")
    application.include_router(cc.router,       prefix="/api/v1")
    application.include_router(mwfrs.router,    prefix="/api/v1")

    # --- Request timing middleware ---
    @application.middleware("http")
    async def add_timing_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
        return response

    # --- Global exception handler for engine ValueErrors ---
    @application.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": str(exc),
                "error_type": "engineering_validation",
            },
        )

    # --- Health check ---
    @application.get(
        "/health",
        tags=["Infrastructure"],
        summary="Health check",
    )
    async def health_check():
        return {
            "status": "healthy",
            "version": settings.APP_VERSION,
            "engine": "ASCE 7 Wind Load Suite",
            "supported_editions": ["7-98", "7-02", "7-05", "7-10", "7-16", "7-22"],
        }

    return application


# --- Module-level app instance (used by uvicorn) ---
app = create_app()
