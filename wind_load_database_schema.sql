-- ============================================================================
-- ASCE 7 WIND LOAD SUITE — PostgreSQL Database Schema
-- ============================================================================
-- Normalized relational schema for all wind load lookup tables.
-- Designed for efficient interpolation queries by the calculation engine.
--
-- CONVENTIONS:
--   • Every coefficient table carries a `code_version` column (e.g. '7-10',
--     '7-16', '7-22') so the engine queries the correct edition dynamically.
--   • Breakpoint columns are typed NUMERIC(8,4) for precision in interpolation.
--   • Each table is indexed on the axes used for nearest-bound lookups.
--   • NULL in a breakpoint column means "all values" (wildcard).
-- ============================================================================


-- ============================================================================
-- 0. REFERENCE / ENUMERATION TABLES
-- ============================================================================

CREATE TABLE asce7_editions (
    code_version    VARCHAR(8) PRIMARY KEY,      -- '7-98','7-02','7-05','7-10','7-16','7-22'
    edition_year    SMALLINT NOT NULL,
    uses_ultimate   BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE for 7-10+
    has_ke          BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE for 7-16+
    has_tornado     BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE for 7-22
    wind_code_index SMALLINT NOT NULL UNIQUE          -- 1–6, maps to spreadsheet index_windCode
);

INSERT INTO asce7_editions VALUES
    ('7-98',  1998, FALSE, FALSE, FALSE, 1),
    ('7-02',  2002, FALSE, FALSE, FALSE, 2),
    ('7-05',  2005, FALSE, FALSE, FALSE, 3),
    ('7-10',  2010, TRUE,  FALSE, FALSE, 4),
    ('7-16',  2016, TRUE,  TRUE,  FALSE, 5),
    ('7-22',  2022, TRUE,  TRUE,  TRUE,  6);


CREATE TABLE exposure_categories (
    exposure    CHAR(1) PRIMARY KEY CHECK (exposure IN ('A','B','C','D'))
);

INSERT INTO exposure_categories VALUES ('A'),('B'),('C'),('D');


CREATE TABLE roof_types (
    roof_type_id    SMALLINT PRIMARY KEY,
    name            VARCHAR(30) NOT NULL,
    min_angle_deg   NUMERIC(5,2) NOT NULL DEFAULT 0,
    max_angle_deg   NUMERIC(5,2) NOT NULL
);

INSERT INTO roof_types VALUES
    (1, 'Monoslope',       0,   30),
    (2, 'Hip',             7,   27),
    (3, 'Gable',           0,   45),
    (4, 'Multispan Gable', 10,  45),
    (5, 'Sawtooth',        0,   45),
    (6, 'Stepped',         0,    7);


CREATE TABLE enclosure_classifications (
    enclosure_id    SMALLINT PRIMARY KEY,
    name            VARCHAR(30) NOT NULL,
    gcpi            NUMERIC(5,3) NOT NULL,
    min_code_version VARCHAR(8)               -- NULL = available in all editions
);

INSERT INTO enclosure_classifications VALUES
    (1, 'Enclosed',           0.18,  NULL),
    (2, 'Partially Enclosed', 0.55,  NULL),
    (3, 'Open',               0.00,  NULL),
    (4, 'Partially Open',     0.18,  '7-16');  -- Only ASCE 7-16+


CREATE TABLE topography_types (
    topo_type_id    SMALLINT PRIMARY KEY,
    name            VARCHAR(30) NOT NULL
);

INSERT INTO topography_types VALUES
    (1, 'Flat'),
    (2, '2D Ridge'),
    (3, '2D Escarpment'),
    (4, '3D Axisymmetrical Hill');


-- ============================================================================
-- 1. TERRAIN EXPOSURE CONSTANTS
-- ============================================================================
-- Source: ASCE 7 Table 26.11-1 (Gust), Table 26.10-1 (Kz)
-- These constants feed both Kz and the Gust Effect Factor calculations.
-- ASCE 7-22 changed Exposure B values, so code_version is essential.
-- ============================================================================

CREATE TABLE terrain_exposure_constants (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8) NOT NULL REFERENCES asce7_editions(code_version),
    exposure        CHAR(1)   NOT NULL REFERENCES exposure_categories(exposure),

    -- Velocity pressure exposure coefficients (Table 26.10-1)
    alpha           NUMERIC(6,4) NOT NULL,   -- power-law exponent (e.g. 9.5 for Exp C)
    zg_ft           NUMERIC(8,2) NOT NULL,   -- gradient height (ft)
    z_min_ft        NUMERIC(6,2) NOT NULL,   -- minimum height (ft)

    -- Gust effect factor constants (Table 26.11-1)
    epsilon_bar     NUMERIC(8,5) NOT NULL,   -- ε̄ (integral length scale exponent)
    ell_ft          NUMERIC(8,2) NOT NULL,   -- ℓ (integral length scale at 33 ft)
    c               NUMERIC(6,4) NOT NULL,   -- turbulence intensity coefficient
    b_bar           NUMERIC(8,5) NOT NULL,   -- b̄ (mean hourly speed factor)
    alpha_bar       NUMERIC(8,5) NOT NULL,   -- ᾱ (mean hourly speed exponent)

    UNIQUE (code_version, exposure)
);

