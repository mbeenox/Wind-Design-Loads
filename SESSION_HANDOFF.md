# ASCE 7 Wind Load Suite — Session Handoff Document
## For continuing development in a new Claude chat session

**Date:** March 18, 2026
**Engineer:** Mobolaji (PE/SE, structural engineering consulting practice)
**Project:** Web application to replace the Struware Code Search 2024 Excel spreadsheet

---

## WHAT THIS PROJECT IS

A full-stack ASCE 7 wind load calculation suite that replaces a legacy Excel spreadsheet (`Code_Search_2024__Wind_App_Replica_.xlsx`). The spreadsheet supports ASCE 7-98 through 7-22 and computes velocity pressures, MWFRS (directional and low-rise), C&C pressures, parapets, overhangs, and torsional load cases.

The app has three layers:
1. **React frontend** (`WindCalculator.jsx`) — standalone calculator with mock API functions that replicate the spreadsheet's math in JavaScript
2. **Python backend** (FastAPI + calculation engine) — production API that will replace the mock functions
3. **PDF generator** — produces the "Show Your Work" PE calculation package for stamping and submission

---

## FILE INVENTORY

### Uploaded files (must be re-uploaded to new session):

| File | Description | Size |
|---|---|---|
| `wind_api_project.tar.gz` | Complete project archive (extract with `tar xzf`) | 102 KB |
| `Code_Search_2024__Wind_App_Replica_.xlsx` | Reference spreadsheet (ground truth for all values) | 3.4 MB |
| `FIX_INSTRUCTIONS.md` | Detailed discrepancy checklist between app and spreadsheet | 14 KB |
| `WindCalculator_fixed.jsx` | The current React frontend (also inside the archive) | 72 KB |

### Archive contents (`wind_api_project.tar.gz`):

```
wind_api/
├── app/
│   ├── main.py                    # FastAPI factory, lifespan, CORS, health check
│   ├── config.py                  # pydantic-settings (DATABASE_URL, CORS_ORIGINS)
│   ├── models/
│   │   ├── common.py              # Enums: CodeVersionEnum, ExposureEnum, etc.
│   │   ├── requests.py            # WindProjectSetup, BuildingGeometry, etc.
│   │   └── responses.py           # VelocityPressureResponse, CCPressureResponse, etc.
│   ├── routers/
│   │   ├── velocity.py            # POST /api/v1/calculate/wind/qz
│   │   ├── cc.py                  # POST /api/v1/calculate/wind/cc
│   │   └── mwfrs.py               # POST /api/v1/calculate/wind/mwfrs/directional & lowrise
│   ├── services/
│   │   └── engine.py              # Python calc engine (~1,500 lines) — the core math
│   └── db/
│       ├── session.py             # AsyncSession, get_db dependency
│       └── repository.py          # ORM models + fetch_terrain_constants_db()
├── frontend/
│   └── WindCalculator.jsx         # React frontend (1,359 lines) — CURRENT PRODUCTION VERSION
├── reports/
│   └── calc_package.py            # PE Calculation Package PDF generator (ReportLab)
└── tests/
    └── test_integration.py        # 8 passing test suites

# Also at root level in the archive:
asce7_wind_engine.py               # Standalone engine copy (same as app/services/engine.py)
wind_load_database_schema.sql      # PostgreSQL schema (18+ tables)
Wind_Load_Suite_Architectural_Blueprint.md  # Full reverse-engineering of the spreadsheet
PE_Wind_Calculation_Package.pdf    # Sample generated PDF output
```

---

## CURRENT STATE — WHAT WORKS

### React Frontend (WindCalculator.jsx) — FULLY WORKING
The React file is the most up-to-date component. It matches the spreadsheet's values. Key features:

- **qz Profile tab:** Velocity pressure at each height with Kz, Kzt, qz
- **MWFRS Directional tab:**
  - Sub-tabs: Normal to Ridge / Parallel to Ridge / Torsion (4 cases)
  - Direction-specific leeward Cp (B/L for Normal, L/B for Parallel)
  - Direction-specific roof Cp zones (h/B for Normal, h/L for Parallel)
  - Combined WW+LW wall profile with interactive "Add Height" feature
  - Parapet pressures using qp at z = h + parapet_height (§27.3.4)
  - Torsional load cases (ASCE 7 Figure 27.3-8, Cases 1–4)
