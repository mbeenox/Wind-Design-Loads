# ASCE 7 Wind Load Calculator — Session Continuity Instructions

## Project Overview

A React JSX wind load calculator (single-file, ~1,350 lines) that replicates an Excel spreadsheet's ASCE 7-22 wind pressure calculations. The output file is `WindCalculator_fixed.jsx`.

**Reference spreadsheet:** `Code_Search_2024__Wind_App_Replica_.xlsx`  
**Test inputs (default):** L=100 ft, B=60 ft, h=15 ft, Monoslope θ=1.2°, V=120 mph, Exp C, Kd=0.85, Ke=1.0, G=0.85 (fixed), GCpi=±0.18, Risk Cat III, Enclosed, Parapet=3 ft, Kzt=1.0 (flat)

---

## File Architecture

### Constants & Helpers (lines 1–115)
- `CC_AREAS_ROOF = [10, 100, 500, 1000]`, `CC_AREAS_WALL = [10, 100, 200, 500]`
- `ZMETA` — zone label/description map
- `CODE_VERS`, `TOPO_TYPES`, `GUST_MODES`, `EXPOSURES`, `ENCLOSURES`, `ROOFS`, `TABS`
- `r2/r4/r6` — rounding helpers; `gcpiOf`, `keOf`, `tcOf`, `defZ`, `compQz`, `cpLW`, `logInterp`, `minPsf`

### Calculation Functions (lines 118–360)
- `calcKzt(topoType, H, Lh, x, z, upwind)` — ASCE 7 §26.8 topographic factor
- `calcG(mode, exposure, h_ft, n1, beta, V_mph)` — ASCE 7 §26.11 gust factor (3 modes)
- `gcpRoof_hle60(roofType, theta, zone, sign)` — C&C GCp tables Fig 30.3-2A
- `gcpWall_hle60(zone, sign)` — C&C wall GCp tables Fig 30.4-1
- `interpGCp(area, table)` — log-linear interpolation

### API Functions (lines 362–560)
- `apiQz(P)` — velocity pressure profile (qz at each height)
- `apiDir(P)` — MWFRS Directional Ch. 27, also computes wall profile with combined WW+LW
- `apiLR(P)` — MWFRS Low-Rise Ch. 28 with Simple Diaphragm pressures
- `apiCC(P)` — C&C pressures for roof zones, walls, and solid parapet

### UI Components (lines 562–979)
- `Psf`, `Field`, `NInput`, `Sel`, `Divider`, `Chip`, `Acc`, `STabs`, `TRow`, `THead`
- `CCMatrix` — C&C pressure grid (zones × areas)
- `WallProfile({ d, isNormal })` — self-contained wall profile table with "+ Add Height (Z)" button
- `DirTab({ d, sub, setSub })` — MWFRS Directional tab renderer

### Main Component (lines 981–1353)
- `WindCalculator()` — full app with sidebar + tabbed content

---

## Tabs Structure

```
[ qz Profile ] [ MWFRS Dir. ] [ MWFRS LR ] [ C&C ]
```

### qz Profile
Shows velocity pressure table: z (ft) | Kz | Kzt | qz (psf) | α | zg (ft)

### MWFRS Dir. (sub-tabs: Normal to Ridge | Parallel | Torsion)
- Surface pressures table: WW (Cp=0.8), LW (Cp varies), Side (Cp=-0.7)
- Roof zones table
- **Wall Profile** accordion: Combined WW+LW at each height
  - Columns: z (ft) | Kz | Kzt | WW+LW w/+GCpi | WW+LW w/−GCpi
  - "+ Add Height (Z)" button adds rows pinned at bottom while typing, sorts on Enter/blur
  - Formula: `combined = p_WW(z) − p_LW = |WW| + |LW|` (net horizontal force)
  - At z=h: GCpi cancels, both columns are equal
- Torsion: 4 mandatory cases per Fig 27.3-8

### MWFRS LR (Ch. 28, only when h≤60 and h≤B)
- Case A (Transverse) GCpf table
- Case B (Longitudinal) GCpf table
- **Horizontal MWFRS Simple Diaphragm Pressures** accordion:
  - Transverse: Interior Wall | Roof, End Zone: Wall | Roof
  - Longitudinal: Interior Wall, End Zone: Wall
  - Notes: ** NOTE total horiz force, plus 16 psf×wall + 8 psf×roof projection

