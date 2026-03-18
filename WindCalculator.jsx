import { useState, useCallback, useMemo, useEffect } from "react";

/* ── constants ── */
// Roof C&C areas per ASCE 7 Table 30.3 (h≤60)
const CC_AREAS_ROOF = [10, 100, 500, 1000];
const CC_AREAS_WALL = [10, 100, 200, 500];

const ZMETA = {
  "1":  { label: "Zone 1",  desc: "Roof Field" },
  "1p": { label: "Zone 1'", desc: "Roof Field (interior)" },
  "2":  { label: "Zone 2",  desc: "Roof Edge" },
  "3":  { label: "Zone 3",  desc: "Roof Corner" },
  "oh1": { label: "Overhang Zone 1&1'", desc: "Overhang - Field" },
  "oh2": { label: "Overhang Zone 2",    desc: "Overhang - Edge" },
  "oh3": { label: "Overhang Zone 3",    desc: "Overhang - Corner" },
  "4":  { label: "Zone 4",  desc: "Wall Field" },
  "5":  { label: "Zone 5",  desc: "Wall Corner" },
};

const CODE_VERS = [
  { value: "7-22", label: "ASCE 7-22" },
  { value: "7-16", label: "ASCE 7-16" },
  { value: "7-10", label: "ASCE 7-10" },
  { value: "7-05", label: "ASCE 7-05" },
];
/* Topographic feature types for Kzt (ASCE 7 §26.8) */
const TOPO_TYPES = [
  { value: "flat",       label: "Flat (Kzt = 1.0)" },
  { value: "2d_ridge",   label: "2D Ridge" },
  { value: "2d_escarp",  label: "2D Escarpment" },
  { value: "3d_hill",    label: "3D Axisym. Hill" },
];

/* Gust effect factor modes (ASCE 7 §26.11) */
const GUST_MODES = [
  { value: "rigid_fixed", label: "Rigid — Fixed G = 0.85" },
  { value: "rigid_calc",  label: "Rigid — Calculated Gf" },
  { value: "flexible",    label: "Flexible / Resonant Gf" },
];

const EXPOSURES = [
  { value: "B", label: "Exp B" },
  { value: "C", label: "Exp C" },
  { value: "D", label: "Exp D" },
];
const ENCLOSURES = [
  { value: "enclosed", label: "Enclosed" },
  { value: "partially_enclosed", label: "Part. Enclosed" },
  { value: "open", label: "Open" },
];
const ROOFS = [
  { value: "gable",     label: "Gable",     mn: 0, mx: 45 },
  { value: "hip",       label: "Hip",       mn: 7, mx: 27 },
  { value: "monoslope", label: "Monoslope", mn: 0, mx: 30 },
];
const TABS = [
  { id: "qz",  label: "qz Profile" },
  { id: "dir", label: "MWFRS Dir." },
  { id: "lr",  label: "MWFRS LR" },
  { id: "cc",  label: "C&C" },
];

/* ── helpers ── */
const r2 = (v) => Math.round(v * 100) / 100;
const r4 = (v) => Math.round(v * 1e4) / 1e4;
const r6 = (v) => Math.round(v * 1e6) / 1e6;
const gcpiOf = (enc) => ({ enclosed: 0.18, partially_enclosed: 0.55, open: 0, partially_open: 0.18 }[enc] || 0.18);
const keOf  = (cv, el) => (cv >= "7-16" ? Math.exp(-0.0000362 * el) : 1);

function tcOf(cv, exp) {
  const db = {
    B: cv === "7-22" ? { a: 7.5, zg: 2460, zm: 30 } : { a: 7, zg: 1200, zm: 30 },
    C: { a: 9.5, zg: 900, zm: 15 },
    D: { a: 11.5, zg: 700, zm: 7 },
  };
  return db[exp] || db.C;
}

function defZ(h) {
  const pts = [15,20,25,30,40,50,60,70,80,90,100,120,140,160,200,300].filter((z) => z <= h);
  if (!pts.length || pts[pts.length - 1] < h) pts.push(h);
  return pts;
}

function compQz(V, exp, z, kd, ke, cv, kzt = 1.0) {
  const tc = tcOf(cv, exp);
  const zE = Math.max(z, tc.zm);
  const kz  = 2.01 * Math.pow(zE / tc.zg, 2 / tc.a);
  const qz  = 0.00256 * ke * kz * kzt * kd * V * V;
  return { z, zE, kz: r6(kz), qz: r4(qz), alpha: tc.a, zg: tc.zg, zm: tc.zm };
}

function cpLW(ratio) {
  if (ratio <= 1) return -0.5;
  if (ratio < 2)  return -0.5 + (ratio - 1) * 0.2;
  if (ratio < 4)  return -0.3 + (ratio - 2) * 0.05;
  return -0.2;
}

function logInterp(x, a0, a1, y0, y1) {
  if (a0 === a1) return y0;
  const t = (Math.log10(x) - Math.log10(a0)) / (Math.log10(a1) - Math.log10(a0));
  return y0 + (y1 - y0) * Math.max(0, Math.min(1, t));
}

const minPsf = (v) => (Math.abs(v) < 16 ? Math.sign(v || 1) * 16 : v);

/* ── Topographic Factor Kzt (ASCE 7-22 §26.8, Table 26.8-1) ──────────
   Inputs from the spreadsheet's Kzt section:
     topoType : "flat" | "2d_ridge" | "2d_escarp" | "3d_hill"
     H        : Hill / escarpment height (ft)
     Lh       : Half-length of hill / escarpment (ft) upwind of crest
     x        : Distance from crest to site (ft), upwind = negative
     z        : Height above ground (ft)
     upwind   : true = upwind side, false = downwind
   Returns { kzt, k1, k2, k3, hLh, xLh, zLh }
─────────────────────────────────────────────────────────────────── */
function calcKzt(topoType, H, Lh, x, z, upwind) {
  if (topoType === "flat" || !H || !Lh) return { kzt: 1.0, k1: 0, k2: 1, k3: 1, hLh: 0, xLh: 0, zLh: 0, note: "Flat — Kzt = 1.0" };

  // H/Lh ratio (clamped to 0.5 per ASCE 7 §26.8.2 note)
  const hLh_raw = H / Lh;
  const hLh = Math.min(hLh_raw, 0.5);          // per ASCE 7 §26.8.2

  // Modified Lh: if H/Lh > 0.5, use Lh_mod = 2H
  const LhMod = hLh_raw > 0.5 ? 2 * H : Lh;

  // K1 — Table 26.8-1 (linear interp on H/Lh for each feature)
  // Values at H/Lh = 0.2, 0.3, 0.4, 0.5
  const K1_table = {
    "2d_ridge":  { gamma: 1.30 },
    "2d_escarp": { gamma: 0.75 },
    "3d_hill":   { gamma: 0.95 },
  };
  const gamma = K1_table[topoType]?.gamma ?? 0.95;
  const k1 = r4(gamma * hLh);

  // K2 — rate of decay with horizontal distance from crest
  const mu = {
    "2d_ridge":  { up: 1.5, dn: 1.5 },
    "2d_escarp": { up: 2.5, dn: 1.5 },
    "3d_hill":   { up: 1.5, dn: 1.5 },
  }[topoType] ?? { up: 1.5, dn: 1.5 };

  const absX = Math.abs(x);
  const xLhMod = absX / LhMod;
  const muVal = upwind ? mu.up : mu.dn;
  const k2 = r4(Math.max(0, 1 - xLhMod / muVal));

  // K3 — rate of decay with height above ground
  const nu = { "2d_ridge": 3, "2d_escarp": 2.5, "3d_hill": 4 }[topoType] ?? 3;
  const zLh = z / LhMod;
  const k3 = r4(Math.exp(-nu * zLh));

  const kzt = r4(Math.pow(1 + k1 * k2 * k3, 2));
  return { kzt, k1, k2, k3, hLh: r4(hLh), xLh: r4(xLhMod), zLh: r4(zLh), LhMod: r2(LhMod) };
}

/* ── Gust Effect Factor G (ASCE 7-22 §26.11) ────────────────────────
   mode: "rigid_fixed" → G = 0.85
         "rigid_calc"  → calculated G for rigid buildings
         "flexible"    → Gf for flexible / resonant buildings
   Inputs: exposure, h_ft (mean roof height), n1 (nat. freq Hz),
           beta (damping ratio), V_mph, code_version
─────────────────────────────────────────────────────────────────── */
function calcG(mode, exposure, h_ft, n1, beta, V_mph) {
  if (mode === "rigid_fixed") return { G: 0.85, mode, note: "Fixed G = 0.85 per §26.11.1" };

  // Terrain constants (Table 26.11-1)
  const tc = {
    B: { Iz_ref_z: 0.45, Lz_c: 320, Lz_eps: 1/3, bg: 0.84, alpha_bar: 1/7, b_bar: 0.84, cg: 0.45, lz_c: 0.30, eps_bar: 1/3, zmin: 30 },
    C: { Iz_ref_z: 0.65, Lz_c: 500, Lz_eps: 1/5, bg: 0.93, alpha_bar: 1/9.5, b_bar: 1.0, cg: 0.65, lz_c: 0.20, eps_bar: 1/5, zmin: 15 },
    D: { Iz_ref_z: 0.80, Lz_c: 650, Lz_eps: 1/8, bg: 0.95, alpha_bar: 1/11.5, b_bar: 1.07, cg: 0.80, lz_c: 0.15, eps_bar: 1/8, zmin: 7 },
  }[exposure] || { Iz_ref_z: 0.65, Lz_c: 500, Lz_eps: 1/5, bg: 0.93, alpha_bar: 1/9.5, b_bar: 1.0, cg: 0.65, lz_c: 0.20, eps_bar: 1/5, zmin: 15 };

  const z_bar = Math.max(0.6 * h_ft, tc.zmin);  // §26.11.1
  const Iz    = tc.cg * Math.pow(33 / z_bar, tc.eps_bar);  // turbulence intensity §26.11.1
  const Lz    = tc.Lz_c * Math.pow(z_bar / 33, tc.Lz_eps); // integral length scale

  const Q_sq  = 1 / (1 + 0.63 * Math.pow((3 + h_ft) / Lz, 0.63)); // background response
  const Q     = Math.sqrt(Q_sq);

  const gQ = 3.4, gv = 3.4;

  if (mode === "rigid_calc") {
    const G = r4(0.925 * (1 + 1.7 * Iz * gQ * Q) / (1 + 1.7 * gv * Iz));
    return { G, mode, Iz: r4(Iz), Lz: r2(Lz), Q: r4(Q), z_bar: r2(z_bar), note: "Rigid G calculated §26.11.1" };
  }

  // Flexible / resonant Gf
  const V_bar_z = tc.b_bar * Math.pow(z_bar / 33, tc.alpha_bar) * V_mph;  // mean hourly speed
  const N1 = n1 * Lz / V_bar_z;  // reduced frequency

  // Rn, Rh, RB, RL (resonant response factors)
  const Rn = 7.47 * N1 / Math.pow(1 + 10.3 * N1, 5/3);
  const fnR = (nu) => nu <= 0 ? 1 : (1/(2*nu) - 1/(2*nu*nu)*(1 - Math.exp(-2*nu)));
  const eta_h = 4.6 * n1 * h_ft / V_bar_z;
  const eta_B = 4.6 * n1 * 3 / V_bar_z;   // using B = 3 placeholder; caller should pass B
  const eta_L = 15.4 * n1 * h_ft / V_bar_z;
  const Rh = fnR(eta_h), RB = fnR(eta_B), RL = fnR(eta_L);
  const R_sq = (1 / beta) * Rn * Rh * RB * (0.53 + 0.47 * RL);
  const R = Math.sqrt(R_sq);

  const gR = Math.sqrt(2 * Math.log(600 * n1)) + 0.5772 / Math.sqrt(2 * Math.log(600 * n1));
  const Gf = r4(0.925 * (1 + 1.7 * Iz * Math.sqrt(gQ*gQ*Q*Q + gR*gR*R*R)) / (1 + 1.7 * gv * Iz));

  return { G: Gf, mode, Iz: r4(Iz), Lz: r2(Lz), Q: r4(Q), R: r4(R), gR: r4(gR), z_bar: r2(z_bar), note: "Flexible Gf §26.11.2" };
}