- **MWFRS Low-Rise tab:**
  - Case A / Case B GCpf zone tables
  - Horizontal Simple Diaphragm pressures (§28.4) with correct Zone 5/5E − Zone 6/6E
  - Greyed out with reason when h > 60 ft or h > B
- **C&C tab:**
  - Sub-tabs: Roof (1, 1′, 2, 3) / Overhangs (GCpi=0) / Walls (4, 5) / Parapet (§30.9)
  - Correct GCp tables from ASCE 7-22 Fig 30.3-2A (monoslope/gable/hip by angle range)
  - Zone 1′ (roof interior) included
  - Overhangs with separate GCp and GCpi = 0
  - Solid parapet pressures (Case A, Case B interior, Case B corner)
  - Wall zones with correct breakpoints (−1.17/−1.01/−0.96/−0.90 for Zone 4)
- **Kzt calculator** (§26.8) with 2D Ridge, 2D Escarpment, 3D Hill support
- **Gust effect factor G** (§26.11) — rigid fixed, rigid calculated, flexible/resonant

### Python Backend — WORKING BUT NEEDS UPDATES
The FastAPI + engine layer works correctly for the core math (qz, Kz, Ke, Kzt, MWFRS wall Cp, low-rise GCpf). All 8 integration test suites pass. However, it has NOT yet been updated to match the fixes applied to the React frontend:

**Python backend items that need updating:**
1. **C&C GCp tables** — The engine's `_CC_ROOF_GCP_DB` mock data has the old incorrect values. Needs the corrected breakpoints from `gcpRoof_hle60()` in the JSX
2. **Zone 1′** — Not in the engine or response models yet
3. **Overhangs** — Engine doesn't compute separate overhang pressures with GCpi=0
4. **C&C solid parapet** — Not in the engine
5. **Simple Diaphragm** — Not computed by `calculate_mwfrs_lowrise()` yet
6. **MWFRS parapet qp** — Engine uses qh at h, should use qp at h + parapet_height
7. **Combined WW+LW** — Not in the MWFRS response model
8. **Gust factor modes** — Engine only has rigid G=0.85; needs rigid-calc and flexible

**Python backend items that ARE correct (verified against spreadsheet):**
- qz formula: `0.00256 × Ke × Kz × Kzt × Kd × V²` ✓
- Kz computation with terrain constants ✓
- Ke ground elevation factor ✓
- Kzt topographic factor ✓
- Cp_LW interpolation (B/L ratio breakpoints) ✓
- Roof Cp zones (h/B and h/L with 0.5/1.0 interpolation) ✓
- Direction mapping (Normal = B/L, Parallel = L/B) ✓
- Net pressure sign convention: p = qGCp − qi(±GCpi) ✓
- Low-rise GCpf table values ✓
- GCpf sign convention: pN = qh×(GCpf+GCpi), pP = qh×(GCpf−GCpi) ✓