### C&C (sub-tabs: Roof | Overhangs | Walls | Parapet)
- Roof: Zones 1, 1', 2, 3 — CCMatrix (zones × areas)
- Overhangs: Zones oh1, oh2, oh3 — GCpi=0
- Walls: Zones 4, 5 — CCMatrix
- Parapet: Solid parapet per §30.9 — 3 rows × 6 areas [10,20,50,100,200,500 sf]

---

## Sidebar Inputs

```
PROJECT: Edition | Risk Cat | Exposure | V (mph) | Enclosure
GEOMETRY: L | B | h | Roof type | θ (deg) | Parapet ht
TOPOGRAPHIC FACTOR Kzt: Topography dropdown + H, Lh, x, upwind/downwind + live preview
GUST EFFECT FACTOR G: Mode dropdown + n1, β (for flexible) + live preview
```

---

## Key Formulas (Verified Against Spreadsheet)

### Velocity Pressure
```
qz = 0.00256 × Ke × Kz × Kzt × Kd × V²
Kz (Exp C) = 2.01 × (max(z, 15) / 900)^(2/9.5)
```
**Test:** V=120, h=15, Exp C, Kd=0.85, Ke=1.0, Kzt=1.0 → Kz=0.8489, qh=26.60 psf ✓

### MWFRS Directional (§27)
```
p_WW(z) = qz × G × 0.8 ± qh × GCpi
p_LW    = qh × G × Cp_LW ± qh × GCpi   (constant, uses qh not qz)
Cp_LW: B/L≤1→−0.5, B/L<2→−0.5+(B/L−1)×0.2, B/L<4→−0.3+(B/L−2)×0.05, else −0.2
Combined = p_WW(z) − p_LW = |WW| + |LW|
```
**Overhang:** `oh = qh × 0.70` (soffit uplift, §27.3.2)  
**MWFRS Parapet:** `WW = qh × 1.5 = 40.0 psf`, `LW = qh × (−1.0) = −26.7 psf`

### MWFRS LR Simple Diaphragm (§28.4) — zone keys from Fig 28.3-1
```
Interior Wall = qh × (B["5"] − B["6"])   = qh × 0.69  = 18.4 psf ✓
End Zone Wall = qh × (B["5E"] − B["6E"]) = qh × 1.04  = 27.7 psf ✓
Interior Roof = qh × (A["2"] − A["3"])   = qh × −0.32 = −8.5 psf ✓
End Zone Roof = qh × (A["2E"] − A["3E"]) = qh × −0.54 = −14.4 psf ✓
```
**CRITICAL:** Use B["6"]/B["6E"] (leeward wall zones, GCpf=−0.29/−0.43), NOT B["4"]/B["4E"] (side walls, GCpf=−0.45/−0.48). Using wrong zones gives 22.6/29.0 psf instead of 18.4/27.7 psf.

### C&C Roof GCp Breakpoints (Monoslope θ≤3°, ASCE 7-22 Fig 30.3-2A)
```
Zone 1 neg:  [[10, −1.70], [500, −1.00], [1000, −1.00]]
Zone 1' neg: [[10, −0.90], [100, −0.90], [1000, −0.40]]  ← flat 10→100 sf
Zone 2 neg:  [[10, −2.30], [500, −1.40], [1000, −1.40]]
Zone 3 neg:  [[10, −2.30], [500, −1.40], [1000, −1.40]]  ← = Zone 2 when parapet ≥3 ft
Pos 1&1':    [[10, +0.30], [100, +0.20], [500, +0.20], [1000, +0.20]]
Pos 2&3:     [[10, +0.90], [500, +0.63], [1000, +0.63]]
OH 1&1' neg: [[10, −1.70], [100, −1.60], [500, −1.00], [1000, −1.00]]  ← 3 segments
OH 2&3 neg:  [[10, −2.30], [500, −1.10], [1000, −1.10]]
```
**No extra 0.9 reduction factor** — ASCE 7-22 Fig 30.3-2A values are already the final design values.  
**Combined pressure:** `p = qh × (GCp ± GCpi)` where GCpi=0 for overhangs.

### C&C Wall GCp Breakpoints (Fig 30.4-1)
```
Zone 4 neg: [[10, −1.17], [100, −1.01], [200, −0.96], [500, −0.90]]
Zone 5 neg: [[10, −1.44], [100, −1.12], [200, −1.03], [500, −0.90]]
Pos 4&5:    [[10, +1.08], [100, +0.921], [200, +0.873], [500, +0.81]]
```