-- Pre-ASCE 7-22 values (apply to '7-98' through '7-16')
-- Note: Exposure A was removed in ASCE 7-02 but retained here for 7-98 support.
INSERT INTO terrain_exposure_constants
    (code_version, exposure, alpha, zg_ft, z_min_ft, epsilon_bar, ell_ft, c, b_bar, alpha_bar) VALUES
    -- ASCE 7-98 only
    ('7-98', 'A',  5.0,   1500, 60, 0.500, 180, 0.45, 0.30, 0.3333),
    ('7-98', 'B',  7.0,   1200, 30, 0.333, 320, 0.30, 0.45, 0.2500),
    ('7-98', 'C',  9.5,    900, 15, 0.200, 500, 0.20, 0.65, 0.1538),
    ('7-98', 'D', 11.5,    700,  7, 0.125, 650, 0.15, 0.80, 0.1111),
    -- ASCE 7-02 through 7-16 (Exposure A removed)
    ('7-02', 'B',  7.0,   1200, 30, 0.333, 320, 0.30, 0.45, 0.2500),
    ('7-02', 'C',  9.5,    900, 15, 0.200, 500, 0.20, 0.65, 0.1538),
    ('7-02', 'D', 11.5,    700,  7, 0.125, 650, 0.15, 0.80, 0.1111),
    ('7-05', 'B',  7.0,   1200, 30, 0.333, 320, 0.30, 0.45, 0.2500),
    ('7-05', 'C',  9.5,    900, 15, 0.200, 500, 0.20, 0.65, 0.1538),
    ('7-05', 'D', 11.5,    700,  7, 0.125, 650, 0.15, 0.80, 0.1111),
    ('7-10', 'B',  7.0,   1200, 30, 0.333, 320, 0.30, 0.45, 0.2500),
    ('7-10', 'C',  9.5,    900, 15, 0.200, 500, 0.20, 0.65, 0.1538),
    ('7-10', 'D', 11.5,    700,  7, 0.125, 650, 0.15, 0.80, 0.1111),
    ('7-16', 'B',  7.0,   1200, 30, 0.333, 320, 0.30, 0.45, 0.2500),
    ('7-16', 'C',  9.5,    900, 15, 0.200, 500, 0.20, 0.65, 0.1538),
    ('7-16', 'D', 11.5,    700,  7, 0.125, 650, 0.15, 0.80, 0.1111),
    -- ASCE 7-22 (Exposure B changed: alpha 7.0→7.5, zg 1200→2460; b_bar, alpha_bar also changed)
    ('7-22', 'B',  7.5,   2460, 30, 0.333, 320, 0.30, 0.47, 0.2222),
    ('7-22', 'C',  9.8,    900, 15, 0.200, 500, 0.20, 0.66, 0.1563),
    ('7-22', 'D', 11.5,    700,  7, 0.125, 650, 0.15, 0.78, 0.1250);

CREATE INDEX idx_terrain_exposure ON terrain_exposure_constants (code_version, exposure);


-- ============================================================================
-- 2. TOPOGRAPHIC FACTOR COEFFICIENTS (Kzt)
-- ============================================================================
-- Source: ASCE 7 Table 26.8-1
-- K1, gamma, and mu values by topography type and exposure category.
-- These are edition-invariant through all current ASCE 7 versions.
-- ============================================================================

CREATE TABLE kzt_coefficients (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8) NOT NULL REFERENCES asce7_editions(code_version),
    topo_type_id    SMALLINT  NOT NULL REFERENCES topography_types(topo_type_id),
    exposure        CHAR(1)   NOT NULL REFERENCES exposure_categories(exposure),

    k1_factor       NUMERIC(6,4) NOT NULL,   -- K1/(H/Lh) from Table 26.8-1
    gamma           NUMERIC(6,4) NOT NULL,   -- γ vertical attenuation exponent
    mu_upwind       NUMERIC(6,4) NOT NULL,   -- μ horizontal attenuation (upwind)
    mu_downwind     NUMERIC(6,4) NOT NULL,   -- μ horizontal attenuation (downwind)
    h_min_ft        NUMERIC(6,2) NOT NULL,   -- minimum H for Kzt to apply

    UNIQUE (code_version, topo_type_id, exposure)
);

-- Data from Wind!AF36:AL38 (edition-invariant, insert for all editions)
-- Flat topography is excluded (Kzt = 1.0 by definition)
INSERT INTO kzt_coefficients
    (code_version, topo_type_id, exposure, k1_factor, gamma, mu_upwind, mu_downwind, h_min_ft)
SELECT e.code_version, t.topo_type_id, t.exposure,
       t.k1_factor, t.gamma, t.mu_upwind, t.mu_downwind, t.h_min_ft
FROM asce7_editions e
CROSS JOIN (VALUES
    -- 2D Ridge (topo=2)
    (2, 'B', 1.3000, 3.0, 1.5, 1.5, 60.0),
    (2, 'C', 1.4500, 3.0, 1.5, 1.5, 15.0),
    (2, 'D', 1.5500, 3.0, 1.5, 1.5, 15.0),
    -- 2D Escarpment (topo=3)
    (3, 'B', 0.7500, 2.5, 1.5, 4.0, 60.0),
    (3, 'C', 0.8500, 2.5, 1.5, 4.0, 15.0),
    (3, 'D', 0.9500, 2.5, 1.5, 4.0, 15.0),
    -- 3D Axisymmetrical Hill (topo=4)
    (4, 'B', 0.9500, 4.0, 1.5, 1.5, 60.0),
    (4, 'C', 1.0500, 4.0, 1.5, 1.5, 15.0),
    (4, 'D', 1.1500, 4.0, 1.5, 1.5, 15.0)
) AS t(topo_type_id, exposure, k1_factor, gamma, mu_upwind, mu_downwind, h_min_ft);

CREATE INDEX idx_kzt ON kzt_coefficients (code_version, topo_type_id, exposure);


-- ============================================================================
-- 3. DIRECTIONALITY FACTOR (Kd)
-- ============================================================================
-- Source: ASCE 7 Table 26.6-1
-- ============================================================================

CREATE TABLE directionality_factors (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)  NOT NULL REFERENCES asce7_editions(code_version),
    structure_type  VARCHAR(50) NOT NULL,     -- 'Buildings','Solid Signs','Open Signs',
                                              -- 'Chimneys','Trussed Towers (sq/rect)',
                                              -- 'Trussed Towers (other)'
    kd              NUMERIC(4,2) NOT NULL,

    UNIQUE (code_version, structure_type)
);

-- Values are edition-invariant; seed for all editions
INSERT INTO directionality_factors (code_version, structure_type, kd)
SELECT e.code_version, t.structure_type, t.kd
FROM asce7_editions e
CROSS JOIN (VALUES
    ('Buildings',                   0.85),
    ('Solid Signs',                 0.85),
    ('Open Signs',                  0.85),
    ('Chimneys',                    0.90),
    ('Trussed Towers (sq/rect)',    0.85),
    ('Trussed Towers (other)',      0.95)
) AS t(structure_type, kd);