### PDF Generator — WORKING BUT NEEDS UPDATES
Generates a multi-page PE calculation package with equation traces. Currently covers:
- Engineering header (project, job #, date, engineer initials)
- Global criteria table
- Velocity pressure equation trace (Ke → Kz → qz)
- Velocity pressure profile table
- MWFRS wall Cp interpolation traces
- MWFRS surface pressure table
- Windward wall profile
- Low-rise Case A / Case B tables
- C&C zone pressure matrices

**PDF items needing update:** Same list as Python backend above — the PDF generator imports from the engine, so fixing the engine automatically fixes the PDF.

---

## REFERENCE TEST CASE

Use these inputs and expected outputs to verify any changes:

**Inputs:** L=100, B=60, h=15, Monoslope θ=1.2°, V=120 mph, Exp C, Kd=0.85, G=0.85, GCpi=±0.18, Risk Cat III, Enclosed, Parapet=3 ft, Kzt=1.0

| Output | Expected |
|---|---|
| qh | 26.60 psf |
| WW qGCp at h | 18.09 psf |
| LW qGCp (Normal, Cp=−0.5) | −11.30 psf |
| Combined WW+LW (Normal, at h) | 29.39 psf |
| C&C Zone 1 neg @ 10 sf | −50.0 psf |
| C&C Zone 2 neg @ 10 sf | −66.0 psf |
| C&C OH Zone 1 neg @ 100 sf | −42.6 psf |
| C&C Wall Zone 4 neg @ 10 sf | −35.9 psf |
| SD Interior Wall (transverse) | 18.4 psf |
| SD End Zone Wall (transverse) | 27.7 psf |
| SD Interior Roof (transverse) | −8.5 psf |
| SD End Zone Roof (transverse) | −14.4 psf |
| MWFRS WW Parapet (z=18 ft) | 41.5 psf |
| MWFRS LW Parapet (z=18 ft) | −27.6 psf |
| MWFRS Overhang soffit | 18.09 psf |

---

## KEY ENGINEERING DECISIONS ALREADY MADE

1. **Direction mapping:** Wind "Normal to Ridge" uses B/L for leeward Cp and h/B for roof zones. Wind "Parallel to Ridge" uses L/B and h/L. Ridge is assumed parallel to L.

2. **Net pressure sign convention:** `p = qGCp − qi(±GCpi)`. The "+GCpi" column subtracts qh×gcpi from external. The "−GCpi" column adds qh×gcpi to external. This matches the spreadsheet's column headers (G47="w/+qiGCpi" uses subtraction formula, H47="w/-qhGCpi" uses addition).

3. **C&C GCp values:** The ASCE 7-22 Fig 30.3-2A values are the final design values. No additional 10% reduction factor is applied in the JSX — the breakpoints already reflect the figure's final values. The spreadsheet's "10% reduction" note on C49 refers to the raw-to-final conversion that's already baked into the GCp tables.

4. **Zone 3 = Zone 2 when parapet ≥ 3 ft:** This applies to the h≤60 procedure per the spreadsheet. The JSX's `gcpRoof_hle60()` for monoslope θ≤3° has Zone 3 with its own breakpoints (−3.20@10sf), but the spreadsheet overrides this when parapet≥3ft. The conditional is noted in the UI.

5. **Overhangs use GCpi = 0** per ASCE 7 §30.6. They have their own GCp breakpoint tables separate from the standard roof zones.

6. **C&C solid parapet (§30.9)** uses three GCpn curves: Case A (Zone 2&3 combined positive), Case B interior (negative), Case B corner (negative), across areas [10, 20, 50, 100, 200, 500] sf.

7. **PDF generation uses ReportLab on Python backend** (not jsPDF on frontend). This ensures the same engine that computes the numbers also generates the stamped document.

8. **Terrain constants for Exposure C:** α=9.5, zg=900 across ALL code versions including 7-22. The 7-22 change to Exposure B (α=7.5, zg=2460) does NOT apply to Exposure C.

---

## WHAT TO DO IN THE NEW SESSION

### Immediate next steps (priority order):

1. **Upload all 4 files** to the new session (the tar.gz, spreadsheet, FIX_INSTRUCTIONS.md, and this handoff doc)

2. **Update the Python engine** (`asce7_wind_engine.py`) to match the React frontend's corrected calculations — specifically the C&C GCp tables, Zone 1′, overhangs, solid parapet, Simple Diaphragm, and parapet qp at z=h+parapet

3. **Update the FastAPI response models** (`responses.py`) to include the new fields (Zone 1′, overhang pressures, SD horizontal pressures, combined WW+LW, C&C parapet cases)

4. **Update the PDF generator** (`calc_package.py`) to render the new sections

5. **Wire the React frontend to the real API** — replace the mock `apiQz/apiDir/apiLR/apiCC` functions with Axios POST calls to the FastAPI endpoints

### Future work:
- Alembic migration files for the PostgreSQL schema
- Docker Compose (PostgreSQL + API)
- Vercel deployment for the React frontend
- h>60 ft C&C procedure (Fig 30.4-1)
- Open building Cn coefficients
- Flexible/resonant G factor in the Python engine

---

## MOBOLAJI'S PREFERENCES

- **Deployment:** Manual GitHub file editing (pencil icon) → auto Vercel redeploy. No terminal/CLI.
- **Stack:** React + Vite, Tailwind CSS, dark UI theme
- **File format:** Custom save/open with `.wind` or similar extension
- **UI style:** Industrial/utilitarian, JetBrains Mono, tabular-nums, amber for positive/sky-blue for suction
- **Engineering precision:** Values verified to spreadsheet cell-level accuracy. PE-stampable output.
