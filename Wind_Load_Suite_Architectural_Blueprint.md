# ASCE 7 Wind Load Suite — Architectural Blueprint

**Source Workbook:** Code Search 2024 (Struware)
**ASCE 7 Editions Supported:** ASCE 7-98 through ASCE 7-22 (via `index_windCode` 1–6)
**Purpose:** Software development specification for an independent wind load calculation engine.

---

## 1. Global Data Dependencies

All wind sheets ultimately pull their core inputs from two global setup sheets (`Code` and `Tables`), plus project header information from `Title`.

### 1.1 From the `Code` Sheet

| Variable | Cell | Description |
|---|---|---|
| **Building Code** | `Code!E12` | Selected code name (e.g., "International Building Code 2021") |
| **Risk Category** | `Code!F20` | I, II, III, or IV |
| **Wind Importance Factor (I)** | `Code!F22` | 1.0 for ASCE 7-10+; variable for ASCE 7-05 and earlier. Named range `I`. |
| **Roof Angle (θ)** | `Code!G34` | Computed from pitch input at `Code!F34`. Named range `angle`. |
| **Building Length (L)** | `Code!F35` | Plan dimension (ft) |
| **Least Width (B)** | `Code!F36` | Plan dimension (ft) |
| **Mean Roof Height (h)** | `Code!F37` | Named range `h_wind` or `Roof_h` |
| **Parapet Height above Grade** | `Code!F38` | Used for parapet pressure calculations |
| **Minimum Parapet Height** | `Code!F39` | Minimum parapet projection |
| **hb (Elevated Building)** | `Code!F40` | Height to bottom of MWFRS. Named range `hb_wind`. If 0, building is not elevated. |
| **Occupancy Group** | `Code!F16` | Occupancy group letter (e.g., "B") |

### 1.2 From the `Tables` Sheet

| Variable | Named Range / Cell | Description |
|---|---|---|
| **Code Index** | `Index_codes` = `Tables!C3` | Master row index into `codes` table for selected building code |
| **Wind Code Index** | `index_windCode` = `Tables!D3` | Maps the selected building code to an ASCE 7 wind edition (1=ASCE 7-98, 2=02, 3=05, 4=10, 5=16, 6=22) |
| **Wind Basis Code** | `wind` = `Tables!AD7:AF23` | Lookup table mapping `index_windCode` → ASCE 7 edition string and internal sub-index |
| **MWFRS Procedure Table** | `MWFRS` = `Tables!AT38:AU47` | Maps Risk Category to MWFRS parameters |
| **Codes Master Table** | `codes` = `Tables!B5:U30` | All supported building codes with their wind/snow/seismic basis codes |

### 1.3 From the `Title` Sheet

Header information only: Company, Address, Job Title, Job Number, Sheet Number, Calculated By, Checked By, and Dates. All wind sheets pull these via `Title!D2:D6` or `Title!O2:O6` (alternate location for logo use).

---

## 2. Variable Dictionary (By Sheet)

### 2.1 `Wind` Sheet — Central Wind Parameters Engine

This is the master configuration sheet. All other wind sheets read their base parameters from here.

**User Inputs:**

| Parameter | Cell | Notes |
|---|---|---|
| Ultimate Wind Speed (V) | `E12` | mph (ASCE 7-10+) or Basic Wind Speed (ASCE 7-05 and earlier) |
| Exposure Category | `E15` | A, B, C, or D. Selected via `index_exp`. |
| Enclosure Classification | `E16` | Enclosed, Partially Enclosed, Open, or Partially Open. Selected via `index_intpressure`. |
| Type of Roof | `E22` | Monoslope, Hip, Gable, Multispan Gable, Sawtooth, Stepped. Via `index_roof`. |
| Topography Type | `E26` | Flat, 2D Ridge, 2D Escarpment, 3D Hill. Via `index_topo`. |
| Hill Height (H) | `E27` | ft |
| Half Hill Length (Lh) | `E28` | ft |
| Distance from Crest (x) | `E32` | ft |
| Upwind/Downwind | `E33` | Building position relative to crest |
| Ground Elevation above Sea Level | `F72` | ft (for Ke calculation, ASCE 7-16+) or Air Density override (ASCE 7-10 and earlier) |
| Gust Effect Method | `index_gust` | 1=Rigid default (G=0.85), 2=Rigid calculated, 3=Flexible |
| Natural Frequency (η₁) | `I53` | Hz (for flexible structure) |
| Damping Ratio (β) | `I54` | Typically 0.01–0.02 |
| Enclosure Test Inputs (Ao, Ag, Aoi, Agi) | `F87:F90` | For Partially Enclosed determination |
| Large Volume Reduction (Aog, Vi) | `I113:I114` | For Ri factor |