-- ============================================================================
-- 4. MWFRS WALL EXTERNAL PRESSURE COEFFICIENTS (Cp)
-- ============================================================================
-- Source: ASCE 7 Figure 27.3-1
-- Windward wall Cp is always +0.8 (no interpolation needed).
-- Leeward wall Cp depends on L/B ratio.
-- Side wall Cp is always -0.7.
--
-- INTERPOLATION DESIGN: Store the tabulated L/B breakpoints and their Cp values.
-- The engine fetches the two bounding rows and performs linear interpolation.
-- ============================================================================

CREATE TABLE mwfrs_wall_cp (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    surface         VARCHAR(20)  NOT NULL,   -- 'windward', 'leeward', 'side'
    lb_ratio        NUMERIC(6,3),            -- L/B ratio breakpoint (NULL for constant surfaces)
    cp              NUMERIC(6,4) NOT NULL,   -- External pressure coefficient

    UNIQUE (code_version, surface, lb_ratio)
);

-- Data extracted from MWFRS all h!F22 formula and ASCE 7 Figure 27.3-1
-- Leeward wall Cp breakpoints: L/B = 0 → -0.5; 1 → -0.5; 2 → -0.3; 4 → -0.2
-- (The formula interpolates: IF L/B ≤ 1 → -0.5, 1<L/B<2 → linear, 2<L/B<4 → linear, ≥4 → -0.2)
INSERT INTO mwfrs_wall_cp (code_version, surface, lb_ratio, cp)
SELECT e.code_version, t.surface, t.lb_ratio, t.cp
FROM asce7_editions e
CROSS JOIN (VALUES
    ('windward',  NULL,    0.8),
    ('side',      NULL,   -0.7),
    ('leeward',   0.000, -0.5),
    ('leeward',   1.000, -0.5),
    ('leeward',   2.000, -0.3),
    ('leeward',   4.000, -0.2)
) AS t(surface, lb_ratio, cp);

CREATE INDEX idx_mwfrs_wall_cp_interp
    ON mwfrs_wall_cp (code_version, surface, lb_ratio);


-- ============================================================================
-- 5. MWFRS ROOF EXTERNAL PRESSURE COEFFICIENTS (Cp) — DIRECTIONAL
-- ============================================================================
-- Source: ASCE 7 Figure 27.3-1, Roof section
--
-- Two regimes:
--   A) Flat/Low-slope roofs (θ < 10°): Cp by distance zone from windward edge
--      Zones: 0 to h/2, h/2 to h, h to 2h, > 2h.   Axis = h/L ratio.
--   B) Sloped roofs (θ ≥ 10°): Windward and Leeward roof Cp by angle.
--      Leeward Cp also depends on h/L ratio.
-- ============================================================================

CREATE TABLE mwfrs_roof_cp_flat (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    wind_direction  VARCHAR(20)  NOT NULL,   -- 'normal_to_ridge', 'parallel_to_ridge'
    distance_zone   VARCHAR(10)  NOT NULL,   -- '0_to_h2', 'h2_to_h', 'h_to_2h', 'gt_2h'
    hl_ratio        NUMERIC(6,3) NOT NULL,   -- h/L ratio breakpoint
    cp              NUMERIC(6,4) NOT NULL,

    UNIQUE (code_version, wind_direction, distance_zone, hl_ratio)
);

-- Data from MWFRS all h hidden columns (AN-AO rows 9-14, 19-23)
-- Wind Normal to Ridge, angle < 10 deg
INSERT INTO mwfrs_roof_cp_flat (code_version, wind_direction, distance_zone, hl_ratio, cp)
SELECT e.code_version, t.*
FROM asce7_editions e
CROSS JOIN (VALUES
    ('normal_to_ridge', '0_to_h2',  0.500, -0.90),
    ('normal_to_ridge', '0_to_h2',  1.000, -1.04),
    ('normal_to_ridge', 'h2_to_h',  0.500, -0.90),
    ('normal_to_ridge', 'h2_to_h',  1.000, -0.70),
    ('normal_to_ridge', 'h_to_2h',  0.500, -0.50),
    ('normal_to_ridge', 'h_to_2h',  1.000, -0.70),
    ('normal_to_ridge', 'gt_2h',    0.500, -0.30),
    ('normal_to_ridge', 'gt_2h',    1.000, -0.70),
    -- Wind Parallel to Ridge (all angles)
    ('parallel_to_ridge', '0_to_h2',  0.500, -0.90),
    ('parallel_to_ridge', '0_to_h2',  1.000, -1.04),
    ('parallel_to_ridge', 'h2_to_h',  0.500, -0.90),
    ('parallel_to_ridge', 'h2_to_h',  1.000, -0.70),
    ('parallel_to_ridge', 'h_to_2h',  0.500, -0.50),
    ('parallel_to_ridge', 'h_to_2h',  1.000, -0.70),
    ('parallel_to_ridge', 'gt_2h',    0.500, -0.30),
    ('parallel_to_ridge', 'gt_2h',    1.000, -0.70)
) AS t(wind_direction, distance_zone, hl_ratio, cp);

CREATE INDEX idx_mwfrs_roof_flat_interp
    ON mwfrs_roof_cp_flat (code_version, wind_direction, distance_zone, hl_ratio);


CREATE TABLE mwfrs_roof_cp_sloped (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    roof_surface    VARCHAR(20)  NOT NULL,   -- 'windward_neg', 'windward_pos', 'leeward'
    angle_deg       NUMERIC(5,2) NOT NULL,   -- roof angle breakpoint
    hl_ratio        NUMERIC(6,3),            -- h/L breakpoint (NULL when not applicable)
    cp              NUMERIC(6,4) NOT NULL,

    UNIQUE (code_version, roof_surface, angle_deg, hl_ratio)
);