/* ────────────────────────────────────────────────────────────
   C&C GCp tables  (ASCE 7-22 Fig 30.3-2A)
   Format: [[area_sf, GCp], ...]  — raw GCp values from the figure.
   Log-linear interpolation between breakpoints.
   GCpi is added externally. No extra reduction multiplier —
   the ASCE 7-22 figure values are already the final design values.

   Verified against spreadsheet (qh=26.7, GCpi=±0.18):
     Zone 1  neg: -1.88/-1.47/-1.18/-1.18  @ 10/100/500/1000 sf ✓
     Zone 1' neg: -1.08/-1.08/-0.73/-0.58  @ 10/100/500/1000 sf ✓
     Zone 2  neg: -2.48/-1.95/-1.58/-1.58  @ 10/100/500/1000 sf ✓
     Pos 1&1':     0.48/ 0.38/ 0.38/ 0.38  @ 10/100/500/1000 sf ✓
     Pos 2&3:      1.08/ 0.92/ 0.81/ 0.81  @ 10/100/500/1000 sf ✓
     OH 1&1' neg: -1.70/-1.60/-1.00/-1.00  @ 10/100/500/1000 sf ✓
     OH 2&3  neg: -2.30/-1.59/-1.10/-1.10  @ 10/100/500/1000 sf ✓
──────────────────────────────────────────────────────────── */
function gcpRoof_hle60(roofType, theta, zone, sign) {
  // Returns array of [[area_sf, GCp], ...] breakpoints (raw GCp, no GCpi).

  // ── MONOSLOPE θ ≤ 3°  (ASCE 7-22 Fig 30.3-2A) ──────────────
  // These breakpoints are the direct figure values — no extra reduction needed.
  if (roofType === "monoslope" && theta <= 3) {
    if (sign === "neg") {
      // Zone 1: log interp -1.70@10sf → -1.00@500sf, flat beyond
      if (zone === "1")  return [[10,-1.70],[500,-1.00],[1000,-1.00]];
      // Zone 1': flat -0.90 from 10→100sf, then log interp to -0.40@1000sf
      if (zone === "1p") return [[10,-0.90],[100,-0.90],[1000,-0.40]];
      // Zone 2: log interp -2.30@10sf → -1.40@500sf, flat beyond
      if (zone === "2")  return [[10,-2.30],[500,-1.40],[1000,-1.40]];
      // Zone 3 per ASCE 7-22 Fig 30.3-2A h≤60 (NOT equal to Zone 2 — "Zone3=Zone2 when parapet≥3ft"
      // only applies to the h<90 alternate procedure, not h≤60)
      if (zone === "3")  return [[10,-3.20],[100,-1.770],[500,-1.40],[1000,-1.40]];
      // Overhangs — GCpi = 0 per ASCE 7 §30.6
      // OH 1&1': two-segment — -1.70@10, -1.60@100, -1.00@500, flat beyond
      if (zone === "oh1") return [[10,-1.70],[100,-1.60],[500,-1.00],[1000,-1.00]];
      // OH 2&3: log interp -2.30@10sf → -1.10@500sf, flat beyond
      if (zone === "oh2") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
      if (zone === "oh3") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
    } else {
      // Positive — same for all roof zones; log interp 0.30@10 → 0.20@500, flat beyond
      if (zone === "1" || zone === "1p") return [[10,0.30],[100,0.20],[500,0.20],[1000,0.20]];
      // Zones 2 & 3 positive: log interp 0.90@10 → 0.63@500
      if (zone === "2"  || zone === "3") return [[10,0.90],[500,0.63],[1000,0.63]];
      // Overhangs — positive not used for design (suction governs); return near-zero
      return [[10,0.00],[1000,0.00]];
    }
  }

  // ── MONOSLOPE 3° < θ ≤ 10°  (ASCE 7-22 Fig 30.3-2A) ────────
  // Note: the ASCE figure for this angle range has its own breakpoints;
  // no additional 0.9 multiplier is applied here.
  if (roofType === "monoslope" && theta > 3 && theta <= 10) {
    if (sign === "neg") {
      if (zone === "1")  return [[10,-1.30],[500,-1.10],[1000,-1.10]];
      if (zone === "1p") return [[10,-0.90],[100,-0.90],[1000,-0.40]];
      if (zone === "2")  return [[10,-1.80],[500,-1.10],[1000,-1.10]];
      if (zone === "3")  return [[10,-1.80],[500,-1.10],[1000,-1.10]];
      if (zone === "oh1") return [[10,-1.60],[100,-1.60],[500,-1.10],[1000,-1.10]];
      if (zone === "oh2") return [[10,-1.60],[500,-1.10],[1000,-1.10]];
      if (zone === "oh3") return [[10,-1.60],[500,-1.10],[1000,-1.10]];
    } else {
      if (zone === "1" || zone === "1p") return [[10,0.30],[500,0.20],[1000,0.20]];
      if (zone === "2"  || zone === "3") return [[10,0.90],[500,0.63],[1000,0.63]];
      return [[10,0.00],[1000,0.00]];
    }
  }

  // ── GABLE / HIP θ ≤ 7°  (ASCE 7-22 Fig 30.3-2A) ────────────
  if ((roofType === "gable" || roofType === "hip") && theta <= 7) {
    if (sign === "neg") {
      // For gable ≤7°, Zone 1 uses the same breakpoints as monoslope ≤3°
      if (zone === "1")  return [[10,-1.70],[500,-1.00],[1000,-1.00]];
      if (zone === "1p") return [[10,-0.90],[100,-0.90],[1000,-0.40]];
      if (zone === "2")  return [[10,-2.30],[500,-1.40],[1000,-1.40]];
      if (zone === "3")  return [[10,-2.80],[500,-1.80],[1000,-1.80]];
      if (zone === "oh1") return [[10,-1.70],[100,-1.60],[500,-1.00],[1000,-1.00]];
      if (zone === "oh2") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
      if (zone === "oh3") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
    } else {
      if (zone === "1" || zone === "1p") return [[10,0.50],[500,0.30],[1000,0.30]];
      if (zone === "2"  || zone === "3") return [[10,0.90],[500,0.63],[1000,0.63]];
      return [[10,0.00],[1000,0.00]];
    }
  }

  // ── GABLE 7° < θ ≤ 27°  (ASCE 7-22 Fig 30.3-2A) ────────────
  if (roofType === "gable" && theta > 7 && theta <= 27) {
    if (sign === "neg") {
      if (zone === "1")  return [[10,-1.70],[500,-1.00],[1000,-1.00]];
      if (zone === "1p") return [[10,-0.90],[100,-0.90],[1000,-0.40]];
      if (zone === "2")  return [[10,-2.30],[500,-1.40],[1000,-1.40]];
      if (zone === "3")  return [[10,-3.20],[500,-2.30],[1000,-2.30]];
      if (zone === "oh1") return [[10,-1.70],[100,-1.60],[500,-1.00],[1000,-1.00]];
      if (zone === "oh2") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
      if (zone === "oh3") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
    } else {
      if (zone === "1" || zone === "1p") return [[10,0.30],[500,0.20],[1000,0.20]];
      if (zone === "2"  || zone === "3") return [[10,0.90],[500,0.63],[1000,0.63]];
      return [[10,0.00],[1000,0.00]];
    }
  }

  // ── GABLE 27° < θ ≤ 45°  ────────────────────────────────────
  if (roofType === "gable" && theta > 27 && theta <= 45) {
    if (sign === "neg") {
      if (zone === "1")  return [[10,-1.10],[500,-0.80],[1000,-0.80]];
      if (zone === "1p") return [[10,-0.90],[100,-0.90],[1000,-0.40]];
      if (zone === "2")  return [[10,-1.60],[500,-1.20],[1000,-1.20]];
      if (zone === "3")  return [[10,-2.60],[500,-2.00],[1000,-2.00]];
      if (zone === "oh1") return [[10,-1.70],[100,-1.60],[500,-1.00],[1000,-1.00]];
      if (zone === "oh2") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
      if (zone === "oh3") return [[10,-2.30],[500,-1.10],[1000,-1.10]];
    } else {
      if (zone === "1" || zone === "1p") return [[10,0.30],[500,0.20],[1000,0.20]];
      if (zone === "2"  || zone === "3") return [[10,1.50],[500,0.90],[1000,0.90]];
      return [[10,0.00],[1000,0.00]];
    }
  }

  // ── Fallback ─────────────────────────────────────────────────
  if (sign === "neg") {
    if (zone === "1" || zone === "1p") return [[10,-1.70],[500,-1.00],[1000,-1.00]];
    if (zone === "2") return [[10,-2.30],[500,-1.40],[1000,-1.40]];
    if (zone === "3") return [[10,-2.30],[500,-1.40],[1000,-1.40]];
    if (zone.startsWith("oh")) return [[10,-1.70],[500,-1.10],[1000,-1.10]];
  }
  return [[10,0.30],[500,0.20],[1000,0.20]];
}