**Computed Outputs (consumed by all downstream sheets):**

| Output | Cell / Named Range | ASCE 7 Reference |
|---|---|---|
| Directionality Factor (Kd) | `E18` / `Kd` table | §26.6 / Table 26.6-1 |
| Kh (MWFRS ≤ 60) | `E19` / `AI32` | §26.10.1 / Table 26.10-1 |
| Kh (all other) | `E20` / `AI33` | §26.10.1 / Table 26.10-1 |
| Internal Pressure Coeff (GCpi) | `E17` | §26.13 / Table 26.13-1 |
| Topographic Factor (Kzt) | `F39` | §26.8 / Eq. 26.8-1 |
| Gust Effect Factor (G or Gf) | `G50` | §26.11 |
| Ground Elevation Factor (Ke) | `K72` | §26.9 (ASCE 7-16+) |
| Adjusted Constant (0.00256·Ke) | `F74` | The velocity pressure constant with elevation adjustment |
| Nominal Wind Speed | `E13` | = V·√0.6 (ASCE 7-10+) |
| Ri (Large Volume Reduction) | `H115` | §26.13-1 Note 2 |

### 2.2 `MWFRS all h` — Directional Procedure (All Heights)

ASCE 7 Chapter 27, Part 1 (Directional Procedure).

**Additional Inputs (from user on this sheet):**

| Parameter | Cell | Notes |
|---|---|---|
| Ridge Height (optional override) | `Q12` | Defaults to computed value |
| Height for qi (Partially Enclosed) | `Q13` | Height at which qi is evaluated |
| User z-values for windward wall | `Q48:Q73` | Optional elevation points for Kz profile |
| User Kzt overrides at z | `R48:R73` | Optional Kzt overrides per elevation |

**Computed Outputs:**

| Output | Cell(s) | Description |
|---|---|---|
| Base Pressure qh (or Kd·qh) | `E12` | = 0.00256·Ke·Kh·Kzt·Kd·V²·I |
| L/B and B/L ratios | `G19`, `L19` | For both wind directions |
| h/L and h/B ratios | `I19`, `N19` | For roof Cp lookup |
| Wall Cp (Windward) | `F21` | Always +0.8 |
| Wall Cp (Leeward) | `F22` | Interpolated from L/B ratio |
| Wall Cp (Side) | `F23` | Always –0.7 |
| Roof Cp values | `F25:F30` | Negative and positive, by zone distance from windward edge |
| Surface Pressures (qh·G·Cp ± qi·GCpi) | `G21:N30` | For both wind directions, with +/− internal pressure |
| Windward Overhang Pressure | `F36` | = qh·G·0.8 (added to windward roof) |
| Parapet pressures (WW & LW) | `E42`, `E43` | qp·GCpn (+1.5 WW / −1.0 LW, or +1.8/−1.1 for ASCE 7-02) |
| Windward Wall qz at elevations | `F48:F73` | Velocity pressure profile: 0.00256·Ke·Kz·Kzt·Kd·V²·I·G·Cp |
| Net pressures with ±GCpi | `G48:J73` | WW pressure minus LW or SW at each z |

### 2.3 `MWFRS≤60` — Low-Rise Procedure

ASCE 7 Chapter 28 (Envelope Procedure). Only valid when h ≤ 60 ft AND h ≤ B.

**Key Inputs:**

Same global inputs. Additionally uses the `gcp_transverse` named range (`MWFRS≤60!AV38:BF42`) for the Figure 28.3-1 pressure coefficients.

**Computed Outputs:**

| Output | Cell(s) | Description |
|---|---|---|
| Base Pressure qh | `E12` | Same formula as MWFRS all h but uses Kh for ≤60 |
| End Zone Width (2a) | `L12` | = 2 × lesser of (10% of least width, 0.4h), but ≥ 3 ft or 0.04B |
| Zone 2 Length | `L13` | = min(0.5 × least dim, 2.5h) |
| GCpf Coefficients (Zones 1–6, 1E–6E) | `D20:L31` | Case A (transverse) and Case B (longitudinal) |
| Surface Pressures = qh × (GCpf ± GCpi) | `E35:L46` | For all zones, both load cases |
| Horizontal Diaphragm Pressures | `E54:E61` | Interior zone and end zone wall/roof forces |
| Parapet Pressures | `E49:E50` | WW and LW using same GCpn values |
| Windward Roof Overhang | `K50` | 0.85·qh·0.8 (ASCE 7-05 and earlier) or 0.7·qh (ASCE 7-10+) |
| Torsional Loads | — | Note: 25% of zone pressures per code requirement |

### 2.4 `C&C` — Components and Cladding

**Key Inputs:**