-- Windward roof Cp (negative and positive) by angle — from MWFRS all h rows 27-34
INSERT INTO mwfrs_roof_cp_sloped (code_version, roof_surface, angle_deg, hl_ratio, cp)
SELECT e.code_version, t.*
FROM asce7_editions e
CROSS JOIN (VALUES
    -- Windward negative Cp (from AJ31:AP31 row)
    ('windward_neg', 20.0,  NULL, -0.40),
    ('windward_neg', 25.0,  NULL, -0.30),
    ('windward_neg', 30.0,  NULL, -0.20),
    ('windward_neg', 35.0,  NULL, -0.20),
    ('windward_neg', 45.0,  NULL,  0.00),
    ('windward_neg', 60.0,  NULL,  0.00),
    ('windward_neg', 80.0,  NULL,  0.00),
    -- Windward positive Cp (from AJ32:AP32 row)
    ('windward_pos', 20.0,  NULL,  0.00),
    ('windward_pos', 25.0,  NULL,  0.20),
    ('windward_pos', 30.0,  NULL,  0.20),
    ('windward_pos', 35.0,  NULL,  0.30),
    ('windward_pos', 45.0,  NULL,  0.40),
    ('windward_pos', 60.0,  NULL,  0.60),
    ('windward_pos', 80.0,  NULL,  0.80),
    -- Leeward Cp by angle and h/L (from AI19:AK22)
    ('leeward', 15.0, 0.250, -0.50),
    ('leeward', 15.0, 0.500, -0.50),
    ('leeward', 15.0, 1.000, -0.60),
    ('leeward', 20.0, 0.250, -0.60),
    ('leeward', 20.0, 0.500, -0.60),
    ('leeward', 20.0, 1.000, -0.60)
) AS t(roof_surface, angle_deg, hl_ratio, cp);

CREATE INDEX idx_mwfrs_roof_sloped_interp
    ON mwfrs_roof_cp_sloped (code_version, roof_surface, angle_deg, hl_ratio);


-- ============================================================================
-- 6. MWFRS LOW-RISE GCpf COEFFICIENTS (Envelope Procedure)
-- ============================================================================
-- Source: ASCE 7 Figure 28.3-1
-- Zone-based combined pressure coefficients for h ≤ 60 ft buildings.
-- Interpolation on roof angle θ.
-- ============================================================================

CREATE TABLE mwfrs_lowrise_gcpf (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)  NOT NULL REFERENCES asce7_editions(code_version),
    load_case       CHAR(1)     NOT NULL CHECK (load_case IN ('A','B')),
    zone            VARCHAR(4)  NOT NULL,    -- '1','2','3','4','5','6','1E','2E','3E','4E','5E','6E'
    angle_deg       NUMERIC(5,2) NOT NULL,   -- roof angle breakpoint
    gcpf            NUMERIC(6,4) NOT NULL,   -- combined pressure coefficient

    UNIQUE (code_version, load_case, zone, angle_deg)
);

-- Case A (transverse) data from MWFRS≤60!AW38:BF42
-- angle breakpoints: 0(to 5), 20, 30(to 45), 90
INSERT INTO mwfrs_lowrise_gcpf (code_version, load_case, zone, angle_deg, gcpf)
SELECT e.code_version, t.*
FROM asce7_editions e
CROSS JOIN (VALUES
    -- Case A, Zone 1
    ('A', '1',   0.0,   0.40), ('A', '1',  20.0,   0.53),
    ('A', '1', 30.0,   0.56), ('A', '1',  90.0,   0.56),
    -- Case A, Zone 2
    ('A', '2',   0.0,  -0.69), ('A', '2',  20.0,  -0.69),
    ('A', '2', 30.0,   0.21), ('A', '2',  90.0,   0.56),
    -- Case A, Zone 3
    ('A', '3',   0.0,  -0.37), ('A', '3',  20.0,  -0.48),
    ('A', '3', 30.0,  -0.43), ('A', '3',  90.0,  -0.37),
    -- Case A, Zone 4
    ('A', '4',   0.0,  -0.29), ('A', '4',  20.0,  -0.43),
    ('A', '4', 30.0,  -0.37), ('A', '4',  90.0,  -0.37),
    -- Case A, Zone 1E
    ('A', '1E',  0.0,   0.61), ('A', '1E', 20.0,   0.80),
    ('A', '1E', 30.0,   0.69), ('A', '1E', 90.0,   0.69),
    -- Case A, Zone 2E
    ('A', '2E',  0.0,  -1.07), ('A', '2E', 20.0,  -1.07),
    ('A', '2E', 30.0,   0.27), ('A', '2E', 90.0,   0.69),
    -- Case A, Zone 3E
    ('A', '3E',  0.0,  -0.53), ('A', '3E', 20.0,  -0.69),
    ('A', '3E', 30.0,  -0.53), ('A', '3E', 90.0,  -0.48),
    -- Case A, Zone 4E
    ('A', '4E',  0.0,  -0.43), ('A', '4E', 20.0,  -0.64),
    ('A', '4E', 30.0,  -0.48), ('A', '4E', 90.0,  -0.48),
    -- Case B (longitudinal) — constant across all angles per ASCE 7 Figure 28.3-1
    ('B', '1',   0.0,  -0.45), ('B', '2',   0.0,  -0.69),
    ('B', '3',   0.0,  -0.37), ('B', '4',   0.0,  -0.45),
    ('B', '5',   0.0,   0.40), ('B', '6',   0.0,  -0.29),
    ('B', '1E',  0.0,  -0.48), ('B', '2E',  0.0,  -1.07),
    ('B', '3E',  0.0,  -0.53), ('B', '4E',  0.0,  -0.48),
    ('B', '5E',  0.0,   0.61), ('B', '6E',  0.0,  -0.43)
) AS t(load_case, zone, angle_deg, gcpf);

CREATE INDEX idx_lowrise_gcpf_interp
    ON mwfrs_lowrise_gcpf (code_version, load_case, zone, angle_deg);