function gcpWall_hle60(zone, sign) {
  // ASCE 7 Fig 30.4-1 walls h≤60
  if (sign === "neg") {
    if (zone === "4") return [[10,-1.17],[100,-1.01],[200,-0.96],[500,-0.9]];
    if (zone === "5") return [[10,-1.44],[100,-1.12],[200,-1.03],[500,-0.9]];
  } else {
    if (zone === "4" || zone === "5") return [[10,1.08],[100,0.921],[200,0.873],[500,0.81]];
  }
  return [[10,0.9],[500,0.6]];
}

function interpGCp(area, table) {
  // table: [[area, GCp], ...]
  if (area <= table[0][0]) return table[0][1];
  if (area >= table[table.length - 1][0]) return table[table.length - 1][1];
  for (let i = 0; i < table.length - 1; i++) {
    const [a0, g0] = table[i];
    const [a1, g1] = table[i + 1];
    if (area >= a0 && area <= a1) return logInterp(area, a0, a1, g0, g1);
  }
  return table[table.length - 1][1];
}

/* ── mock API ── */
async function apiQz(P) {
  const { project: p, geometry: g, kd, kztInputs } = P;
  const ke  = keOf(p.code_version, 0);
  // Compute Kzt at each height
  const rows = defZ(g.h_ft).map((z) => {
    const kztR = calcKzt(kztInputs.topo_type, kztInputs.H_ft, kztInputs.Lh_ft,
                         kztInputs.x_ft, z, kztInputs.upwind);
    const c = compQz(p.V_mph, p.exposure, z, kd, ke, p.code_version, kztR.kzt);
    return { z_ft: z, kz: c.kz, kzt: kztR.kzt, qz_psf: c.qz, alpha: c.alpha, zg_ft: c.zg, ke: r6(ke), kd };
  });
  // Kzt at mean roof height (for header chip)
  const kztH = calcKzt(kztInputs.topo_type, kztInputs.H_ft, kztInputs.Lh_ft,
                       kztInputs.x_ft, g.h_ft, kztInputs.upwind);
  return { code_version: p.code_version, V_mph: p.V_mph, exposure: p.exposure, pressures: rows, kztH: kztH.kzt };
}

async function apiDir(P) {
  const { project: p, geometry: g, kd, kztInputs, gustInputs } = P;
  const ke = keOf(p.code_version, 0);
  const kztH = calcKzt(kztInputs.topo_type, kztInputs.H_ft, kztInputs.Lh_ft,
                       kztInputs.x_ft, g.h_ft, kztInputs.upwind).kzt;
  const gRes = calcG(gustInputs.mode, p.exposure, g.h_ft, gustInputs.n1, gustInputs.beta, p.V_mph);
  const G    = gRes.G;
  const gcpi = gcpiOf(p.enclosure);
  const qhC  = compQz(p.V_mph, p.exposure, g.h_ft, kd, ke, p.code_version, kztH);
  const qh   = qhC.qz;

  const bl = g.B_ft / g.L_ft;
  const lb = g.L_ft / g.B_ft;
  const hb = g.h_ft / g.B_ft;
  const hl = g.h_ft / g.L_ft;

  const cLW_normal   = r4(cpLW(bl));
  const cLW_parallel = r4(cpLW(lb));

  const interpRoof = (ratio, cp05, cp10) => {
    if (ratio <= 0.5) return cp05;
    if (ratio >= 1.0) return cp10;
    return cp05 + (cp10 - cp05) * (ratio - 0.5) / 0.5;
  };
  const roofNormal = [
    { zone: "0 to h/2",   cp: interpRoof(hb, -0.9, -1.04) },
    { zone: "h/2 to h",   cp: interpRoof(hb, -0.9, -0.7) },
    { zone: "h to 2h",    cp: interpRoof(hb, -0.5, -0.7) },
    { zone: "> 2h",       cp: interpRoof(hb, -0.3, -0.7) },
    { zone: "WW pos/min", cp: -0.18 },
  ];
  const roofParallel = [
    { zone: "0 to h/2",   cp: interpRoof(hl, -0.9, -1.04) },
    { zone: "h/2 to h",   cp: interpRoof(hl, -0.9, -0.7) },
    { zone: "h to 2h",    cp: interpRoof(hl, -0.5, -0.7) },
    { zone: "> 2h",       cp: interpRoof(hl, -0.3, -0.7) },
    { zone: "WW pos/min", cp: -0.18 },
  ];

  // LW pressures (constant at all heights) for both directions
  const lwPrs = {
    normal:   { pN: r2(qh*G*cLW_normal   - qh*gcpi), pP: r2(qh*G*cLW_normal   + qh*gcpi) },
    parallel: { pN: r2(qh*G*cLW_parallel  - qh*gcpi), pP: r2(qh*G*cLW_parallel  + qh*gcpi) },
  };

  // Merge standard + user-added heights
  const allHeights = [...new Set([...defZ(g.h_ft), ...(g.extraHeights||[])])].sort((a,b)=>a-b);

  const profile = allHeights.map((z) => {
    const kztZ = calcKzt(kztInputs.topo_type, kztInputs.H_ft, kztInputs.Lh_ft,
                         kztInputs.x_ft, z, kztInputs.upwind).kzt;
    const c      = compQz(p.V_mph, p.exposure, z, kd, ke, p.code_version, kztZ);
    const qzGCp  = c.qz * G * 0.8;
    const pN_ww  = r2(qzGCp - qh * gcpi);
    const pP_ww  = r2(qzGCp + qh * gcpi);
    return {
      z_ft: z, kz: c.kz, kzt: kztZ,
      pN: pN_ww, pP: pP_ww,
      combN_normal:   r2(pN_ww - lwPrs.normal.pN),
      combP_normal:   r2(pP_ww - lwPrs.normal.pP),
      combN_parallel: r2(pN_ww - lwPrs.parallel.pN),
      combP_parallel: r2(pP_ww - lwPrs.parallel.pP),
    };
  });

  const gcpn = p.code_version === "7-02" ? [1.8, -1.1] : [1.5, -1.0];
  // qp for MWFRS parapet per §27.3.4: velocity pressure at TOP of parapet, not at mean roof h
  const zParapet = g.h_ft + (g.parapet_height_ft || 0);
  const kztPar   = calcKzt(kztInputs.topo_type, kztInputs.H_ft, kztInputs.Lh_ft, kztInputs.x_ft, zParapet, kztInputs.upwind).kzt;
  const qp_par   = compQz(p.V_mph, p.exposure, zParapet, kd, ke, p.code_version, kztPar).qz;
  const pLW_n = qh * G * cLW_normal;
  const pSW   = qh * G * -0.7;
  const pWW   = qh * G * 0.8;
  const pR_n  = qh * G * roofNormal[0].cp;
  const pLW_p = qh * G * cLW_parallel;

  const torsion = [
    { id: "1", label: "Full Pressures",    mt: false, fW: 1,     pWW: r2(pWW),        pLW: r2(pLW_n),        pSW: r2(pSW),        pR: r2(pR_n) },
    { id: "2", label: "75% + Torsion",     mt: true,  fW: 0.75,  pWW: r2(0.75*pWW),   pLW: r2(0.75*pLW_n),   pSW: r2(0.75*pSW),   pR: r2(0.75*pR_n) },
    { id: "3", label: "Full Walls, 75% Roof", mt: true, fW: 1,   pWW: r2(pWW),        pLW: r2(pLW_n),        pSW: r2(pSW),        pR: r2(0.75*pR_n) },
    { id: "4", label: "56% All + Torsion", mt: true,  fW: 0.563, pWW: r2(0.563*pWW),  pLW: r2(0.563*pLW_n),  pSW: r2(0.563*pSW),  pR: r2(0.563*pR_n) },
  ];

  return {
    qh, G, gcpi, kd, V: p.V_mph, L: g.L_ft, B: g.B_ft, h: g.h_ft,
    cWW: 0.8, cSW: -0.7,
    cLW_n: cLW_normal, ratioLW_n: r4(bl), ratioRoof_n: r4(hb), roofNormal,
    lwP_n: r2(pLW_n - qh*gcpi), lwN_n: r2(pLW_n + qh*gcpi),
    cLW_p: cLW_parallel, ratioLW_p: r4(lb), ratioRoof_p: r4(hl), roofParallel,
    lwP_p: r2(pLW_p - qh*gcpi), lwN_p: r2(pLW_p + qh*gcpi),
    swP: r2(pSW - qh*gcpi), swN: r2(pSW + qh*gcpi),
    profile, parWW: r2(qp_par*gcpn[0]), parLW: r2(qp_par*gcpn[1]),  // qp at parapet top per §27.3.4
    oh: r2(qh * G * 0.8), torsion, G, gRes, kztH, lwPrs,  // §27.3.2 soffit = qh×G×Cp_WW
  };
}