| Parameter | Cell | Notes |
|---|---|---|
| index_CC | `C&C!AG3` | Selects the C&C procedure: 1 = h ≤ 60, 2 = h > 60 analytical, 3–6 = other roof types |
| Effective Wind Area (user) | Various | User-specified areas for tabular lookup |
| User z-values | `R53:R78` | Optional wall pressure height profile |

**Computed Outputs:**

| Output | Cell(s) | Description |
|---|---|---|
| qh (base pressure) | `E12` | Uses Kh for "all other" (not MWFRS ≤ 60) |
| Zone dimension (a) | `H13` | = max(0.1 × least dim, 0.4h) but ≥ 3 ft |
| Roof Zone Pressures (Zones 1, 2, 3) | `D17:O27` | By effective wind area. Includes ±GCpi. Zone 1=field, 2=edge/eave, 3=corner. |
| Roof Overhang Pressures | `E29:E31` | Additional overhang zone values |
| Parapet Zone Pressures | `F34:M40` | C&C parapet pressures for each zone |
| Wall Zone Pressures (Zones 4, 5) | `D46:O48` | Zone 4=field, Zone 5=corner. Positive and negative. |
| Wall Pressures at z | `E53:O78` | qz·GCp + qi·GCpi at user-defined heights for each effective wind area |

**The C&C output uses a massive switching structure** based on `index_CC` (1–6), mapping to entirely different column blocks for each procedure variant (h ≤ 60, h > 60, stepped roofs, etc.).

### 2.5 `Open Bldg` — Open Building Roofs (Free Roofs)

ASCE 7 §27.3 / Figure 27.3-4 through 27.3-7.

**Additional Inputs:**

| Parameter | Cell | Notes |
|---|---|---|
| Wind Flow | `D13` | Clear or Obstructed (via `index_windflow`) |
| Type of Mono/Gable/Trough Roof | `D12` | Via `index_MonoRoof` |

**Computed Outputs:**

| Output | Cell(s) | Description |
|---|---|---|
| qh (base pressure) | `I18` | Standard velocity pressure at h |
| MWFRS Cn values (normal to ridge) | `F24:G27` | Cnw (windward) and Cnl (leeward), Load Cases A and B |
| MWFRS surface pressures (p = qh·G·Cn) | `F25:G27` | For γ = 0° and 180° |
| MWFRS Cn values (parallel to ridge) | `F38:H41` | By distance from windward edge (≤h, h–2h, >2h) |
| Fascia Panel pressures | `H45:H46` | WW = qp·1.5, LW = qp·(−1.0) |
| C&C Cn values by zone and area | `F58:K60` | Zones 1 (field), 2 (edge), 3 (corner), positive and negative, by effective wind area (≤a², a²–4a², >4a²) |

### 2.6 `Roof W` — Rooftop Structures and Canopies

ASCE 7 §29.4 (Rooftop Structures and Equipment).

**Inputs (per equipment item, up to 2 items):**

| Parameter | Cell(s) | Description |
|---|---|---|
| Equipment length parallel to L | `H21` (item 1), `H38` (item 2) | ft |
| Equipment length parallel to B | `H22`, `H39` | ft |
| Height of equipment | `H23`, `H40` | ft |

**Computed Outputs (per item):**

| Output | Cell(s) | Description |
|---|---|---|
| qh (base pressure) | `M23`, `M40` | At mean roof height |
| Vertical: Ar, GCr, F = qh·GCr·Ar | `E28:E32` | Vertical wind uplift force |
| Horizontal (normal to B): Af, GCr, Fh | `I28:I32` | Lateral force |
| Horizontal (normal to L): Af, GCr, Fh | `M28:M32` | Lateral force |

Also includes **Attached Canopy** pressures for buildings where 60 < h < 90 ft, with C&C zone pressures at user-specified effective wind areas.

### 2.7 `Other W` — Other Structures

ASCE 7 Chapter 29 / §29.3.

**Four sub-modules:**

**A. Solid Freestanding Walls & Signs** (§29.3):

| Input | Cell | Output | Cell |
|---|---|---|---|
| Height to top (h) | `F19` | Cf (from s/h, B/s) | `M19` |
| Height of sign (s) | `F20` | qh at top | `I22` |
| Width (B) | `F21` | F = qh·G·Cf·As | `M20` (per sf) |
| Return length (Lr) | `F22` | Case C zone pressures | `L27:M29` |
| Open area % | `F25` | Open reduction factor | `I25` |

**B. Open Signs & Single-Plane Open Frames** (§29.3):

| Input | Cell | Output | Cell |
|---|---|---|---|
| Height to centroid (z) | `F37` | Solidity ratio (ε) | `I41` |
| Width or Diameter | `F39`/`F40` | Cf | `I42` |
| Open area % | `F42` | F = qz·G·Cf·Af | `M40` (per sf) |