-- ============================================================================
-- 7. C&C EXTERNAL PRESSURE COEFFICIENTS (GCp)
-- ============================================================================
-- Source: ASCE 7 Figures 30.3-1 through 30.3-7 (h ≤ 60)
--         and Figures 30.4-1 through 30.6-1 (h > 60)
--
-- INTERPOLATION: Log-linear on effective wind area.
-- The spreadsheet stores GCp at area breakpoints 10, 20, 50, 100, 200, 500 sf
-- and interpolates using log10(area).
--
-- Roof zones: 1 (field), 2 (eave/edge), 3 (corner)
-- Wall zones: 4 (field), 5 (corner)
-- Overhangs:  1o, 2o, 3o
-- ============================================================================

CREATE TABLE cc_roof_gcp (
    id                  SERIAL PRIMARY KEY,
    code_version        VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    procedure_variant   VARCHAR(20)  NOT NULL,   -- 'h_le_60','h_gt_60','stepped','multispan','sawtooth'
    roof_type_id        SMALLINT     REFERENCES roof_types(roof_type_id),
    angle_range         VARCHAR(15)  NOT NULL,   -- '0_to_7','7_to_10','10_to_30','30_to_45','gt_45'
    zone                VARCHAR(4)   NOT NULL,   -- '1','2','3','1o','2o','3o' (o = overhang)
    sign                VARCHAR(8)   NOT NULL CHECK (sign IN ('positive','negative')),
    eff_wind_area_sf    NUMERIC(8,2) NOT NULL,   -- effective wind area breakpoint (sf)
    gcp                 NUMERIC(7,4) NOT NULL,   -- external pressure coefficient (unsigned or signed)

    UNIQUE (code_version, procedure_variant, angle_range, zone, sign, eff_wind_area_sf)
);

-- Sample data from C&C!DD68:DN104 (roof1 through roof5 named ranges)
-- roof1 = 0 to 7 deg (flat), rows 68-74
-- roof2 = 7/10 to 27/30 deg, rows 75-83
-- roof3 = 30 to 45 deg, rows 84-90
-- Effective wind area breakpoints: 10, 20, 50, 100, 200, 500 sf
-- Full data insertion would be extensive; representative subset shown:
INSERT INTO cc_roof_gcp
    (code_version, procedure_variant, roof_type_id, angle_range, zone, sign, eff_wind_area_sf, gcp)
VALUES
    -- Flat roof (0-7 deg), h ≤ 60, Zone 1 negative
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'negative',   10.0, -1.0000),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'negative',   20.0, -0.9699),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'negative',   50.0, -0.9301),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'negative',  100.0, -0.9000),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'negative',  200.0, -0.9000),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'negative',  500.0, -0.9000),
    -- Flat roof, Zone 1 positive
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'positive',   10.0,  0.3000),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'positive',   20.0,  0.2699),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'positive',   50.0,  0.2301),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'positive',  100.0,  0.2000),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'positive',  200.0,  0.2000),
    ('7-22', 'h_le_60', 3, '0_to_7', '1', 'positive',  500.0,  0.2000),
    -- Flat roof, Zone 2 negative
    ('7-22', 'h_le_60', 3, '0_to_7', '2', 'negative',   10.0, -1.8000),
    ('7-22', 'h_le_60', 3, '0_to_7', '2', 'negative',   20.0, -1.5893),
    ('7-22', 'h_le_60', 3, '0_to_7', '2', 'negative',   50.0, -1.3107),
    ('7-22', 'h_le_60', 3, '0_to_7', '2', 'negative',  100.0, -1.1000),
    ('7-22', 'h_le_60', 3, '0_to_7', '2', 'negative',  200.0, -1.1000),
    ('7-22', 'h_le_60', 3, '0_to_7', '2', 'negative',  500.0, -1.1000),
    -- Flat roof, Zone 3 negative
    ('7-22', 'h_le_60', 3, '0_to_7', '3', 'negative',   10.0, -1.8000),
    ('7-22', 'h_le_60', 3, '0_to_7', '3', 'negative',   20.0, -1.5893),
    ('7-22', 'h_le_60', 3, '0_to_7', '3', 'negative',   50.0, -1.3107),
    ('7-22', 'h_le_60', 3, '0_to_7', '3', 'negative',  100.0, -1.1000),
    ('7-22', 'h_le_60', 3, '0_to_7', '3', 'negative',  200.0, -1.1000),
    ('7-22', 'h_le_60', 3, '0_to_7', '3', 'negative',  500.0, -1.1000);

-- NOTE: The full dataset requires ~300+ rows covering all angle ranges,
-- all zones (1,2,3 + overhangs), all signs, all area breakpoints, and
-- all code versions where coefficients differ. The pattern above repeats.

CREATE INDEX idx_cc_roof_gcp_interp
    ON cc_roof_gcp (code_version, procedure_variant, angle_range, zone, sign, eff_wind_area_sf);


CREATE TABLE cc_wall_gcp (
    id                  SERIAL PRIMARY KEY,
    code_version        VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    procedure_variant   VARCHAR(20)  NOT NULL,   -- 'h_le_60', 'h_gt_60'
    zone                CHAR(1)      NOT NULL CHECK (zone IN ('4','5')),
    sign                VARCHAR(8)   NOT NULL CHECK (sign IN ('positive','negative')),
    eff_wind_area_sf    NUMERIC(8,2) NOT NULL,   -- effective wind area breakpoint (sf)
    gcp                 NUMERIC(7,4) NOT NULL,

    UNIQUE (code_version, procedure_variant, zone, sign, eff_wind_area_sf)
);

-- Data from C&C!AV44:BB48
INSERT INTO cc_wall_gcp
    (code_version, procedure_variant, zone, sign, eff_wind_area_sf, gcp)