async function apiLR(P) {
  const { project: p, geometry: g, kd } = P;
  const gcpi = gcpiOf(p.enclosure);
  if (g.h_ft > 60) return { ok:false, reason:"h > 60 ft", qh:0, gcpi, ez:0, cA:[], cB:[], pww:0, plw:0, sd:null };
  if (g.h_ft > g.B_ft) return { ok:false, reason:"h > B",   qh:0, gcpi, ez:0, cA:[], cB:[], pww:0, plw:0, sd:null };
  const ke   = keOf(p.code_version, 0);
  const kztH = calcKzt(P.kztInputs.topo_type, P.kztInputs.H_ft, P.kztInputs.Lh_ft,
                       P.kztInputs.x_ft, g.h_ft, P.kztInputs.upwind).kzt;
  const qh   = compQz(p.V_mph, p.exposure, g.h_ft, kd, ke, p.code_version, kztH).qz;
  const a    = Math.max(Math.min(0.1*Math.min(g.L_ft,g.B_ft), 0.4*g.h_ft), 3);
  const A  = { "1":0.4,"2":-0.69,"3":-0.37,"4":-0.29,"1E":0.61,"2E":-1.07,"3E":-0.53,"4E":-0.43 };
  const B  = { "1":-0.45,"2":-0.69,"3":-0.37,"4":-0.45,"5":0.4,"6":-0.29,"1E":-0.48,"2E":-1.07,"3E":-0.53,"4E":-0.48,"5E":0.61,"6E":-0.43 };
  const mk = (s) => Object.entries(s).map(([z,v]) => ({ zone:z, gcpf:v, pN:r2(qh*(v+gcpi)), pP:r2(qh*(v-gcpi)) }));
  const gcpn = p.code_version === "7-02" ? [1.8,-1.1] : [1.5,-1.0];
  // Horizontal MWFRS Simple Diaphragm (§28.4) — zones 5/6 for walls, 2/3 for roof
  const edgeA = r2(a), end2a = r2(2*a);
  const sd_tw_int = r2(qh*(B["5"]-B["6"]));
  const sd_tw_end = r2(qh*(B["5E"]-B["6E"]));
  const sd_tr_int = r2(qh*(A["2"]-A["3"]));
  const sd_tr_end = r2(qh*(A["2E"]-A["3E"]));
  const minH = 16;
  const sd = {
    a: edgeA, endZone2a: end2a,
    transverse:  { intWall:r2(Math.max(sd_tw_int,minH)), endWall:r2(Math.max(sd_tw_end,minH)), intRoof:sd_tr_int, endRoof:sd_tr_end },
    longitudinal:{ intWall:r2(Math.max(sd_tw_int,minH)), endWall:r2(Math.max(sd_tw_end,minH)) },
  };
  return { ok:true, reason:"", qh, gcpi, ez:r2(2*a), cA:mk(A), cB:mk(B), pww:r2(qh*gcpn[0]), plw:r2(qh*gcpn[1]), sd };
}
async function apiCC(P) {
  const { project: p, geometry: g, kd, kztInputs } = P;
  const ke   = keOf(p.code_version, 0);
  const kztH = calcKzt(kztInputs.topo_type, kztInputs.H_ft, kztInputs.Lh_ft,
                       kztInputs.x_ft, g.h_ft, kztInputs.upwind).kzt;
  const qh   = compQz(p.V_mph, p.exposure, g.h_ft, kd, ke, p.code_version, kztH).qz;
  const gcpi = gcpiOf(p.enclosure);
  const a    = r2(Math.max(Math.min(0.1*Math.min(g.L_ft,g.B_ft), 0.4*g.h_ft), 3));
  const roof  = g.roof_type;
  const theta = g.roof_angle_deg;
  const hle60 = g.h_ft <= 60;

  // Roof zones to compute
  const roofZones = ["1","1p","2","3","oh1","oh2","oh3"];
  // For overhangs, GCpi = 0 per ASCE 7 §30.6
  // For note: zone3 = zone2 when parapet ≥ 3ft (handled in GCp table)

  const prs = [];

  if (hle60) {
    const areas = CC_AREAS_ROOF; // [10, 100, 500, 1000]
    for (const zone of roofZones) {
      const isOverhang = zone.startsWith("oh");
      for (const ar of areas) {
        const negTable = gcpRoof_hle60(roof, theta, zone, "neg");
        const posTable = gcpRoof_hle60(roof, theta, zone, "pos");
        const gn = interpGCp(ar, negTable);
        const gp = interpGCp(ar, posTable);
        // Overhangs: GCpi = 0 (per ASCE 7 commentary)
        const gcpiEff = isOverhang ? 0 : gcpi;

        prs.push({
          zone, area: ar, gn: r4(gn), gp: r4(gp), isOverhang,
          // Suction (negative): qh * (GCp_neg - GCpi)  [more negative]
          pnN: r2(minPsf(qh * (gn - gcpiEff))),
          // Positive: qh * (GCp_pos + GCpi)
          ppP: r2(minPsf(qh * (gp + gcpiEff))),
        });
      }
    }

    // Wall zones 4 & 5
    const wallAreas = CC_AREAS_WALL;
    for (const zone of ["4","5"]) {
      for (const ar of wallAreas) {
        const negTable = gcpWall_hle60(zone, "neg");
        const posTable = gcpWall_hle60(zone, "pos");
        const gn = interpGCp(ar, negTable);
        const gp = interpGCp(ar, posTable);
        prs.push({
          zone, area: ar, gn: r4(gn), gp: r4(gp), isOverhang: false,
          pnN: r2(minPsf(qh * (gn - gcpi))),
          ppP: r2(minPsf(qh * (gp + gcpi))),
        });
      }
    }
  }

  const parAreas=[10,20,50,100,200,500];
  const PAR_GCpn={caseA:[[10,3.1948],[500,2.0262]],caseBint:[[10,-1.8876],[500,-1.3483]],caseBcor:[[10,-2.1573],[500,-1.3483]]};
  const parPrs=parAreas.map((ar)=>({area:ar,caseA:r2(qh*interpGCp(ar,PAR_GCpn.caseA)),caseBint:r2(qh*interpGCp(ar,PAR_GCpn.caseBint)),caseBcor:r2(qh*interpGCp(ar,PAR_GCpn.caseBcor))}));
  return { qh: r2(qh), gcpi, a, prs, proc: hle60 ? "h\u226460'" : "h>60'", theta, roof, parPrs, parAreas };
}

function validate(p, g) {
  const e = {};
  if (p.V_mph < 85)  e.V_mph = "≥85 mph";
  if (p.V_mph > 300) e.V_mph = "≤300 mph";
  if (g.h_ft  <= 0)  e.h_ft  = ">0";
  if (g.L_ft  <= 0)  e.L_ft  = ">0";
  if (g.B_ft  <= 0)  e.B_ft  = ">0";
  return e;
}

/* ── UI primitives ── */
function Psf({ v }) {
  if (v == null) return (<span className="text-slate-600">—</span>);
  const color = v < 0 ? "text-sky-400" : v > 0 ? "text-amber-300" : "text-slate-400";
  return (<span className={"font-mono tabular-nums " + color}>{Number(v).toFixed(1)}</span>);
}

function Field({ label, unit, error, hint, children }) {
  return (
    <div className="mb-3">
      <label className="block text-xs font-semibold tracking-wide text-slate-400 uppercase mb-1">
        {label}{unit ? <span className="text-slate-500 font-normal normal-case"> ({unit})</span> : null}
      </label>
      {children}
      {hint && !error ? <p className="text-xs text-slate-500 mt-0.5">{hint}</p> : null}
      {error ? <p className="text-xs text-red-400 mt-0.5 font-medium">{error}</p> : null}
    </div>
  );
}

function NInput({ value, onChange, min, max, step, error }) {
  return (
    <input type="number" value={value} onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
      min={min} max={max} step={step || "any"}
      className={"w-full bg-slate-800 border rounded px-3 py-1.5 text-sm text-slate-100 font-mono tabular-nums focus:outline-none focus:border-sky-500/70 focus:ring-1 focus:ring-sky-500/30 transition-colors " + (error ? "border-red-500/60" : "border-slate-600/50")} />
  );
}

function Sel({ value, onChange, options }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full bg-slate-800 border border-slate-600/50 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-sky-500/70 transition-colors">
      {options.map((o) => <option key={o.value||o} value={o.value||o}>{o.label||o}</option>)}
    </select>
  );
}

function Divider({ label }) {
  return (
    <div className="flex items-center gap-2 mt-5 mb-3">
      <div className="h-px flex-1 bg-slate-700" />
      <span className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">{label}</span>
      <div className="h-px flex-1 bg-slate-700" />
    </div>
  );
}

function Chip({ label, value }) {
  return (
    <div className="bg-slate-800/80 border border-slate-700/60 rounded px-2.5 py-1 text-center min-w-[68px]">
      <div className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">{label}</div>
      <div className="text-sm font-mono text-slate-200 tabular-nums">{value}</div>
    </div>
  );
}

function Acc({ title, open: initOpen, badge, children }) {
  const [open, setOpen] = useState(!!initOpen);
  return (
    <div className="border border-slate-700/50 rounded overflow-hidden">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-3 py-2 bg-slate-800/60 hover:bg-slate-800 transition-colors text-left">
        <span className="text-xs font-bold text-slate-300 uppercase tracking-wide">{title}</span>
        <div className="flex items-center gap-2">
          {badge}
          <svg className={"w-3.5 h-3.5 text-slate-500 transition-transform " + (open ? "rotate-180" : "")} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>
      {open ? <div className="px-3 py-2.5 bg-slate-900/40">{children}</div> : null}
    </div>
  );
}