**C. Chimneys, Tanks** (§29.4):

| Input | Cell | Output | Cell |
|---|---|---|---|
| Cross-section shape | `F49` | Cf (square normal / diagonal) | `H56`, `M56` |
| Height (h) | `F51` | F = qz·G·Cf·Af | `H57`, `M57` |
| Width (D) | `F52` | Total force | `H59`, `M59` |

**D. Trussed Towers** (§29.3):

| Input | Cell | Output | Cell |
|---|---|---|---|
| Solidity ratio (ε) | `F64` | Cf (from ε and shape) | `H70`, `M70` |
| Cross-section | `F65` | Diagonal wind factor | `M66` |
| Member shape (flat/round) | `F66` | F = qz·G·Cf·Af | `H71`, `M71` |

---

## 3. Master Equation List

### 3.1 Velocity Pressure (Core Equation)

**Eq. 26.10-1 (ASCE 7-10+):**

```
qz = 0.00256 · Ke · Kz · Kzt · Kd · V²   (psf)
```

In the workbook, this is computed as:

```
qz = [Wind!F74] · Kz · Kzt · [Wind!AD18] · V² · I
```

Where:
- `Wind!F74` = 0.00256 · Ke (the adjusted constant)
- `Wind!AD18` = Kd (directionality factor)
- `V` = `Wind!E12` (ultimate wind speed)
- `I` = `Code!F22` (importance factor; 1.0 for ASCE 7-10+)

For ASCE 7-05 and earlier, V is the basic wind speed and I is the importance factor per category.

### 3.2 Velocity Pressure Exposure Coefficient Kz

**Table 26.10-1 / Eq. 26.10.1:**

```
Kz = 2.01 · (z/zg)^(2/α)    for z ≥ zmin
Kz = 2.01 · (zmin/zg)^(2/α)  for z < zmin
```

Exposure constants (from `Wind` hidden columns):

| Exposure | α | zg (ft) | zmin (ft) |
|---|---|---|---|
| B | 7.0 | 1200 | 30 |
| C | 9.5 | 900 | 15 |
| D | 11.5 | 700 | 7 |

**Note:** ASCE 7-22 changed the values for Exposure B (α = 7.5, zg = 2460 in the hidden column `AK28:AK29`). The sheet branches on `index_windCode` to select the correct constants.

The MWFRS ≤ 60 procedure uses a single Kh evaluated at mean roof height h (but not less than zmin). The "all h" directional procedure evaluates Kz at each elevation z.

### 3.3 Topographic Factor Kzt

**Eq. 26.8-1:**

```
Kzt = (1 + K1 · K2 · K3)²
```

Workbook implementation at `Wind!F39`:

```
F39 = (1 + F35 * F36 * F37)²
```

Where:
- **K1** (`F35`): = (K1 factor from Table 26.8-1) × (H/Lh). Set to 0 if H/Lh < 0.2 or topography is Flat.
- **K2** (`F36`): Horizontal attenuation. = max(0, 1 − |x|/Lh/μ), where μ depends on upwind (1.5) or downwind (1.5) multiplier from `AK37:AL37`.
- **K3** (`F37`): Vertical attenuation. = e^(−γ · z/Lh), where γ is the height attenuation factor from `AJ37`.

The spreadsheet clips K1 to 0 when H/Lh < 0.2 (§26.8.1 exemption), and caps H/Lh at 0.5 (modifying Lh = 2H when actual H/Lh > 0.5).

### 3.4 Ground Elevation Factor Ke

**Eq. 26.9-1 (ASCE 7-16+):**

```
Ke = e^(−0.0000362 · zg)
```

Where zg = ground elevation above sea level (ft).

Workbook at `Wind!K72`:

```
K72 = EXP(-0.0000362 * F72)   [when index_windCode ≥ 5]
```

For ASCE 7-10 and earlier (`index_windCode < 5`), Ke is replaced by an air density adjustment to the 0.00256 constant.

### 3.5 Gust Effect Factor

**Rigid Structure (§26.11.4) — Eq. 26.11-4:**

```
G = 0.925 · (1 + 1.7·gQ·Iz·Q) / (1 + 1.7·gv·Iz)
```

But the code permits G = 0.85 as a default. The workbook uses 0.85 when `index_gust = 1`. When `index_gust = 2` (calculated rigid), it computes G per the equation above, capped at a minimum of 0.85.

Supporting terms (all computed on the Wind sheet):
- `Iz` (`D60`) = c · (33/z̄)^(1/6) — turbulence intensity at z̄
- `Lz` (`D58`) = ℓ · (z̄/33)^ε̄ — integral length scale
- `Q` (`D59`) = √(1 / (1 + 0.63·((B+h)/Lz)^0.63)) — background response factor
- `gQ = gv = 3.4`
- z̄ = max(0.6h, zmin)