### C&C Solid Parapet (§30.9 / Fig 30.9-1)
```
qp = qh (velocity pressure at mean roof height per §30.9 for h≤60)
GCpn breakpoints (back-calculated from spreadsheet, qp=26.7 psf):
  Case A (Zone 2&3 pos): [[10, +3.1948], [500, +2.0262]]
  Case B Interior neg:   [[10, −1.8876], [500, −1.3483]]
  Case B Corner neg:     [[10, −2.1573], [500, −1.3483]]
```
**Verified @50sf:** Case A=72.5, B-Int=−44.5, B-Cor=−48.7 ✓

### Topographic Factor Kzt (§26.8)
```
K1 = γ × (H/Lh)  where γ: 2D ridge=1.30, escarpment=0.75, 3D hill=0.95
K2 = max(0, 1 − |x/LhMod| / μ)  where μ: upwind=1.5 (ridge/hill), 2.5 (escarp upwind)
K3 = exp(−ν × z/LhMod)  where ν: 2D ridge=3, escarp=2.5, 3D hill=4
Kzt = (1 + K1 × K2 × K3)²
```
LhMod = 2H if H/Lh > 0.5 (capped), else Lh. H/Lh capped at 0.5.

### Gust Effect Factor G (§26.11)
```
Rigid fixed: G = 0.85
Rigid calc:  G = 0.925 × (1 + 1.7×Iz×gQ×Q) / (1 + 1.7×gv×Iz)   where gQ=gv=3.4
Flexible Gf: adds resonant response R, peak factor gR = √(2ln(600n1)) + 0.5772/√(2ln(600n1))
```
Exposure C: cg=0.65, eps_bar=1/5, Lz_c=500, b_bar=1.0, alpha_bar=1/9.5, zmin=15

---

## State Structure (WindCalculator component)

```javascript
proj  = { code_version, risk_category, V_mph, exposure, enclosure }
geo   = { L_ft, B_ft, h_ft, roof_type, roof_angle_deg, parapet_height_ft, extraHeights[] }
kd    = 0.85  (fixed, not editable)
kztIn = { topo_type, H_ft, Lh_ft, x_ft, upwind }
gustIn = { mode, n1, beta }
// Results:
qzR, dirR, lrR, ccR
// UI:
tab, dirSub, ccSub, calc, errs, apiE
```

### apiDir return object
```javascript
{ qh, G, gcpi, kd, V, L, B, h,
  cWW, cSW, cLW_n, cLW_p,
  lwPrs: { normal: {pN, pP}, parallel: {pN, pP} },  // LW pressures for wall profile
  profile[]: { z_ft, kz, kzt, pN, pP,
               combN_normal, combP_normal,
               combN_parallel, combP_parallel },
  roofNormal[], roofParallel[],
  torsion[], gRes, kztH, lwPrs,
  parWW, parLW, oh }
```

### apiLR return object
```javascript
{ ok, reason, qh, gcpi, ez,
  cA[], cB[],  // GCpf zone tables
  pww, plw,    // MWFRS parapet
  sd: { a, endZone2a,
        transverse: { intWall, endWall, intRoof, endRoof },
        longitudinal: { intWall, endWall } } }
```

### apiCC return object
```javascript
{ qh, gcpi, a, theta, roof, proc,
  prs[]: { zone, area, gn, gp, isOverhang, pnN, ppP },
  parPrs[]: { area, caseA, caseBint, caseBcor },
  parAreas: [10,20,50,100,200,500] }
```

---

## WallProfile Component Details

```javascript
function WallProfile({ d, isNormal })
```
- Owns its own `rows` state: `[{ id, val, locked }]`
- **`+ Add Height (Z)`** → appends `{ id: Date.now(), val: "", locked: false }`
- **While typing** (`locked=false`): row stays pinned at bottom (`unlockedRows`), shows live preview
- **On Enter or blur** (`locked=true`): row moves to `lockedExtras`, sorted into table by z
- **Editing locked row**: `updateRow` resets `locked=false`, pulling it back to bottom
- `calcExtra(z_ft)` computes Kz, combined pressures locally using `d.kd`, `d.V`, `d.G`, `d.qh`, `d.gcpi`