SELECT e.code_version, t.*
FROM asce7_editions e
CROSS JOIN (VALUES
    -- Zone 4 (field) negative
    ('h_le_60', '4', 'negative',   10.0, -0.9000),
    ('h_le_60', '4', 'negative',   20.0, -0.9000),
    ('h_le_60', '4', 'negative',   50.0, -0.8430),
    ('h_le_60', '4', 'negative',  100.0, -0.7999),
    ('h_le_60', '4', 'negative',  200.0, -0.7568),
    ('h_le_60', '4', 'negative',  400.0, -0.7137),
    ('h_le_60', '4', 'negative',  500.0, -0.7000),
    -- Zone 5 (corner) negative
    ('h_le_60', '5', 'negative',   10.0, -1.8000),
    ('h_le_60', '5', 'negative',   20.0, -1.8000),
    ('h_le_60', '5', 'negative',   50.0, -1.5722),
    ('h_le_60', '5', 'negative',  100.0, -1.3999),
    ('h_le_60', '5', 'negative',  200.0, -1.2276),
    ('h_le_60', '5', 'negative',  400.0, -1.0553),
    ('h_le_60', '5', 'negative',  500.0, -1.0000),
    -- Zone 4 & 5 positive (same values)
    ('h_le_60', '4', 'positive',   10.0,  0.9000),
    ('h_le_60', '4', 'positive',   20.0,  0.9000),
    ('h_le_60', '4', 'positive',   50.0,  0.8146),
    ('h_le_60', '4', 'positive',  100.0,  0.7500),
    ('h_le_60', '4', 'positive',  200.0,  0.6854),
    ('h_le_60', '4', 'positive',  400.0,  0.6208),
    ('h_le_60', '4', 'positive',  500.0,  0.6000),
    ('h_le_60', '5', 'positive',   10.0,  0.9000),
    ('h_le_60', '5', 'positive',   20.0,  0.9000),
    ('h_le_60', '5', 'positive',   50.0,  0.8146),
    ('h_le_60', '5', 'positive',  100.0,  0.7500),
    ('h_le_60', '5', 'positive',  200.0,  0.6854),
    ('h_le_60', '5', 'positive',  400.0,  0.6208),
    ('h_le_60', '5', 'positive',  500.0,  0.6000)
) AS t(procedure_variant, zone, sign, eff_wind_area_sf, gcp);

CREATE INDEX idx_cc_wall_gcp_interp
    ON cc_wall_gcp (code_version, procedure_variant, zone, sign, eff_wind_area_sf);


-- ============================================================================
-- 8. OPEN BUILDING Cn COEFFICIENTS
-- ============================================================================
-- Source: ASCE 7 Figures 27.3-4 through 27.3-7
-- ============================================================================

CREATE TABLE open_bldg_cn (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    roof_type_id    SMALLINT     NOT NULL REFERENCES roof_types(roof_type_id),
    wind_flow       VARCHAR(12)  NOT NULL CHECK (wind_flow IN ('clear','obstructed')),
    wind_direction  VARCHAR(20)  NOT NULL,   -- 'normal_0deg','normal_180deg','parallel_90deg'
    load_case       CHAR(1)      NOT NULL CHECK (load_case IN ('A','B')),
    distance_zone   VARCHAR(15),             -- NULL for normal-to-ridge; 'le_h','h_to_2h','gt_2h' for parallel
    roof_half       VARCHAR(10),             -- 'windward','leeward' for normal-to-ridge; NULL for parallel
    angle_deg       NUMERIC(5,2) NOT NULL,   -- roof angle breakpoint
    cn              NUMERIC(6,3) NOT NULL,

    UNIQUE (code_version, roof_type_id, wind_flow, wind_direction, load_case, distance_zone, roof_half, angle_deg)
);

-- Sample: Monoslope, Clear flow (from Open Bldg data)
INSERT INTO open_bldg_cn
    (code_version, roof_type_id, wind_flow, wind_direction, load_case, distance_zone, roof_half, angle_deg, cn)
VALUES
    ('7-22', 1, 'clear', 'normal_0deg', 'A', NULL, 'windward',  0.0,  1.200),
    ('7-22', 1, 'clear', 'normal_0deg', 'A', NULL, 'leeward',   0.0,  0.300),
    ('7-22', 1, 'clear', 'normal_0deg', 'B', NULL, 'windward',  0.0, -1.100),
    ('7-22', 1, 'clear', 'normal_0deg', 'B', NULL, 'leeward',   0.0, -0.100),
    ('7-22', 1, 'clear', 'parallel_90deg', 'A', 'le_h',    NULL, 0.0, -0.800),
    ('7-22', 1, 'clear', 'parallel_90deg', 'A', 'h_to_2h', NULL, 0.0, -0.600),
    ('7-22', 1, 'clear', 'parallel_90deg', 'A', 'gt_2h',   NULL, 0.0, -0.300),
    ('7-22', 1, 'clear', 'parallel_90deg', 'B', 'le_h',    NULL, 0.0,  0.800),
    ('7-22', 1, 'clear', 'parallel_90deg', 'B', 'h_to_2h', NULL, 0.0,  0.500),
    ('7-22', 1, 'clear', 'parallel_90deg', 'B', 'gt_2h',   NULL, 0.0,  0.300);


-- ============================================================================
-- 9. OTHER STRUCTURES — FORCE COEFFICIENTS (Cf)
-- ============================================================================

-- 9A. Solid Signs (Cf by B/s and s/h ratios)
CREATE TABLE cf_solid_signs (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    case_id         CHAR(1)      NOT NULL CHECK (case_id IN ('A','B','C')),
    bs_ratio        NUMERIC(6,3),            -- B/s breakpoint
    sh_ratio        NUMERIC(6,3),            -- s/h breakpoint
    distance_zone   VARCHAR(10),             -- For Case C: '0_to_s','s_to_2s','2s_to_3s'
    cf              NUMERIC(6,4) NOT NULL,

    UNIQUE (code_version, case_id, bs_ratio, sh_ratio, distance_zone)
);

-- 9B. Chimneys, Tanks (Cf by h/D and cross-section)
CREATE TABLE cf_chimneys (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    cross_section   VARCHAR(20)  NOT NULL,   -- 'square_normal','square_diagonal','hexagonal','round'
    hd_ratio        NUMERIC(6,2) NOT NULL,   -- h/D breakpoint
    cf              NUMERIC(6,4) NOT NULL,

    UNIQUE (code_version, cross_section, hd_ratio)
);