**Flexible Structure (§26.11.5) — Eq. 26.11-10:**

```
Gf = 0.925 · (1 + 1.7·Iz·√(gQ²·Q² + gR²·R²)) / (1 + 1.7·gv·Iz)
```

Where R is the resonant response factor involving:
- `Rn` (`I59`) = 7.47·N₁ / (1 + 10.3·N₁)^(5/3) — reduced frequency
- `N₁` (`I58`) = η₁·Lz / V̄z — dimensionless frequency
- `V̄z` (`I57`) = b̄·(z̄/33)^α̅ · V · (88/60) — mean hourly wind speed at z̄
- `Rh`, `RB`, `RL` (`I60:I62`) = aerodynamic admittance functions = 1/η − 1/(2η²)·(1−e^(−2η))
- `R²` (`I64`) = √(Rn·Rh·RB·(0.53 + 0.47·RL) / β)
- `gR` (`I63`) = √(2·ln(3600·η₁)) + 0.577/√(2·ln(3600·η₁))

### 3.6 MWFRS Surface Pressure — Directional Procedure

**Eq. 27.3-1:**

```
p = q·G·Cp − qi·(GCpi)
```

Where:
- q = qz for windward walls (varies with height), qh for all other surfaces
- qi = qh for enclosed/open; qz at specified height for partially enclosed
- Cp = external pressure coefficient from ASCE 7 tables (see §4 below for lookup logic)

Workbook implementation for the windward wall at elevation z (`MWFRS all h!F48`):

```
F48 = [Wind!F74] · Kz · Kzt · Kd · V² · I · G · Cp_windward_wall
```

And the net pressure with positive internal pressure:

```
G48 = F48 − qh · GCpi   (most critical for windward wall)
H48 = F48 + qh · GCpi   (suction case)
```

For partially enclosed buildings, qi may differ from qh:

```
G48 = F48 − qi · GCpi   [qi evaluated at user-specified z for internal pressure]
```

### 3.7 MWFRS Surface Pressure — Low-Rise Envelope Procedure

**Eq. 28.3-1:**

```
p = qh · [(GCpf) − (GCpi)]
```

Workbook at `MWFRS≤60!E35`:

```
E35 = E12 · (GCpf_zone + GCpi)
F35 = E12 · (GCpf_zone − GCpi)
```

Where `GCpf` values come from Figure 28.3-1 lookup tables (`gcp` named range), interpolated by roof angle θ.

### 3.8 C&C Surface Pressure

**Eq. 30.3-1 (h ≤ 60 ft) and Eq. 30.4-1 (h > 60 ft):**

```
p = qh · [(GCp) − (GCpi)]     [h ≤ 60]
p = qh · [(GCp) ± (GCpi)]     [h > 60 — use qh for roof, qz for walls]
```

For walls at elevation z in the h > 60 procedure:

```
p = qz · GCp + qh · (±GCpi)
```

Workbook implementation (`C&C!H53` for wall at z):

```
H53 = Wind!F74 · Kz · Kzt · Kd · V² · I
```

Then surface pressure = `H53 · GCp + GCpi · E12`, with minimum pressure enforced.

### 3.9 Parapet Pressure

**Eq. 27.3-4 / §26.10.3:**

```
pp = qp · GCpn
```

Where:
- qp = velocity pressure evaluated at parapet height
- GCpn = +1.5 (windward) / −1.0 (leeward) for ASCE 7-05+
- GCpn = +1.8 (windward) / −1.1 (leeward) for ASCE 7-02

The workbook computes qp at `MWFRS all h!F40`:

```
F40 = Wind!F74 · Kz_parapet · Kzt_parapet · Kd · V² · I
```

### 3.10 Open Building Roof Pressures

**Eq. 27.3-2:**

```
p = qh · G · Cn
```

Where Cn = net pressure coefficient (combined top and bottom surfaces) from ASCE 7 Figure 27.3-4 through 27.3-7, depending on roof type, wind direction, and Clear vs. Obstructed flow.

### 3.11 Rooftop Equipment Forces

**§29.4 / Eq. 29.4-2:**

```
F = qh · GCr · Af  (horizontal)
F = qh · GCr · Ar  (vertical)
```

Where GCr depends on the ratio of equipment plan area to building plan area. The workbook uses GCr = 1.9 for horizontal and GCr = 1.5 for vertical.

### 3.12 Solid Signs / Freestanding Walls

**§29.3 / Eq. 29.3-1:**

```
F = qh · G · Cf · As
```

Cf is looked up from Figure 29.3-1 based on B/s and s/h ratios. Case C provides distance-dependent Cf values (0 to s, s to 2s, 2s to 3s).

### 3.13 Chimneys, Tanks & Similar