function STabs({ tabs, active, onChange }) {
  return (
    <div className="flex gap-0.5 mb-3">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={"px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded-sm transition-colors " + (active === t.id ? "bg-sky-900/50 text-sky-400 border border-sky-700/50" : "text-slate-500 hover:text-slate-300 border border-transparent")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

function TRow({ cells, alt }) {
  return (
    <tr className={"border-b border-slate-800/50 " + (alt ? "bg-slate-900/20" : "")}>
      {cells.map((c, i) => (
        <td key={i} className={"px-2 py-1 text-xs font-mono tabular-nums whitespace-nowrap " + (i > 0 ? "text-right" : "")}>{c}</td>
      ))}
    </tr>
  );
}

function THead({ cols }) {
  return (
    <thead>
      <tr className="border-b-2 border-slate-700">
        {cols.map((c, i) => (
          <th key={i} className="px-2 py-1 text-left text-[10px] font-bold text-slate-400 uppercase tracking-wider whitespace-nowrap">{c}</th>
        ))}
      </tr>
    </thead>
  );
}

/* ── Revised C&C Matrix — shows all zones with correct areas ── */
function CCMatrix({ pressures, title, areas }) {
  const zones = [...new Set(pressures.map((p) => p.zone))];
  return (
    <div className="overflow-x-auto">
      {title ? <p className="text-xs text-slate-400 mb-1.5 font-semibold">{title}</p> : null}
      <table className="w-full text-xs font-mono tabular-nums border-collapse">
        <thead>
          <tr className="border-b-2 border-slate-700">
            <th className="px-2 py-1.5 text-left text-[10px] font-bold text-slate-400 uppercase w-28" rowSpan={2}>Zone</th>
            <th className="px-1 py-0.5 text-center text-[10px] font-bold text-slate-400 uppercase" colSpan={areas.length}>Eff. Wind Area (sf)</th>
          </tr>
          <tr className="border-b border-slate-700">
            {areas.map((a) => <th key={a} className="px-1.5 py-1 text-center text-[10px] font-bold text-sky-500/70 w-[72px]">{a}</th>)}
          </tr>
        </thead>
        <tbody>
          {zones.map((zone, zi) => {
            const m  = ZMETA[zone] || { label: zone, desc: "" };
            const zd = pressures.filter((p) => p.zone === zone);
            const isOh = zd[0]?.isOverhang;
            return (
              <tr key={zone} className={"border-b border-slate-800/50 " + (zi % 2 === 0 ? "bg-slate-900/25" : "") + (isOh ? " opacity-80" : "")}>
                <td className="px-2 py-1.5">
                  <div className="text-slate-200 font-bold text-[11px]">{m.label}</div>
                  <div className="text-[9px] text-slate-500">{m.desc}{isOh ? " (GCpi=0)" : ""}</div>
                </td>
                {areas.map((a) => {
                  const c = zd.find((p) => p.area === a);
                  if (!c) return (<td key={a} className="text-center text-slate-700">—</td>);
                  return (
                    <td key={a} className="px-1 py-1 text-center">
                      <div className="text-amber-300/90 leading-tight">{c.ppP.toFixed(1)}</div>
                      <div className="text-sky-400/90 leading-tight">{c.pnN.toFixed(1)}</div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex gap-4 mt-1.5 text-[9px] text-slate-600">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-amber-300/50" />Positive (+GCpi)</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-sky-400/50" />Suction (−GCpi)</span>
        <span>psf per cell: +max / −max</span>
      </div>
    </div>
  );
}


/* ── Wall Profile — self-contained, owns its own row state ── */
function WallProfile({ d, isNormal }) {
  const [rows, setRows] = useState([]);

  const addRow    = () => setRows((p) => [...p, { id: Date.now(), val: "", locked: false }]);
  const removeRow = (id) => setRows((p) => p.filter((r) => r.id !== id));
  const updateRow = (id, val) => setRows((p) => p.map((r) => r.id === id ? { ...r, val, locked: false } : r));
  const lockRow   = (id) => setRows((p) => p.map((r) => r.id === id ? { ...r, locked: true } : r));

  const combN = isNormal ? "combN_normal"   : "combN_parallel";
  const combP = isNormal ? "combP_normal"   : "combP_parallel";
  const lwPrs = isNormal ? d.lwPrs?.normal  : d.lwPrs?.parallel;
  const lwPn  = lwPrs?.pN ?? 0;
  const lwPp  = lwPrs?.pP ?? 0;

  function calcExtra(z_ft) {
    const alpha=9.5, zg=900, zm=15;
    const kz = 2.01 * Math.pow(Math.max(z_ft, zm) / zg, 2 / alpha);
    const qz = 0.00256 * kz * (d.kd||0.85) * (d.V||120) * (d.V||120);
    const pN_ww = qz * (d.G||0.85) * 0.8 - d.qh * (d.gcpi||0.18);
    const pP_ww = qz * (d.G||0.85) * 0.8 + d.qh * (d.gcpi||0.18);
    return {
      kz,
      kzt: 1.0,
      cN: Math.round((pN_ww - lwPn) * 100) / 100,
      cP: Math.round((pP_ww - lwPp) * 100) / 100,
    };
  }

  /* Base profile rows from apiDir */
  const baseEntries = (d.profile || []).map((r) => ({
    key: "b-" + r.z_ft,
    z_ft: r.z_ft, kz: r.kz, kzt: r.kzt ?? 1.0,
    cN: r[combN], cP: r[combP],
    isBase: true,
  }));

  /* Extra rows: locked ones sort into the table; unlocked ones stay at bottom */
  const lockedExtras = rows
    .filter((r) => r.locked && !isNaN(parseFloat(r.val)) && parseFloat(r.val) > 0)
    .map((r) => {
      const z   = parseFloat(r.val);
      const calc = calcExtra(z);
      return { key: "e-" + r.id, id: r.id, val: r.val, z_ft: z, ...calc, isBase: false, locked: true };
    });

  const sortedRows = [...baseEntries, ...lockedExtras].sort((a, b) => a.z_ft - b.z_ft);

  /* Unlocked rows always stay at the bottom — no jumping */
  const unlockedRows = rows
    .filter((r) => !r.locked)
    .map((r) => {
      const z     = parseFloat(r.val);
      const valid = !isNaN(z) && z > 0;
      const calc  = valid ? calcExtra(z) : null;
      return { key: "u-" + r.id, id: r.id, val: r.val, valid, calc };
    });

  return (
    <div className="border border-slate-700/50 rounded overflow-hidden">
      <div className="px-3 py-2 bg-slate-800/60 flex items-center justify-between">
        <span className="text-xs font-bold text-slate-300 uppercase tracking-wide">Wall Profile — Combined WW + LW (psf)</span>
        <button
          onClick={addRow}
          className="text-[10px] px-2.5 py-0.5 bg-sky-900/40 border border-sky-700/50 rounded text-sky-400 hover:bg-sky-800/60 transition-colors font-semibold tracking-wide">
          + Add Height (Z)
        </button>
      </div>

      <div className="px-3 py-2.5 bg-slate-900/40 space-y-2">
        {lwPrs ? (
          <div className="flex flex-wrap gap-x-5 gap-y-0.5 text-[10px] font-mono text-slate-500 pb-1.5 border-b border-slate-800/60">
            <span>LW Cp = {isNormal ? (d.cLW_n||0).toFixed(3) : (d.cLW_p||0).toFixed(3)}</span>
            <span>LW w/+GCpi: <span className="text-slate-400">{lwPn.toFixed(2)} psf</span></span>
            <span>LW w/−GCpi: <span className="text-slate-400">{lwPp.toFixed(2)} psf</span></span>
            <span className="text-slate-600">Combined = |WW| + |LW| = WW − LW</span>
          </div>
        ) : null}

        <table className="w-full text-xs font-mono tabular-nums">
          <THead cols={["z (ft)", "Kz", "Kzt", "WW+LW w/+GCpi", "WW+LW w/−GCpi", ""]} />
          <tbody>
            {/* Sorted base + locked extra rows */}
            {sortedRows.map((r, i) => (
              <tr key={r.key}
                className={"border-b border-slate-800/50 " + (i%2===1 ? "bg-slate-900/20" : "") + (!r.isBase ? " bg-sky-950/20" : "")}>
                <td className="px-2 py-1 whitespace-nowrap">
                  {r.isBase ? (
                    <span className="text-slate-300">{r.z_ft.toFixed(1)}</span>
                  ) : (
                    <input
                      type="number" min="1" step="1"
                      value={r.val}
                      onChange={(e) => updateRow(r.id, e.target.value)}
                      onBlur={() => lockRow(r.id)}
                      onKeyDown={(e) => { if (e.key === "Enter") { lockRow(r.id); e.target.blur(); } }}
                      className="w-16 bg-transparent border-b border-sky-600/40 text-sky-300 font-mono text-xs focus:outline-none focus:border-sky-400 tabular-nums" />
                  )}
                </td>
                <td className="px-2 py-1 text-right text-slate-400">{r.kz != null ? r.kz.toFixed(4) : "—"}</td>
                <td className="px-2 py-1 text-right text-slate-400">{r.kzt != null ? r.kzt.toFixed(4) : "—"}</td>
                <td className="px-2 py-1 text-right">{r.cN != null ? <Psf v={r.cN} /> : <span className="text-slate-600">—</span>}</td>
                <td className="px-2 py-1 text-right">{r.cP != null ? <Psf v={r.cP} /> : <span className="text-slate-600">—</span>}</td>
                <td className="px-1 py-1 text-center w-5">
                  {!r.isBase ? (
                    <button onClick={() => removeRow(r.id)} className="text-red-500/50 hover:text-red-400 text-[11px]">✕</button>
                  ) : null}
                </td>
              </tr>
            ))}

            {/* Unlocked (being typed) rows — pinned at bottom, never jump */}
            {unlockedRows.map((r) => (
              <tr key={r.key} className="border-b border-slate-800/30 bg-sky-950/10">
                <td className="px-2 py-1">
                  <input
                    type="number" min="1" step="1"
                    value={r.val}
                    onChange={(e) => updateRow(r.id, e.target.value)}
                    onBlur={() => lockRow(r.id)}
                    onKeyDown={(e) => { if (e.key === "Enter") { lockRow(r.id); e.target.blur(); } }}
                    autoFocus
                    placeholder="z ft"
                    className="w-16 bg-transparent border-b border-sky-500/60 text-sky-300 font-mono text-xs focus:outline-none focus:border-sky-300 tabular-nums" />
                </td>
                <td className="px-2 py-1 text-right text-slate-500">{r.valid ? r.calc.kz.toFixed(4) : "—"}</td>
                <td className="px-2 py-1 text-right text-slate-500">{r.valid ? r.calc.kzt.toFixed(4) : "—"}</td>
                <td className="px-2 py-1 text-right">{r.valid ? <span className="opacity-60"><Psf v={r.calc.cN} /></span> : <span className="text-slate-600">—</span>}</td>
                <td className="px-2 py-1 text-right">{r.valid ? <span className="opacity-60"><Psf v={r.calc.cP} /></span> : <span className="text-slate-600">—</span>}</td>
                <td className="px-1 py-1 text-center w-5">
                  <button onClick={() => removeRow(r.id)} className="text-red-500/50 hover:text-red-400 text-[11px]">✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <p className="text-[10px] text-slate-600 pt-0.5">
          Combined = p_WW(z) − p_LW = |WW|+|LW|. At z = h GCpi cancels. GCpi = ±{d.gcpi}.
          <span className="text-sky-700/70 ml-2">Type height then press Enter or click away to sort into table.</span>
        </p>
      </div>
    </div>
  );
}

/* ── MWFRS Directional tab ── */
function DirTab({ d, sub, setSub }) {
  const isNormal   = sub === "normal";
  const cpLw      = isNormal ? d.cLW_n : d.cLW_p;
  const lwRatio   = isNormal ? d.ratioLW_n : d.ratioLW_p;
  const roofRatio = isNormal ? d.ratioRoof_n : d.ratioRoof_p;
  const rz        = isNormal ? d.roofNormal : d.roofParallel;
  const lwP       = isNormal ? d.lwP_n : d.lwP_p;
  const lwN       = isNormal ? d.lwN_n : d.lwN_p;
  const dirLabel  = isNormal ? "Normal to Ridge" : "Parallel to Ridge";
  const ratioLabel = isNormal ? "B/L" : "L/B";
  const roofLabel  = isNormal ? "h/B" : "h/L";

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-bold text-slate-300">
        MWFRS Directional — Ch. 27 | L = {d.L} ft | B = {d.B} ft | h = {d.h} ft
      </h2>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono text-slate-400">
        <span>qh = {d.qh.toFixed(2)} psf</span>
        <span>G = <span className="text-sky-400 font-bold">{d.G.toFixed(4)}</span></span>
        {d.kztH && d.kztH !== 1.0 ? <span className="text-amber-400/80">Kzt = {d.kztH.toFixed(4)}</span> : null}
        <span className="text-slate-600 text-[9px]">{d.gRes?.note}</span>
      </div>
      <STabs tabs={[{ id:"normal", label:"Normal to Ridge" }, { id:"parallel", label:"Parallel" }, { id:"torsion", label:"Torsion (4 Cases)" }]} active={sub} onChange={setSub} />

      {(sub === "normal" || sub === "parallel") ? (
        <>
          <div className="px-3 py-1.5 bg-slate-800/40 border border-slate-700/30 rounded text-[10px] text-slate-400 flex flex-wrap gap-x-4 gap-y-0.5 font-mono">
            <span>{dirLabel}</span>
            <span>LW ratio ({ratioLabel}) = {lwRatio}</span>
            <span>Roof ratio ({roofLabel}) = {roofRatio}</span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {[{ l:"Windward", cp:d.cWW }, { l:"Leeward", cp:cpLw }, { l:"Side", cp:d.cSW }].map((w) => (
              <div key={w.l} className="bg-slate-800/60 border border-slate-700/50 rounded p-2.5">
                <div className="text-[10px] text-slate-500 font-semibold uppercase">{w.l}</div>
                <div className="text-base font-bold text-slate-200 font-mono">{w.cp.toFixed(2)} <span className="text-[10px] text-slate-600">Cp</span></div>
              </div>
            ))}
          </div>

          <Acc title={"Surface Pressures — " + dirLabel + " (psf)"} open={true}>
            <table className="w-full text-xs font-mono tabular-nums">
              <THead cols={["Surface", "Cp", "qGCp", "w/ +GCpi", "w/ −GCpi"]} />
              <tbody>
                <TRow cells={["Windward","0.80",(d.qh*d.G*0.8).toFixed(2),<Psf v={d.qh*d.G*0.8 - d.qh*d.gcpi} />,<Psf v={d.qh*d.G*0.8 + d.qh*d.gcpi} />]} />
                <TRow cells={["Leeward",cpLw.toFixed(4),(d.qh*d.G*cpLw).toFixed(2),<Psf v={lwP} />,<Psf v={lwN} />]} alt />
                <TRow cells={["Side","−0.70",(d.qh*d.G*-0.7).toFixed(2),<Psf v={d.swP} />,<Psf v={d.swN} />]} />
              </tbody>
            </table>
          </Acc>

          {rz ? (
            <Acc title={"Roof Zones — " + dirLabel + " (" + roofLabel + " = " + roofRatio + ")"} open={true}>
              <table className="w-full text-xs font-mono tabular-nums">
                <THead cols={["Zone","Cp","qhGCp","w/ +GCpi","w/ −GCpi"]} />
                <tbody>
                  {rz.map((r, i) => {
                    const q = d.qh * d.G * r.cp;
                    return (<TRow key={i} alt={i%2===1} cells={[r.zone, r.cp.toFixed(2), q.toFixed(2), <Psf v={q - d.qh*d.gcpi} />, <Psf v={q + d.qh*d.gcpi} />]} />);
                  })}
                </tbody>
              </table>
            </Acc>
          ) : null}

          <WallProfile d={d} isNormal={isNormal} />
        </>
      ) : null}

      {sub === "torsion" && d.torsion ? (
        <div className="space-y-3">
          <p className="text-xs text-slate-500">ASCE 7 Fig 27.3-8: Four mandatory cases for rigid diaphragms.</p>
          {d.torsion.map((tc) => (
            <div key={tc.id} className="bg-slate-800/40 border border-slate-700/40 rounded p-3">
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-xs font-bold text-sky-400">Case {tc.id}</span>
                <span className="text-xs font-semibold text-slate-300">{tc.label}</span>
                {tc.mt ? <span className="text-[9px] px-1.5 py-0.5 bg-amber-900/30 border border-amber-700/40 rounded text-amber-400 font-bold uppercase">+Torsion</span> : null}
              </div>
              <div className="grid grid-cols-4 gap-2 text-xs font-mono">
                {[{ l:"WW", v:tc.pWW }, { l:"LW", v:tc.pLW }, { l:"SW", v:tc.pSW }, { l:"Roof", v:tc.pR }].map((s) => (
                  <div key={s.l} className="bg-slate-900/50 rounded px-2 py-1.5">
                    <div className="text-[9px] text-slate-500 uppercase">{s.l}</div>
                    <Psf v={s.v} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════ */
/*  MAIN COMPONENT                                                   */
/* ══════════════════════════════════════════════════════════════════ */

export default function WindCalculator() {
  const [proj, setProj] = useState({ code_version:"7-22", risk_category:"III", V_mph:120, exposure:"C", enclosure:"enclosed" });
  const [geo, setGeo]   = useState({ L_ft:100, B_ft:60, h_ft:15, roof_type:"monoslope", roof_angle_deg:1.2, parapet_height_ft:3 });
  const [kd]  = useState(0.85);
  // Topographic factor inputs (§26.8)
  const [kztIn, setKztIn] = useState({
    topo_type: "flat",
    H_ft:   80,    // hill/escarpment height
    Lh_ft:  100,   // half-length of hill
    x_ft:   50,    // distance from crest (+ve = downwind)
    upwind: false,
  });
  // Gust effect factor inputs (§26.11)
  const [gustIn, setGustIn] = useState({
    mode:  "rigid_fixed",
    n1:    1.0,    // natural frequency (Hz)
    beta:  0.02,   // damping ratio
  });
  const ukzt = (f,v) => setKztIn((s) => ({...s,[f]:v}));
  const ugust = (f,v) => setGustIn((s) => ({...s,[f]:v}));
  const [extraHeights, setExtraHeights] = useState([]);
  const addHeight = () => setExtraHeights((h) => [...h, { id: Date.now(), val: "" }]);
  const removeHeight = (id) => setExtraHeights((h) => h.filter((r) => r.id !== id));
  const updateHeight = (id, val) => setExtraHeights((h) => h.map((r) => r.id === id ? {...r, val} : r));
  const [tab, setTab] = useState("qz");
  const [dirSub, setDirSub] = useState("normal");
  const [ccSub,  setCcSub]  = useState("roof");
  const [calc, setCalc] = useState(false);
  const [errs, setErrs] = useState({});
  const [apiE, setApiE] = useState(null);
  const [qzR,  setQzR]  = useState(null);
  const [dirR, setDirR] = useState(null);
  const [lrR,  setLrR]  = useState(null);
  const [ccR,  setCcR]  = useState(null);

  const up = (f,v) => { setProj((p) => ({...p,[f]:v})); setErrs((e) => ({...e,[f]:undefined})); };
  const ug = (f,v) => { setGeo((p)  => ({...p,[f]:v})); setErrs((e) => ({...e,[f]:undefined})); };

  const shared = useMemo(() => {
    if (!qzR) return null;
    const q = qzR.pressures[qzR.pressures.length - 1];
    return { ke: q.ke, kd: q.kd, alpha: q.alpha, zg: q.zg_ft, qh: q.qz_psf, kztH: qzR.kztH };
  }, [qzR]);

  const run = useCallback(async () => {
    const ve = validate(proj, geo);
    if (Object.keys(ve).length > 0) { setErrs(ve); return; }
    setErrs({}); setApiE(null); setCalc(true);
    const bp = { project:{...proj, importance_factor:1}, geometry:{...geo, extraHeights: extraHeights.map(r=>parseFloat(r.val)).filter(v=>!isNaN(v)&&v>0)}, kd, kztInputs:kztIn, gustInputs:gustIn };
    try {
      const [a,b,c,d] = await Promise.allSettled([apiQz(bp), apiDir(bp), apiLR(bp), apiCC(bp)]);
      if (a.status==="fulfilled") setQzR(a.value);
      if (b.status==="fulfilled") setDirR(b.value);
      if (c.status==="fulfilled") setLrR(c.value);
      if (d.status==="fulfilled") setCcR(d.value);
    } catch (err) { setApiE(err.message); }
    setCalc(false);
  }, [proj, geo, kd]);

  const lrOk = lrR ? lrR.ok : null;

  // Determine areas for C&C display
  const ccRoofAreas = CC_AREAS_ROOF;
  const ccWallAreas = CC_AREAS_WALL;

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200" style={{ fontFamily:"'JetBrains Mono','Fira Code','SF Mono',monospace" }}>
      {/* ── SIDEBAR ── */}
      <aside className="w-72 shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col overflow-y-auto">
        <div className="px-4 py-3 border-b border-slate-800 sticky top-0 bg-slate-900/90 backdrop-blur-sm z-10">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-bold text-slate-100">WIND LOADS</span>
            <span className="text-[10px] text-sky-500 font-semibold">ASCE 7</span>
          </div>
        </div>
        <div className="px-4 py-3 flex-1">
          <Divider label="Project" />
          <Field label="Edition"><Sel value={proj.code_version} onChange={(v) => up("code_version",v)} options={CODE_VERS} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Risk Cat"><Sel value={proj.risk_category} onChange={(v) => up("risk_category",v)} options={["I","II","III","IV"].map((v) => ({value:v,label:v}))} /></Field>
            <Field label="Exposure"><Sel value={proj.exposure} onChange={(v) => up("exposure",v)} options={EXPOSURES} /></Field>
          </div>
          <Field label="V" unit="mph" error={errs.V_mph}><NInput value={proj.V_mph} onChange={(v) => up("V_mph",v)} min={85} max={300} error={errs.V_mph} /></Field>
          <Field label="Enclosure"><Sel value={proj.enclosure} onChange={(v) => up("enclosure",v)} options={ENCLOSURES} /></Field>
          <Divider label="Geometry" />
          <div className="grid grid-cols-2 gap-2">
            <Field label="L" unit="ft" error={errs.L_ft}><NInput value={geo.L_ft} onChange={(v) => ug("L_ft",v)} min={1} error={errs.L_ft} /></Field>
            <Field label="B" unit="ft" error={errs.B_ft}><NInput value={geo.B_ft} onChange={(v) => ug("B_ft",v)} min={1} error={errs.B_ft} /></Field>
          </div>
          <Field label="h" unit="ft" error={errs.h_ft}><NInput value={geo.h_ft} onChange={(v) => ug("h_ft",v)} min={1} error={errs.h_ft} /></Field>
          <Field label="Roof"><Sel value={geo.roof_type} onChange={(v) => ug("roof_type",v)} options={ROOFS.map((r) => ({value:r.value,label:r.label}))} /></Field>
          <Field label="θ" unit="deg"><NInput value={geo.roof_angle_deg} onChange={(v) => ug("roof_angle_deg",v)} min={0} max={90} step={0.1} /></Field>
          <Field label="Parapet ht" unit="ft"><NInput value={geo.parapet_height_ft} onChange={(v) => ug("parapet_height_ft",v)} min={0} step={0.5} /></Field>

          {/* ── Topographic Factor ── */}
          <Divider label="Topographic Factor Kzt" />
          <Field label="Topography">
            <Sel value={kztIn.topo_type} onChange={(v) => ukzt("topo_type", v)} options={TOPO_TYPES} />
          </Field>
          {kztIn.topo_type !== "flat" ? (
            <>
              <div className="grid grid-cols-2 gap-2">
                <Field label="H" unit="ft"><NInput value={kztIn.H_ft} onChange={(v) => ukzt("H_ft", v)} min={0} step={1} /></Field>
                <Field label="Lh" unit="ft"><NInput value={kztIn.Lh_ft} onChange={(v) => ukzt("Lh_ft", v)} min={1} step={1} /></Field>
              </div>
              <Field label="x from crest" unit="ft">
                <NInput value={kztIn.x_ft} onChange={(v) => ukzt("x_ft", v)} step={1} />
              </Field>
              <Field label="Location">
                <Sel value={kztIn.upwind ? "upwind" : "downwind"} onChange={(v) => ukzt("upwind", v === "upwind")}
                  options={[{value:"upwind",label:"Upwind of crest"},{value:"downwind",label:"Downwind of crest"}]} />
              </Field>
              {/* live Kzt preview */}
              {(() => {
                const r = calcKzt(kztIn.topo_type, kztIn.H_ft, kztIn.Lh_ft, kztIn.x_ft, geo.h_ft, kztIn.upwind);
                return (
                  <div className="px-3 py-2 bg-sky-950/30 border border-sky-800/40 rounded text-xs font-mono text-slate-300 space-y-0.5">
                    <div className="flex justify-between"><span className="text-slate-500">H/Lh</span><span>{r.hLh.toFixed(4)}</span></div>
                    <div className="flex justify-between"><span className="text-slate-500">K1</span><span>{r.k1.toFixed(4)}</span></div>
                    <div className="flex justify-between"><span className="text-slate-500">K2</span><span>{r.k2.toFixed(4)}</span></div>
                    <div className="flex justify-between"><span className="text-slate-500">K3</span><span>{r.k3.toFixed(4)}</span></div>
                    <div className="flex justify-between font-bold text-sky-300"><span>Kzt @ h</span><span>{r.kzt.toFixed(4)}</span></div>
                  </div>
                );
              })()}
            </>
          ) : (
            <div className="px-3 py-2 bg-slate-800/30 rounded text-xs font-mono text-slate-500">Kzt = 1.0 (flat terrain)</div>
          )}

          {/* ── Gust Effect Factor ── */}
          <Divider label="Gust Effect Factor G" />
          <Field label="Method">
            <Sel value={gustIn.mode} onChange={(v) => ugust("mode", v)} options={GUST_MODES} />
          </Field>
          {gustIn.mode !== "rigid_fixed" ? (
            <>
              {gustIn.mode === "flexible" ? (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="n₁" unit="Hz"><NInput value={gustIn.n1} onChange={(v) => ugust("n1", v)} min={0.01} step={0.05} /></Field>
                    <Field label="β" unit="ratio"><NInput value={gustIn.beta} onChange={(v) => ugust("beta", v)} min={0.005} max={0.2} step={0.005} /></Field>
                  </div>
                </>
              ) : null}
              {/* live G preview */}
              {(() => {
                const r = calcG(gustIn.mode, proj.exposure, geo.h_ft, gustIn.n1, gustIn.beta, proj.V_mph);
                return (
                  <div className="px-3 py-2 bg-sky-950/30 border border-sky-800/40 rounded text-xs font-mono text-slate-300 space-y-0.5">
                    {r.Iz  != null ? <div className="flex justify-between"><span className="text-slate-500">Iz</span><span>{r.Iz.toFixed(4)}</span></div> : null}
                    {r.Lz  != null ? <div className="flex justify-between"><span className="text-slate-500">Lz (ft)</span><span>{r.Lz.toFixed(2)}</span></div> : null}
                    {r.Q   != null ? <div className="flex justify-between"><span className="text-slate-500">Q</span><span>{r.Q.toFixed(4)}</span></div> : null}
                    {r.R   != null ? <div className="flex justify-between"><span className="text-slate-500">R</span><span>{r.R.toFixed(4)}</span></div> : null}
                    <div className="flex justify-between font-bold text-sky-300"><span>G</span><span>{r.G.toFixed(4)}</span></div>
                    <div className="text-slate-600 text-[9px] mt-0.5">{r.note}</div>
                  </div>
                );
              })()}
            </>
          ) : (
            <div className="px-3 py-2 bg-slate-800/30 rounded text-xs font-mono text-slate-500">G = 0.85 (§26.11.1 fixed)</div>
          )}
        </div>
        <div className="px-4 py-3 border-t border-slate-800 sticky bottom-0 bg-slate-900/90 backdrop-blur-sm">
          {apiE ? <div className="mb-2 px-2.5 py-1.5 bg-red-950/40 border border-red-800/50 rounded text-xs text-red-400">{apiE}</div> : null}
          <button onClick={run} disabled={calc} className="w-full py-2 rounded font-bold text-sm tracking-wider uppercase bg-sky-600 hover:bg-sky-500 disabled:bg-slate-700 disabled:text-slate-500 text-white transition-all">
            {calc ? "Computing…" : "Calculate"}
          </button>
        </div>
      </aside>

      {/* ── MAIN ── */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {shared ? (
          <div className="px-4 py-2 border-b border-slate-800 bg-slate-900/50 flex flex-wrap gap-1.5">
            <Chip label="Ke"   value={shared.ke.toFixed(4)} />
            <Chip label="Kd"   value={shared.kd.toFixed(2)} />
            <Chip label="Kzt"  value={shared.kztH != null ? shared.kztH.toFixed(4) : "1.0000"} />
            <Chip label="α"    value={shared.alpha.toFixed(1)} />
            <Chip label="zg"   value={shared.zg + "'"} />
            <Chip label="qh"   value={shared.qh.toFixed(2) + " psf"} />
            <Chip label="G"    value={dirR ? dirR.G.toFixed(4) : (gustIn.mode==="rigid_fixed" ? "0.8500" : "—")} />
            <Chip label="GCpi" value={"±" + gcpiOf(proj.enclosure)} />
          </div>
        ) : null}

        {/* tabs */}
        <div className="px-4 pt-2 flex gap-0.5 border-b border-slate-800">
          {TABS.map((t) => {
            const dis = t.id === "lr" && lrOk === false;
            const act = tab === t.id;
            return (
              <button key={t.id} onClick={() => !dis && setTab(t.id)} disabled={dis}
                title={dis && lrR ? lrR.reason : ""}
                className={"px-3 py-1.5 text-[10px] font-bold tracking-wider uppercase rounded-t transition-all " + (act ? "bg-slate-800 text-sky-400 border border-slate-700 border-b-transparent -mb-px" : dis ? "text-slate-600 cursor-not-allowed opacity-40" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/40")}>
                {t.label}{t.id==="lr" && lrOk===false ? <span className="ml-1 text-[8px] text-amber-500">N/A</span> : null}
              </button>
            );
          })}
        </div>

        {/* content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {!qzR && !calc ? <div className="flex flex-col items-center justify-center h-full opacity-30"><p className="text-sm text-slate-600">Enter parameters and Calculate</p></div> : null}

          {/* ── qz Profile ── */}
          {tab === "qz" && qzR ? (
            <div>
              <h2 className="text-sm font-bold text-slate-300 mb-3">Velocity Pressure — {qzR.code_version}, Exp {qzR.exposure}, V={qzR.V_mph} mph</h2>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono text-slate-400 mb-3">
                <span>Kd = {shared.kd.toFixed(2)}</span>
                <span>Ke = {shared.ke.toFixed(4)}</span>
                <span>Kzt @ h = {qzR.kztH != null ? qzR.kztH.toFixed(4) : "1.0000"}</span>
                {kztIn.topo_type !== "flat" ? <span className="text-amber-400/80">Topo: {TOPO_TYPES.find(t=>t.value===kztIn.topo_type)?.label}</span> : null}
              </div>
              <table className="w-full text-xs font-mono tabular-nums">
                <THead cols={["z (ft)","Kz","Kzt","qz (psf)","α","zg (ft)"]} />
                <tbody>
                  {qzR.pressures.map((r, i) => (
                    <TRow key={i} alt={i%2===1} cells={[r.z_ft.toFixed(1), r.kz.toFixed(4), r.kzt != null ? r.kzt.toFixed(4) : "1.0000", r.qz_psf.toFixed(2), r.alpha.toFixed(1), r.zg_ft.toFixed(0)]} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {/* ── MWFRS Dir ── */}
          {tab === "dir" && dirR ? <DirTab d={dirR} sub={dirSub} setSub={setDirSub} /> : null}

          {/* ── MWFRS LR ── */}
          {tab === "lr" ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold text-slate-300">MWFRS Low-Rise — Ch. 28</h2>
                {lrR ? (lrR.ok ? <span className="text-xs font-semibold text-emerald-400">Applicable</span> : <span className="text-xs font-semibold text-amber-400">N/A: {lrR.reason}</span>) : null}
              </div>
              {lrR && !lrR.ok ? <div className="px-4 py-3 bg-amber-950/20 border border-amber-800/30 rounded"><p className="text-sm text-amber-400">{lrR.reason}</p></div> : null}
              {lrR && lrR.ok ? (
                <>
                  <div className="flex gap-3 text-xs font-mono text-slate-400"><span>qh = {lrR.qh} psf</span><span>2a = {lrR.ez} ft</span></div>
                  <Acc title="Case A — Transverse" open={true}>
                    <table className="w-full text-xs font-mono tabular-nums">
                      <THead cols={["Zone","GCpf","+GCpi","−GCpi"]} />
                      <tbody>{lrR.cA.map((r, i) => <TRow key={i} alt={i%2===1} cells={[r.zone, r.gcpf.toFixed(4), <Psf v={r.pN} />, <Psf v={r.pP} />]} />)}</tbody>
                    </table>
                  </Acc>
                  <Acc title="Case B — Longitudinal" open={true}>
                    <table className="w-full text-xs font-mono tabular-nums">
                      <THead cols={["Zone","GCpf","+GCpi","−GCpi"]} />
                      <tbody>{lrR.cB.map((r, i) => <TRow key={i} alt={i%2===1} cells={[r.zone, r.gcpf.toFixed(4), <Psf v={r.pN} />, <Psf v={r.pP} />]} />)}</tbody>
                    </table>
                  </Acc>
                  {/* Horizontal MWFRS Simple Diaphragm Pressures */}
                  {lrR.sd ? (
                    <Acc title="Horizontal MWFRS Simple Diaphragm Pressures (psf)" open={true}>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono text-slate-500 mb-3">
                        <span>qh = {lrR.qh.toFixed(2)} psf</span>
                        <span>Edge strip a = {lrR.sd.a} ft</span>
                        <span>End zone 2a = {lrR.sd.endZone2a} ft</span>
                      </div>
                      <p className="text-[11px] font-bold text-slate-300 mb-2 tracking-wide">Transverse direction (normal to L)</p>
                      <div className="space-y-1 mb-4 pl-2 font-mono text-xs">
                        <div className="flex justify-between"><span className="text-slate-400">Interior Zone: &nbsp; Wall</span><span className="text-amber-300 font-bold">{lrR.sd.transverse.intWall.toFixed(1)} psf</span></div>
                        <div className="flex justify-between"><span className="text-slate-500">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Roof</span><span className="text-sky-400">{lrR.sd.transverse.intRoof.toFixed(1)} psf **</span></div>
                        <div className="flex justify-between"><span className="text-slate-400">End Zone: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Wall</span><span className="text-amber-300 font-bold">{lrR.sd.transverse.endWall.toFixed(1)} psf</span></div>
                        <div className="flex justify-between"><span className="text-slate-500">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Roof</span><span className="text-sky-400">{lrR.sd.transverse.endRoof.toFixed(1)} psf **</span></div>
                      </div>
                      <p className="text-[11px] font-bold text-slate-300 mb-2 tracking-wide">Longitudinal direction (parallel to L)</p>
                      <div className="space-y-1 mb-3 pl-2 font-mono text-xs">
                        <div className="flex justify-between"><span className="text-slate-400">Interior Zone: &nbsp; Wall</span><span className="text-amber-300 font-bold">{lrR.sd.longitudinal.intWall.toFixed(1)} psf</span></div>
                        <div className="flex justify-between"><span className="text-slate-400">End Zone: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Wall</span><span className="text-amber-300 font-bold">{lrR.sd.longitudinal.endWall.toFixed(1)} psf</span></div>
                      </div>
                      <div className="space-y-1 pt-1.5 border-t border-slate-800/60 text-[10px] text-slate-500">
                        <p>** NOTE: Total horiz force shall not be less than that determined by neglecting roof forces (except for MWFRS moment frames).</p>
                        <p className="text-amber-400/80 font-medium">The code requires the MWFRS be designed for a min ultimate force of 16 psf multiplied by the wall area plus an 8 psf force applied to the vertical projection of the roof.</p>
                      </div>
                    </Acc>
                  ) : null}
                  <p className="text-[10px] text-slate-500 px-1">Torsional loads = 25% of zones 1–6 per §28.3.4. Light-frame/flexible diaphragm exempt.</p>
                </>
              ) : null}
            </div>
          ) : null}

          {/* ── C&C ── */}
          {tab === "cc" && ccR ? (
            <div className="space-y-4">
              <h2 className="text-sm font-bold text-slate-300">C&C — {ccR.proc}</h2>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono text-slate-400">
                <span>qh = {ccR.qh} psf</span>
                <span>GCpi = ±{ccR.gcpi}</span>
                <span>a = {ccR.a} ft</span>
                <span>θ = {ccR.theta}°</span>
                <span>Min = 16 psf</span>
              </div>
              {ccR.theta <= 10 ? (
                <div className="text-[10px] text-amber-500/80 px-1">Note: GCp values from ASCE 7-22 Fig 30.3-2A (final design values for θ ≤ 10°)</div>
              ) : null}
              {ccR.prs.some(p => p.zone === "3") ? (
                <div className="text-[10px] text-slate-500 px-1">Note: Negative Zone 3 = Zone 2 when parapet ≥ 3 ft</div>
              ) : null}
              <STabs tabs={[
                { id:"roof",     label:"Roof (1, 1’, 2, 3)" },
                { id:"overhang", label:"Overhangs" },
                { id:"wall",     label:"Walls (4–5)" },
                { id:"parapet",  label:"Parapet" },
              ]} active={ccSub} onChange={setCcSub} />

              {ccSub === "roof" ? (
                <CCMatrix
                  pressures={ccR.prs.filter((p) => ["1","1p","2","3"].includes(p.zone))}
                  title="Roof C&C (psf)"
                  areas={ccRoofAreas}
                />
              ) : null}

              {ccSub === "overhang" ? (
                <CCMatrix
                  pressures={ccR.prs.filter((p) => ["oh1","oh2","oh3"].includes(p.zone))}
                  title="Roof Overhang C&C (psf) — GCpi = 0"
                  areas={ccRoofAreas}
                />
              ) : null}

              {ccSub === "wall" ? (
                <CCMatrix
                  pressures={ccR.prs.filter((p) => ["4","5"].includes(p.zone))}
                  title="Wall C&C (psf)"
                  areas={ccWallAreas}
                />
              ) : null}

              {ccSub === "parapet" && ccR.parPrs ? (
                <div>
                  <p className="text-xs font-semibold text-slate-400 mb-1.5">Solid Parapet Pressure (psf) — §30.9 / Fig 30.9-1</p>
                  <div className="text-[10px] font-mono text-slate-500 mb-2">Kd × qp = {ccR.qh} psf (qp = qh at roof height)</div>
                  <table className="w-full text-xs font-mono tabular-nums">
                    <thead>
                      <tr className="border-b-2 border-slate-700">
                        <th className="px-2 py-1.5 text-left text-[10px] font-bold text-slate-400 uppercase w-36" rowSpan={2}>Case</th>
                        <th className="px-1 py-0.5 text-center text-[10px] font-bold text-slate-400 uppercase" colSpan={ccR.parAreas.length}>Eff. Wind Area (sf)</th>
                      </tr>
                      <tr className="border-b border-slate-700">
                        {ccR.parAreas.map((a) => <th key={a} className="px-1.5 py-1 text-center text-[10px] font-bold text-sky-500/70">{a}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="border-b border-slate-800/50 bg-slate-900/25">
                        <td className="px-2 py-1.5 text-[11px] font-bold text-slate-200">CASE A: Zone 2 &amp; 3</td>
                        {ccR.parPrs.map((r) => <td key={r.area} className="px-1 py-1.5 text-center text-amber-300/90">{r.caseA.toFixed(1)}</td>)}
                      </tr>
                      <tr className="border-b border-slate-800/50">
                        <td className="px-2 py-1.5 text-[11px] font-bold text-slate-200">CASE B: Interior zone</td>
                        {ccR.parPrs.map((r) => <td key={r.area} className="px-1 py-1.5 text-center text-sky-400/90">{r.caseBint.toFixed(1)}</td>)}
                      </tr>
                      <tr className="border-b border-slate-800/50 bg-slate-900/25">
                        <td className="px-2 py-1.5 text-[11px] font-bold text-slate-200">CASE B: Corner zone</td>
                        {ccR.parPrs.map((r) => <td key={r.area} className="px-1 py-1.5 text-center text-sky-400/90">{r.caseBcor.toFixed(1)}</td>)}
                      </tr>
                    </tbody>
                  </table>
                  <p className="text-[10px] text-slate-500 mt-1.5">Case A = combined WW+LW. Case B = suction; corner zone within a = {ccR.a} ft.</p>
                </div>
              ) : null}
            </div>
          ) : null}

        </div>
      </main>
    </div>
  );
}