-- 9C. Trussed Towers (Cf by solidity ratio ε)
CREATE TABLE cf_trussed_towers (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    tower_section   VARCHAR(20)  NOT NULL,   -- 'square','triangle'
    member_shape    VARCHAR(10)  NOT NULL,   -- 'flat','round'
    solidity_ratio  NUMERIC(6,4) NOT NULL,   -- ε breakpoint
    cf              NUMERIC(6,4) NOT NULL,

    UNIQUE (code_version, tower_section, member_shape, solidity_ratio)
);


-- ============================================================================
-- 10. PARAPET GCpn VALUES
-- ============================================================================

CREATE TABLE parapet_gcpn (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)   NOT NULL REFERENCES asce7_editions(code_version),
    location        VARCHAR(10)  NOT NULL CHECK (location IN ('windward','leeward')),
    gcpn            NUMERIC(5,2) NOT NULL,

    UNIQUE (code_version, location)
);

INSERT INTO parapet_gcpn (code_version, location, gcpn)
SELECT e.code_version,
       t.location,
       CASE WHEN e.wind_code_index = 2  -- ASCE 7-02 uses different values
            THEN t.gcpn_02
            ELSE t.gcpn_std
       END
FROM asce7_editions e
CROSS JOIN (VALUES
    ('windward',  1.50,  1.80),
    ('leeward',  -1.00, -1.10)
) AS t(location, gcpn_std, gcpn_02);


-- ============================================================================
-- 11. APPROXIMATE NATURAL FREQUENCY FORMULAS (metadata)
-- ============================================================================

CREATE TABLE approx_natural_frequency (
    id              SERIAL PRIMARY KEY,
    system_type     VARCHAR(80)  NOT NULL,
    coefficient     NUMERIC(6,2) NOT NULL,   -- numerator constant
    exponent        NUMERIC(6,3) NOT NULL,   -- height exponent (negative)
    notes           TEXT
);

INSERT INTO approx_natural_frequency (system_type, coefficient, exponent) VALUES
    ('Steel moment-resisting frame',    22.2, 0.800),
    ('Concrete moment-resisting frame', 43.5, 0.900),
    ('Other lateral-force-resisting',   75.0, 1.000);


-- ============================================================================
-- 12. ROOFTOP EQUIPMENT GCr VALUES
-- ============================================================================

CREATE TABLE rooftop_equipment_gcr (
    id              SERIAL PRIMARY KEY,
    code_version    VARCHAR(8)  NOT NULL REFERENCES asce7_editions(code_version),
    load_direction  VARCHAR(12) NOT NULL CHECK (load_direction IN ('vertical','horizontal')),
    gcr             NUMERIC(4,2) NOT NULL,

    UNIQUE (code_version, load_direction)
);

INSERT INTO rooftop_equipment_gcr (code_version, load_direction, gcr)
SELECT e.code_version, t.*
FROM asce7_editions e
CROSS JOIN (VALUES
    ('vertical',   1.50),
    ('horizontal', 1.90)
) AS t(load_direction, gcr);


-- ============================================================================
-- ============================================================================
-- INTERPOLATION STRATEGY
-- ============================================================================
-- ============================================================================
--
-- CORE PATTERN: "Bounding Pair" Queries
--
-- Most ASCE 7 lookups require interpolation between two tabulated breakpoints.
-- The schema is designed so each coefficient row is keyed by its breakpoint
-- value on the interpolation axis (e.g., lb_ratio, angle_deg, eff_wind_area_sf).
--
-- The API fetches exactly two rows per interpolation:
--   1. The greatest breakpoint ≤ the input value  (lower bound)
--   2. The least breakpoint ≥ the input value      (upper bound)
--
-- If both rows return the same breakpoint, the input is an exact match and
-- no interpolation is needed.
--
-- LINEAR INTERPOLATION in the application layer:
--   result = cp_lo + (cp_hi - cp_lo) * (input - bp_lo) / (bp_hi - bp_lo)
--
-- LOG-LINEAR INTERPOLATION (for C&C effective wind area):
--   result = gcp_lo + (gcp_hi - gcp_lo) * (log10(area) - log10(a_lo)) / (log10(a_hi) - log10(a_lo))
--
-- ============================================================================


-- ============================================================================
-- EXAMPLE 1: Leeward Wall Cp for arbitrary L/B ratio
-- ============================================================================
-- Given: code_version = '7-22', L/B = 1.5
-- We need the two bounding rows from mwfrs_wall_cp to interpolate.
-- ============================================================================

-- This CTE-based query returns both bounding rows in a single round trip:

/*
  WITH bounds AS (
      SELECT
          cp,
          lb_ratio,
          -- Flag which bound this row represents
          CASE
              WHEN lb_ratio <= 1.5 THEN 'lower'
              WHEN lb_ratio >= 1.5 THEN 'upper'
          END AS bound_type
      FROM mwfrs_wall_cp
      WHERE code_version = '7-22'
        AND surface = 'leeward'
        AND lb_ratio IS NOT NULL
        AND (lb_ratio <= 1.5 OR lb_ratio >= 1.5)
  ),
  lower_bound AS (
      SELECT cp, lb_ratio
      FROM bounds
      WHERE bound_type = 'lower'
      ORDER BY lb_ratio DESC
      LIMIT 1
  ),
  upper_bound AS (
      SELECT cp, lb_ratio
      FROM bounds
      WHERE bound_type = 'upper'
      ORDER BY lb_ratio ASC
      LIMIT 1
  )
  SELECT
      lo.lb_ratio  AS lb_lo,
      lo.cp        AS cp_lo,
      hi.lb_ratio  AS lb_hi,
      hi.cp        AS cp_hi
  FROM lower_bound lo
  CROSS JOIN upper_bound hi;
*/

-- EXPECTED RESULT for L/B = 1.5:
--   lb_lo = 1.000,  cp_lo = -0.5000
--   lb_hi = 2.000,  cp_hi = -0.3000
--
-- APPLICATION LAYER computes:
--   Cp = -0.5 + (-0.3 - (-0.5)) * (1.5 - 1.0) / (2.0 - 1.0)
--      = -0.5 + 0.2 * 0.5
--      = -0.4