**§29.4:**

```
F = qz · G · Cf · Af
```

Cf depends on cross-section shape (square, hexagonal, round) and h/D ratio, interpolated from Figure 29.4-1. For square sections, separate Cf values are provided for wind normal to face and along diagonal.

### 3.14 Trussed Towers

**§29.3:**

```
F = qz · G · Cf · Af
```

Cf depends on solidity ratio (ε), cross-section shape (square/triangular), and member shape (flat/round), from Figure 29.3-2. Diagonal wind factor of 1.2 applies for square towers.

### 3.15 Approximate Natural Frequency

**§26.11.3 (Commentary):**

The workbook provides three approximate formulas (selected via `index_approx_nat_freq`):

```
Steel MRF:     ηa = 22.2 / h^0.8
Concrete MRF:  ηa = 43.5 / h^0.9
Other systems: ηa = 75 / h
```

### 3.16 Ri — Large Volume Partially Enclosed Building Reduction

**§26.13-1 Note:**

```
Ri = 0.5 · (1 + 1/√(1 + Vi/(22800·Aog)))
```

Capped at a maximum of 1.0. Implemented at `Wind!H115`.

---

## 4. Logical Decision Trees

### 4.1 Enclosure Classification

The `Wind` sheet (rows 77–115) determines enclosure via three sequential tests on user-input opening areas:

```
INPUTS: Ao, Ag, Aoi, Agi  [cells F87:F90]

TEST 1 — Partially Enclosed? (Must pass ALL three):
  ├─ Ao ≥ 1.1 · Aoi                     [J87]
  ├─ Ao > min(4 sf, 0.01·Ag)            [J88]
  └─ Aoi / Agi ≤ 0.20                   [J89]
  
  IF all three = YES → Partially Enclosed (GCpi = ±0.55)

TEST 2 — Open?
  └─ All walls ≥ 80% open (Ao ≥ 0.8·Ag) → Open (GCpi = 0)

TEST 3 — Partially Open? (ASCE 7-16+ only, index_windCode ≥ 5)
  └─ Does not qualify as Open, Enclosed, or Partially Enclosed
  └─ Same pressures as Enclosed (GCpi = ±0.18)

DEFAULT → Enclosed (GCpi = ±0.18)
```

The user selects the classification via `index_intpressure`, which maps to:

| Index | Classification | GCpi |
|---|---|---|
| 1 | Enclosed | ±0.18 |
| 2 | Partially Enclosed | ±0.55 |
| 3 | Open | 0 |
| 4 | Partially Open (ASCE 7-16+) | ±0.18 |

### 4.2 MWFRS Procedure Selection

The workbook does **not** automatically select one procedure over the other. Both `MWFRS all h` and `MWFRS≤60` sheets compute independently. However, the `MWFRS≤60` sheet enforces applicability:

```
IF h > 60 ft → "h > 60 — can't use low-rise method"    [L14]
IF h > B    → "h > B — can't use low-rise method"       [L14]
IF GCpi = 0 → "Open Bldg — procedure doesn't apply"     [D14]
```

Specifically:

```
MWFRS≤60!E12 = IF(Code!F37 ≤ Code!F36,  [computed qh],  0)
```

This zeroes out all low-rise results when h > B, effectively disabling the sheet.

**The Directional Procedure (`MWFRS all h`) is always available** for any height, any enclosure, any building. The low-rise procedure is an **additional** option when h ≤ 60 ft AND h ≤ least horizontal dimension.

### 4.3 C&C Procedure Selection

Controlled by `index_CC` at `C&C!AG3`, which routes output through entirely different column blocks:

```
index_CC = 1  → h ≤ 60 ft procedure (Figure 30.3-1 through 30.3-7)
index_CC = 2  → h > 60 ft analytical (Figure 30.6-1)
index_CC = 3  → Multispan Gable
index_CC = 4  → Stepped Roof
index_CC = 5  → Sawtooth Roof  
index_CC = 6  → Additional roof type
```

An alternate procedure for 60 < h < 90 ft is available when `Alt60_90` (`C&C!AR2`) = "yes".

### 4.4 Topographic Effects

```
IF topography = "Flat" → Kzt = 1.0  (bypass all calculations)
IF H < Hmin (exposure-dependent) → Kzt = 1.0
IF H/Lh < 0.2 → Kzt = 1.0
OTHERWISE:
  ├─ Cap H/Lh at 0.5 (if > 0.5, adjust Lh = 2H)
  ├─ Compute K1, K2, K3 from Table 26.8-1
  └─ Kzt = (1 + K1·K2·K3)²
```

Hmin thresholds from `Wind!AI38`:
- Exposure A: 60 ft
- Exposure B: 60 ft  
- Exposure C: 15 ft
- Exposure D: 15 ft