---

## Known Bugs Fixed (DO NOT REINTRODUCE)

1. **C&C GCp 10% reduction** — was applying `×0.9` to all θ≤10° negatives. WRONG. Remove it.
2. **SD zone keys** — was `B["4"]/B["4E"]` (side walls, −0.45/−0.48). CORRECT is `B["6"]/B["6E"]` (leeward walls, −0.29/−0.43).
3. **apiLR qh rounding** — was using `r2(qh)` before multiplying, giving 27.8 instead of 27.7. Use full-precision `qh`.
4. **Overhang formula** — was `qh×G×0.8=18.1`. CORRECT is `qh×0.70=18.7`.
5. **WallProfile state** — had a "committed" two-phase pattern where `val===committed` on creation caused the row to never render. Replaced with `locked` boolean.
6. **WallProfile sorting during typing** — row jumped into sorted position mid-entry, stealing focus. Fixed by keeping `locked=false` rows pinned at bottom until Enter/blur.
7. **MWFRS parapet GCpn** — must use `qh×GCpn` (no G factor). GCpn=+1.5 WW, −1.0 LW.

---

## Spreadsheet Cross-Check Values (V=120 mph, test defaults)

| Calculation | App | Spreadsheet |
|---|---|---|
| Kh (h=15, Exp C) | 0.8489 | 0.851 (tabulated) |
| qh (Kd×qh) | 26.60 psf | 26.7 psf |
| WW qGCp | 18.09 psf | 18.0 psf |
| LW qGCp (normal) | −11.30 psf | −11.3 psf |
| Combined WW+LW | 29.4 psf | 29.4 psf |
| SD Interior Wall | 18.4 psf | 18.4 psf ✓ |
| SD End Zone Wall | 27.7 psf | 27.7 psf ✓ |
| SD Interior Roof | −8.5 psf | −8.5 psf ✓ |
| SD End Zone Roof | −14.4 psf | −14.4 psf ✓ |
| C&C Zone1 @10sf | −50.2 psf | −50.1 psf ✓ |
| C&C Zone2 @10sf | −66.2 psf | −66.1 psf ✓ |
| C&C OH1 @100sf | −42.7 psf | −42.7 psf ✓ |
| Parapet Case A @50sf | 72.5 psf | 72.5 psf ✓ |
| Parapet B-Int @50sf | −44.5 psf | −44.5 psf ✓ |
| Parapet B-Cor @50sf | −48.7 psf | −48.7 psf ✓ |
| MWFRS WW parapet | 40.0 psf | 40.0 psf ✓ |
| MWFRS LW parapet | −26.7 psf | −26.7 psf ✓ |
| Overhang soffit | 18.7 psf | 18.7 psf ✓ |

**Note:** Small discrepancies in qh (26.60 vs 26.7) are because the app uses the exact formula Kz=2.01×(z/zg)^(2/α) while the spreadsheet uses the tabulated value Kz=0.851. Both are valid. All spreadsheet comparisons must be done at the same V — the spreadsheet defaults to V=130 mph in the saved file, not V=120.

---

## How to Continue

1. Open a new Claude session and attach `WindCalculator_fixed.jsx`
2. Paste this document as context
3. The file is a self-contained React JSX component — no external dependencies beyond React/Tailwind
4. All calculations are in `apiQz`, `apiDir`, `apiLR`, `apiCC` — pure functions, no side effects
5. UI is entirely in `WindCalculator` (main) + helper components above it
6. To add a new output section: add to the relevant `api*` return object, then render it in the corresponding tab in `WindCalculator`

---

## Possible Next Steps / Known Gaps

- [ ] **Hip roof GCp tables** — currently uses gable fallback for hip at θ>27°; needs dedicated Fig 30.3-2B values
- [ ] **h>60 ft C&C procedure** — currently shows "h>60'" proc but doesn't compute different zone tables
- [ ] **Multiple risk categories** — importance factor is set to 1.0 always; could apply Ie to qz
- [ ] **Exposure B ASCE 7-22** — uses α=7.5, zg=2460, zm=30 (different from 7-16 B constants)
- [ ] **Print / export** — no PDF or export functionality yet
- [ ] **Ke at elevation** — currently always Ke=1.0; sidebar has no elevation input for §26.9
- [ ] **Wind directionality Kd** — fixed at 0.85 in sidebar; could allow editing per §26.6