-- ============================================================================
-- EXAMPLE 2: C&C Roof GCp for arbitrary effective wind area (log-linear)
-- ============================================================================
-- Given: code_version = '7-22', flat roof (0-7 deg), Zone 2, negative,
--        effective wind area = 35 sf
-- ============================================================================

/*
  WITH bounds AS (
      SELECT
          gcp,
          eff_wind_area_sf,
          LOG(eff_wind_area_sf) AS log_area,   -- PostgreSQL LOG() is log10
          CASE
              WHEN eff_wind_area_sf <= 35 THEN 'lower'
              WHEN eff_wind_area_sf >= 35 THEN 'upper'
          END AS bound_type
      FROM cc_roof_gcp
      WHERE code_version      = '7-22'
        AND procedure_variant = 'h_le_60'
        AND angle_range       = '0_to_7'
        AND zone              = '2'
        AND sign              = 'negative'
        AND (eff_wind_area_sf <= 35 OR eff_wind_area_sf >= 35)
  ),
  lower_bound AS (
      SELECT gcp, eff_wind_area_sf, log_area
      FROM bounds WHERE bound_type = 'lower'
      ORDER BY eff_wind_area_sf DESC LIMIT 1
  ),
  upper_bound AS (
      SELECT gcp, eff_wind_area_sf, log_area
      FROM bounds WHERE bound_type = 'upper'
      ORDER BY eff_wind_area_sf ASC LIMIT 1
  )
  SELECT
      lo.eff_wind_area_sf AS area_lo,
      lo.gcp              AS gcp_lo,
      lo.log_area         AS log_lo,
      hi.eff_wind_area_sf AS area_hi,
      hi.gcp              AS gcp_hi,
      hi.log_area         AS log_hi,
      -- Compute interpolated value right in SQL if desired:
      lo.gcp + (hi.gcp - lo.gcp)
              * (LOG(35.0) - lo.log_area)
              / NULLIF(hi.log_area - lo.log_area, 0) AS gcp_interpolated
  FROM lower_bound lo
  CROSS JOIN upper_bound hi;
*/

-- EXPECTED RESULT for area = 35 sf:
--   area_lo = 20,   gcp_lo = -1.5893,  log_lo = 1.3010
--   area_hi = 50,   gcp_hi = -1.3107,  log_hi = 1.6990
--   log(35) = 1.5441
--   gcp_interpolated = -1.5893 + (-1.3107 - (-1.5893)) * (1.5441 - 1.3010) / (1.6990 - 1.3010)
--                     ≈ -1.5893 + 0.2786 * 0.6113
--                     ≈ -1.419


-- ============================================================================
-- EXAMPLE 3: MWFRS Low-Rise GCpf for arbitrary roof angle
-- ============================================================================
-- Given: code_version = '7-22', Case A, Zone 2, θ = 12°
-- ============================================================================

/*
  WITH bounds AS (
      SELECT
          gcpf,
          angle_deg,
          CASE
              WHEN angle_deg <= 12 THEN 'lower'
              WHEN angle_deg >= 12 THEN 'upper'
          END AS bound_type
      FROM mwfrs_lowrise_gcpf
      WHERE code_version = '7-22'
        AND load_case    = 'A'
        AND zone         = '2'
        AND (angle_deg <= 12 OR angle_deg >= 12)
  ),
  lower_bound AS (
      SELECT gcpf, angle_deg FROM bounds
      WHERE bound_type = 'lower' ORDER BY angle_deg DESC LIMIT 1
  ),
  upper_bound AS (
      SELECT gcpf, angle_deg FROM bounds
      WHERE bound_type = 'upper' ORDER BY angle_deg ASC LIMIT 1
  )
  SELECT
      lo.angle_deg AS angle_lo, lo.gcpf AS gcpf_lo,
      hi.angle_deg AS angle_hi, hi.gcpf AS gcpf_hi
  FROM lower_bound lo CROSS JOIN upper_bound hi;
*/

-- EXPECTED RESULT for θ = 12°:
--   angle_lo = 0.0,  gcpf_lo = -0.69
--   angle_hi = 20.0, gcpf_hi = -0.69
--   GCpf = -0.69 (no change in this range — values are constant 0–20°)


-- ============================================================================
-- COMPACT UTILITY: Generic bounding-pair function
-- ============================================================================
-- For production use, wrap the bounding-pair pattern in a reusable function.
-- ============================================================================

CREATE OR REPLACE FUNCTION get_bounding_pair(
    p_table_name   TEXT,
    p_axis_column  TEXT,
    p_value_column TEXT,
    p_input_value  NUMERIC,
    p_where_clause TEXT  -- e.g. "code_version = '7-22' AND surface = 'leeward'"
)
RETURNS TABLE (
    bound_lo   NUMERIC,
    value_lo   NUMERIC,
    bound_hi   NUMERIC,
    value_hi   NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        'WITH lo AS (
             SELECT %I AS bp, %I AS val FROM %I
             WHERE %s AND %I <= $1
             ORDER BY %I DESC LIMIT 1
         ),
         hi AS (
             SELECT %I AS bp, %I AS val FROM %I
             WHERE %s AND %I >= $1
             ORDER BY %I ASC LIMIT 1
         )
         SELECT lo.bp, lo.val, hi.bp, hi.val
         FROM lo CROSS JOIN hi',
        p_axis_column, p_value_column, p_table_name,
        p_where_clause, p_axis_column, p_axis_column,
        p_axis_column, p_value_column, p_table_name,
        p_where_clause, p_axis_column, p_axis_column
    ) USING p_input_value;
END;
$$;

-- Usage example:
-- SELECT * FROM get_bounding_pair(
--     'mwfrs_wall_cp', 'lb_ratio', 'cp', 1.5,
--     $$code_version = '7-22' AND surface = 'leeward' AND lb_ratio IS NOT NULL$$
-- );