### 4.5 Ke / Elevation Factor

```
IF index_windCode ≥ 5 (ASCE 7-16+):
  ├─ Ke = e^(−0.0000362 · ground_elevation)
  └─ Adjusted constant = 0.00256 · Ke

IF index_windCode < 5 (ASCE 7-10 and earlier):
  ├─ User may provide air density override
  ├─ Default air density = 0.0765 lb/ft³ (sea level)
  └─ Adjusted constant = 0.00256 · (ρ / 0.0765)
```

### 4.6 ASCE 7 Edition Branching

The `index_windCode` integer (1–6) drives all edition-specific logic:

| index_windCode | Edition | Key Differences |
|---|---|---|
| 1 | ASCE 7-98 | Basic wind speed (3-sec gust), importance factor required, no Ke |
| 2 | ASCE 7-02 | Same as 98 but parapet GCpn = +1.8/−1.1, different low-rise procedure layout |
| 3 | ASCE 7-05 | Same as 02 with some table updates |
| 4 | ASCE 7-10 | Transition to ultimate wind speeds, I = 1.0 for all categories |
| 5 | ASCE 7-16 | Added Ke, tornado provisions for Cat III/IV, updated Kz for Exp B |
| 6 | ASCE 7-22 | Updated exposure B constants (α=7.5, zg=2460), elevated building procedure, Partially Open classification |

Throughout the workbook, this manifests as `IF(index_windCode < 4, ...)` (pre-2010 vs. post-2010 wind maps), `IF(index_windCode < 5, ...)` (pre-2016 vs. with Ke), and `IF(index_windCode < 6, ...)` (pre-2022 vs. latest).

### 4.7 Roof Type Logic

The `index_roof` (1–6) controls which Cp tables are used and which angle ranges are valid:

| index_roof | Type | Min Angle | Max Angle |
|---|---|---|---|
| 1 | Monoslope | 0° | 30° |
| 2 | Hip | 7° | 27° |
| 3 | Gable | 0° | 45° |
| 4 | Multispan Gable | 10° | 45° |
| 5 | Sawtooth | 0° | 45° |
| 6 | Stepped | 0° | 7° |

For Hip roofs at angles outside the valid range, Gable values are substituted with a warning.

---

## 5. Relational Lookup Tables (Tables Sheet Schema)

### 5.1 Building Code Master Table

- **Named Range:** `codes` = `Tables!B5:U30`
- **Purpose:** Maps each supported building code (rows) to its wind, snow, seismic, and live load basis codes
- **Axes:** Row = building code (IBC 2000 through IBC 2024, ASCE 7-98 through 7-22, state codes), Columns = code name, wind basis, wind code index, snow basis, seismic basis, live load basis, etc.

### 5.2 Wind Code Index Table

- **Named Range:** `wind` = `Tables!AD7:AF23`
- **Purpose:** Maps each wind code string to its internal `index_windCode` (1–6)
- **Axes:** Row = wind code name (e.g., "ASCE 7-16"), Col 1 = name, Col 2 = index

### 5.3 Velocity Pressure Exposure Coefficients (Kz)

- **Location:** `Wind` sheet hidden columns (~AD28:AK33)
- **Purpose:** Exposure-dependent constants α, zg, zmin for the Kz power law
- **Axes:** Row = parameter (α, zg), Col = exposure category (B, C, D) × ASCE 7 edition (pre-22 vs. 22+)

### 5.4 Topographic Factor (K1/K2/K3) Tables

- **Location:** `Wind` sheet hidden columns (~AF35:AL38)
- **Purpose:** K1 factor per H/Lh, K3 attenuation factor γ, and horizontal attenuation multipliers μ
- **Axes:** Row = hill shape (2D ridge, 2D escarpment, 3D hill), Col = Exposure (B, C, D), up/downwind multiplier

### 5.5 Directionality Factor Kd

- **Named Range:** `Kd` = `Wind!AS26:AS27`
- **Purpose:** Kd value for buildings (0.85) and other structure types
- **Axes:** Row = structure type, Col = Kd value

### 5.6 Internal Pressure Coefficients

- **Named Range:** `intpressure` = `Wind!AH50:AI53`
- **Purpose:** Maps enclosure classification to GCpi value
- **Axes:** Row = classification (Enclosed, Partially Enclosed, Open, Partially Open), Col = GCpi value and label

### 5.7 Gust Effect Factor Options

- **Named Range:** `Gust` = `Wind!BC17:BE19`
- **Purpose:** Maps gust method index to label and default value
- **Axes:** Row = method (rigid default, rigid calculated, flexible), Col = label, G value

### 5.8 MWFRS Wall Cp Tables (Directional)

- **Location:** `MWFRS all h` hidden columns (~AK:AR)
- **Purpose:** Leeward wall Cp as function of L/B ratio; roof Cp as function of h/L ratio and angle
- **Axes:** Leeward wall: interpolated from L/B breakpoints (1, 2, 4). Roof: by distance zone (0–h/2, h/2–h, h–2h, >2h) and angle.

### 5.9 MWFRS Low-Rise GCpf Table (Envelope)

- **Named Range:** `gcp_transverse` = `MWFRS≤60!AV38:BF42`
- **Purpose:** Combined GCpf values for Figure 28.3-1 zones (1–6, 1E–6E), Cases A and B
- **Axes:** Row = roof angle breakpoints (0°, 5°/7°, 10°/20°, etc.), Col = zone number × load case. Interpolation by roof angle θ.

### 5.10 C&C GCp Tables

- **Named Range (examples):** `roof1` through `roof5` = `C&C!DD68:DN104`
- **Purpose:** External pressure coefficients GCp for C&C by roof zone, effective wind area, and roof angle
- **Axes:** Row = effective wind area breakpoints (10, 20, 50, 100, 200, 500 sf), Col = zone (1/2/3 for roof, 4/5 for walls) × positive/negative. Separate table blocks for each roof angle range (0–10°, 10–30°, 30–45°).
- **Interpolation:** Log-linear interpolation on effective wind area.

### 5.11 Open Building Cn Tables

- **Location:** `Open Bldg` hidden columns
- **Purpose:** Net pressure coefficients Cn for free roofs by roof type, wind direction, flow type, and load case
- **Axes:** Row = load case (A, B), Col = wind direction (normal: Cnw/Cnl; parallel: distance zones ≤h, h–2h, >2h)

### 5.12 Solid Sign Cf Table

- **Location:** `Other W` hidden columns (~AR43:AR88)
- **Purpose:** Force coefficients for solid signs (by B/s and s/h), chimneys (by h/D and shape), trussed towers (by ε and shape)
- **Axes:** Vary per structure type. Interpolation on ratios.

### 5.13 Approximate Natural Frequency Options

- **Named Range:** `approx_nat_freq` = `Wind!AH57:AH59`
- **Purpose:** Three structural system types for the approximate frequency formula
- **Index:** 1=Steel MRF, 2=Concrete MRF, 3=Other

### 5.14 Roof Type Lookup

- **Named Range:** `name_roof` = `Wind!AJ15:AJ20`
- **Purpose:** Maps index_roof (1–6) to roof type name and valid angle ranges
- **Axes:** Row = roof type, Col = name, min angle, max angle

### 5.15 Exposure Constants

- **Named Range:** `exposure` = `Wind!AU17:AU20`; `name_exp` = `Wind!AV17:AV20`
- **Purpose:** Maps index_exp to exposure category letter
- **Supporting data:** Exposure-dependent constants (ε̄, ℓ, zmin, c, b̄, α̅) at `Wind!D53:D56` and `Wind!I55:I56`, computed via IF chains on `Exp_cat`.

---

## Appendix: Key Named Ranges Quick Reference

| Named Range | Location | Purpose |
|---|---|---|
| `angle` | `Code!G34` | Roof pitch in degrees |
| `h_wind` | `Code!F37` | Mean roof height |
| `hb_wind` | `Code!F40` | Elevated building hb |
| `Exp_cat` | `Wind!E15` | Exposure category letter |
| `I` | `Code!AS22` → `Code!F22` | Wind importance factor |
| `index_windCode` | `Tables!D3` | ASCE 7 edition (1–6) |
| `index_roof` | `Wind!AJ14` | Roof type (1–6) |
| `index_intpressure` | `Wind!AK49` | Enclosure classification (1–4) |
| `index_exp` | `Wind!AV16` | Exposure category index |
| `index_gust` | `Wind!BD16` | Gust method (1–3) |
| `index_topo` | `Wind!AT32` | Topography type (1–4) |
| `index_CC` | `C&C!AG3` | C&C procedure variant (1–6) |
| `index_ridge` | `MWFRS all h!AN3` | Ridge orientation (1–2) |
| `Ri` | `Wind!H115` | Large volume reduction factor |
| `Kd` | `Wind!AS26:AS27` | Directionality factor table |
| `intpressure` | `Wind!AH50:AI53` | GCpi lookup table |
| `gcp` | `MWFRS≤60!AV39:BF41` | Low-rise GCpf values |
| `codes` | `Tables!B5:U30` | Master code table |
| `wind` | `Tables!AD7:AF23` | Wind code index table |

---

*End of architectural blueprint. This document maps the complete engineering logic skeleton — all formulas, decision trees, and data dependencies — required to independently implement the ASCE 7 wind load suite without referencing any proprietary source code or UI design.*
