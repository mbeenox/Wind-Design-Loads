"""
ASCE 7 Wind Load — PE Calculation Package Generator
=====================================================
Generates the "Show Your Work" PDF that a Professional Engineer stamps
and submits to the Authority Having Jurisdiction.

ARCHITECTURE DECISION: Backend (ReportLab) over Frontend (jsPDF)
-----------------------------------------------------------------
Why ReportLab on the Python backend?

1. EQUATION RENDERING — Engineering calcs need subscripts, superscripts,
   Greek letters, and multi-line substitution chains. ReportLab's Paragraph
   engine with XML markup (<sub>, <super>, <b>, <i>) handles this natively.
   jsPDF has no equivalent; you'd be drawing glyph-by-glyph on a canvas.

2. DETERMINISTIC OUTPUT — The PE stamps a specific document. Backend
   generation ensures identical PDF bytes regardless of browser, OS, or
   font availability. Frontend generation varies across Chrome/Safari/FF.

3. AUDIT TRAIL — The PDF is generated from the same Python engine that
   computed the numbers. There's zero risk of the frontend displaying one
   value and the PDF showing another due to floating-point rounding in JS.

4. TABULAR DATA — ReportLab's Table class handles spanning, alignment,
   and page-break splitting natively. jspdf-autotable is adequate but
   can't match ReportLab's control over cell padding and border weight.

5. LEGAL DEFENSIBILITY — When a plan checker questions a value, the PDF
   must trace back to the exact code section and equation number. This
   traceability is enforced by the EquationTrace dataclass (below), which
   the engine populates and the PDF generator consumes. Keeping both in
   Python means the chain is auditable.

REPORT LAYOUT SKELETON
-----------------------
  Page 1:
    ┌─────────────────────────────────────┐
    │ ENGINEERING HEADER                  │  ← Project name, job #, date,
    │ Company / PE / Sheet # of #        │     engineer initials
    ├─────────────────────────────────────┤
    │ GLOBAL WIND CRITERIA               │  ← Code version, Risk Cat, V,
    │ Building Code, Exposure, Enclosure │     Exposure, Enclosure, Kd
    ├─────────────────────────────────────┤
    │ SITE & BUILDING PARAMETERS         │  ← L, B, h, θ, Ke, Kzt, G
    ├─────────────────────────────────────┤
    │ VELOCITY PRESSURE (§26.10)         │
    │   Step 1: Kz = 2.01(z/zg)^(2/α)   │  ← Formula → Substitution → Result
    │   Step 2: qz = 0.00256·Ke·Kz·...  │
    │   Pressure Profile Table           │  ← z, Kz, qz at each height
    └─────────────────────────────────────┘

  Page 2+:
    ┌─────────────────────────────────────┐
    │ MWFRS — DIRECTIONAL (Ch. 27)       │
    │   Wall Cp Determination            │  ← L/B interpolation trace
    │   Surface Pressure Table           │
    │   Parapet Pressures                │
    ├─────────────────────────────────────┤
    │ MWFRS — LOW-RISE (Ch. 28)          │  ← Or "NOT APPLICABLE" with reason
    │   GCpf by Zone, Cases A & B        │
    ├─────────────────────────────────────┤
    │ C&C PRESSURES (Ch. 30)             │
    │   GCp Log-Linear Interpolation     │  ← Trace for each zone
    │   Zone Pressure Matrix             │
    └─────────────────────────────────────┘
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# Import the engine (adjust path for your project structure)
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "services"))
from engine import (
    calculate_qz,
    calculate_ke,
    calculate_kz,
    calculate_kzt,
    calculate_gust_rigid,
    calculate_mwfrs_directional,
    calculate_mwfrs_lowrise,
    calculate_cc_pressure,
    calculate_cc_parapet,
    linear_interpolate,
    log_linear_interpolate,
    fetch_bounds_from_db,
    _interp_gcp_direct,
    _CC_GCP_TABLES,
    _CC_WALL_GCP,
    _cc_angle_key,
    KztInputs,
    VelocityPressureResult,
    MWFRSDirectionalResult,
    MWFRSLowRiseResult,
    CCPressureResult,
    CCParapetResult,
)


# ============================================================================
# 1. EQUATION TRACE DATA STRUCTURE
# ============================================================================
# This is the "show your work" contract. The engine populates these, and
# the PDF generator renders them. Each trace captures:
#   - The symbolic formula (with ASCE 7 reference)
#   - The substituted values
#   - The computed result
# ============================================================================

@dataclass
class EquationStep:
    """A single equation in the calculation trace."""
    label: str                  # Human-readable step name
    reference: str              # ASCE 7 section/equation (e.g. "Eq. 26.10-1")
    formula_symbolic: str       # e.g. "q<sub>z</sub> = 0.00256 K<sub>e</sub> K<sub>z</sub> ..."
    formula_substituted: str    # e.g. "= 0.00256 × 1.0 × 1.144 × 1.0 × 0.85 × 120²"
    result: str                 # e.g. "= 35.86 psf"
    notes: str = ""             # Optional clarification


@dataclass
class CalculationTrace:
    """Complete trace for an entire calculation block (e.g. velocity pressure)."""
    title: str
    section_ref: str            # e.g. "ASCE 7-22 §26.10"
    steps: list[EquationStep] = field(default_factory=list)


# ============================================================================
# 2. TRACE BUILDERS — Generate EquationSteps from engine results
# ============================================================================

def build_ke_trace(ground_elev: float, code_version: str, ke: float) -> EquationStep:
    edition_year = int("20" + code_version.split("-")[1])
    if edition_year < 2016:
        return EquationStep(
            label="Ground Elevation Factor",
            reference="N/A (pre-ASCE 7-16)",
            formula_symbolic="K<sub>e</sub> = 1.0 (not applicable for this code edition)",
            formula_substituted="",
            result=f"K<sub>e</sub> = {ke:.4f}",
        )
    return EquationStep(
        label="Ground Elevation Factor",
        reference="ASCE 7 §26.9, Eq. 26.9-1",
        formula_symbolic="K<sub>e</sub> = e<super>−0.0000362 z<sub>g</sub></super>",
        formula_substituted=f"= e<super>−0.0000362 × {ground_elev:.0f}</super>",
        result=f"K<sub>e</sub> = <b>{ke:.6f}</b>",
    )


def build_kz_trace(z_ft: float, code_version: str, exposure: str, kz: float, alpha: float, zg: float, zmin: float) -> EquationStep:
    z_eval = max(z_ft, zmin)
    used_zmin = z_ft < zmin
    note = f"z = {z_ft} ft < z<sub>min</sub> = {zmin} ft → using z<sub>min</sub>" if used_zmin else ""
    return EquationStep(
        label=f"Velocity Pressure Exposure Coefficient at z = {z_ft} ft",
        reference="ASCE 7 §26.10.1, Table 26.10-1",
        formula_symbolic="K<sub>z</sub> = 2.01 (z / z<sub>g</sub>)<super>2/α</super>",
        formula_substituted=f"= 2.01 × ({z_eval:.1f} / {zg:.0f})<super>2/{alpha}</super>",
        result=f"K<sub>z</sub> = <b>{kz:.6f}</b>",
        notes=note,
    )


def build_qz_trace(qz_result: VelocityPressureResult) -> EquationStep:
    r = qz_result
    return EquationStep(
        label=f"Velocity Pressure at z = {r.z_ft} ft",
        reference="ASCE 7 §26.10, Eq. 26.10-1",
        formula_symbolic="q<sub>z</sub> = 0.00256 K<sub>e</sub> K<sub>z</sub> K<sub>zt</sub> K<sub>d</sub> V<super>2</super>",
        formula_substituted=(
            f"= 0.00256 × {r.ke:.4f} × {r.kz:.4f} × {r.kzt:.4f} × "
            f"{r.kd:.2f} × {r.V_mph:.0f}<super>2</super>"
        ),
        result=f"q<sub>z</sub> = <b>{r.qz_psf:.4f} psf</b>",
    )


def build_wall_cp_trace(lb_ratio: float, cp_lw: float) -> EquationStep:
    bounds = fetch_bounds_from_db("mwfrs_wall_cp", "", "lb_ratio", lb_ratio, surface="leeward")
    if bounds.is_exact_match:
        sub = f"L/B = {lb_ratio:.3f} → direct table lookup"
    else:
        sub = (
            f"L/B = {lb_ratio:.3f}, interpolating between "
            f"{bounds.bp_lo:.1f} (C<sub>p</sub> = {bounds.val_lo:.2f}) and "
            f"{bounds.bp_hi:.1f} (C<sub>p</sub> = {bounds.val_hi:.2f})"
        )
    return EquationStep(
        label="Leeward Wall External Pressure Coefficient",
        reference="ASCE 7 Figure 27.3-1",
        formula_symbolic="C<sub>p,leeward</sub> = f(L/B) from Figure 27.3-1",
        formula_substituted=sub,
        result=f"C<sub>p,leeward</sub> = <b>{cp_lw:.4f}</b>",
    )


def build_surface_pressure_trace(surface: str, q_psf: float, G: float, cp: float, gcpi: float) -> EquationStep:
    p_pos = q_psf * G * cp + q_psf * abs(gcpi)
    p_neg = q_psf * G * cp - q_psf * abs(gcpi)
    return EquationStep(
        label=f"{surface} Wall Design Pressure",
        reference="ASCE 7 §27.3, Eq. 27.3-1",
        formula_symbolic="p = q<sub>h</sub> G C<sub>p</sub> − q<sub>i</sub>(±GC<sub>pi</sub>)",
        formula_substituted=(
            f"= {q_psf:.2f} × {G:.2f} × ({cp:.4f}) ± {q_psf:.2f} × {abs(gcpi):.2f}"
        ),
        result=f"p = <b>{p_pos:.2f} psf</b> (with +GC<sub>pi</sub>) / <b>{p_neg:.2f} psf</b> (with −GC<sub>pi</sub>)",
    )


def build_cc_gcp_trace(
    zone: str, area: float, gcp: float, sign: str,
    procedure_variant: str = "h_le_60", angle_range: str = "0_to_7",
    roof_type: str = "gable", roof_angle_deg: float = 0.0,
) -> EquationStep:
    """Build an equation trace for a single C&C GCp log-linear interpolation."""
    # Fetch bounding breakpoints for display in the trace
    try:
        if zone in ("4", "5"):
            bpts = _CC_WALL_GCP.get((zone, sign), [])
        else:
            akey = _cc_angle_key(roof_type, roof_angle_deg)
            bpts = _CC_GCP_TABLES.get((akey, zone, sign), [])
        # Find bounding pair
        lo_bp = lo_val = hi_bp = hi_val = None
        for i in range(len(bpts) - 1):
            if bpts[i][0] <= area <= bpts[i + 1][0]:
                lo_bp, lo_val = bpts[i]
                hi_bp, hi_val = bpts[i + 1]
                break
        if lo_bp is not None:
            sub_text = (
                f"log<sub>10</sub>({area}) = {math.log10(area):.4f} "
                f"between [{lo_bp:.0f} sf → {lo_val:.4f}] and [{hi_bp:.0f} sf → {hi_val:.4f}]"
            )
        else:
            sub_text = f"Area = {area} sf (at table boundary)"
    except Exception:
        sub_text = f"Area = {area} sf"

    zone_labels = {
        "1": "Roof Field", "1p": "Roof Interior (1′)",
        "2": "Roof Edge/Eave", "3": "Roof Corner",
        "oh1": "Overhang Zone 1 (GCpi=0)", "oh2": "Overhang Zone 2 (GCpi=0)",
        "oh3": "Overhang Zone 3 (GCpi=0)",
        "4": "Wall Field", "5": "Wall Corner",
    }
    zone_name = zone_labels.get(zone, f"Zone {zone}")
    figure_ref = "ASCE 7 Figure 30.3-2A" if procedure_variant == "h_le_60" else "ASCE 7 Figure 30.4-1"

    return EquationStep(
        label=f"GC<sub>p</sub> — {zone_name} (Zone {zone}), {sign}, A = {area} sf",
        reference=figure_ref,
        formula_symbolic="GC<sub>p</sub> = log-linear interpolation on effective wind area",
        formula_substituted=sub_text,
        result=f"GC<sub>p</sub> = <b>{gcp:.4f}</b>",
    )


def build_cc_pressure_trace(
    zone: str, qh: float, gcp_neg: float, gcp_pos: float, gcpi: float,
    p_neg_pos: float, p_pos_neg: float,
) -> EquationStep:
    """Build an equation trace for the C&C net design pressure formula."""
    zone_labels = {"1": "Roof Field", "2": "Roof Edge/Eave", "3": "Roof Corner",
                   "4": "Wall Field", "5": "Wall Corner"}
    zone_name = zone_labels.get(zone, f"Zone {zone}")
    return EquationStep(
        label=f"Design Pressure — {zone_name} (Zone {zone})",
        reference="ASCE 7 §30.3-1 / §30.4-1",
        formula_symbolic="p = q<sub>h</sub> [(GC<sub>p</sub>) − (±GC<sub>pi</sub>)]",
        formula_substituted=(
            f"Max suction: {qh:.2f} × [({gcp_neg:.4f}) − (+{gcpi:.2f})] = "
            f"{qh:.2f} × {gcp_neg - gcpi:.4f}\n"
            f"Max positive: {qh:.2f} × [({gcp_pos:.4f}) − (−{gcpi:.2f})] = "
            f"{qh:.2f} × {gcp_pos + gcpi:.4f}"
        ),
        result=(
            f"p<sub>max suction</sub> = <b>{p_neg_pos:.2f} psf</b> &nbsp;|&nbsp; "
            f"p<sub>max positive</sub> = <b>{p_pos_neg:.2f} psf</b>"
        ),
    )


# ============================================================================
# 3. PDF STYLE DEFINITIONS
# ============================================================================

# -- Color Palette (engineering-document appropriate) --
CLR_HEADER_BG = colors.HexColor("#1a2332")
CLR_HEADER_TEXT = colors.white
CLR_SECTION_BG = colors.HexColor("#e8edf2")
CLR_TABLE_HEADER = colors.HexColor("#2c3e50")
CLR_TABLE_ALT = colors.HexColor("#f4f6f8")
CLR_BORDER = colors.HexColor("#bdc3c7")
CLR_REFERENCE = colors.HexColor("#2980b9")
CLR_RESULT = colors.HexColor("#1a5276")
CLR_NOTE = colors.HexColor("#7f8c8d")

def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "CalcTitle", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=14, leading=18,
            textColor=CLR_HEADER_BG, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "CalcSubtitle", parent=base["Normal"],
            fontName="Helvetica", fontSize=9, leading=12,
            textColor=CLR_NOTE, spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "SectionHead", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=10, leading=14,
            textColor=CLR_HEADER_BG, spaceBefore=12, spaceAfter=4,
            borderWidth=0, borderColor=CLR_BORDER, borderPadding=4,
        ),
        "ref": ParagraphStyle(
            "CodeRef", parent=base["Normal"],
            fontName="Helvetica-Oblique", fontSize=8, leading=10,
            textColor=CLR_REFERENCE, spaceAfter=2,
        ),
        "formula": ParagraphStyle(
            "Formula", parent=base["Normal"],
            fontName="Courier", fontSize=9, leading=13,
            textColor=colors.black, leftIndent=24, spaceAfter=1,
        ),
        "substitution": ParagraphStyle(
            "Substitution", parent=base["Normal"],
            fontName="Courier", fontSize=8.5, leading=12,
            textColor=colors.HexColor("#555555"), leftIndent=36, spaceAfter=1,
        ),
        "result": ParagraphStyle(
            "Result", parent=base["Normal"],
            fontName="Courier-Bold", fontSize=9, leading=13,
            textColor=CLR_RESULT, leftIndent=36, spaceAfter=6,
        ),
        "note": ParagraphStyle(
            "Note", parent=base["Normal"],
            fontName="Helvetica-Oblique", fontSize=7.5, leading=10,
            textColor=CLR_NOTE, leftIndent=36, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontName="Helvetica", fontSize=9, leading=12,
            textColor=colors.black, spaceAfter=4,
        ),
        "label": ParagraphStyle(
            "Label", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=colors.black, spaceAfter=1,
        ),
        "table_header": ParagraphStyle(
            "TableHeader", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=7.5, leading=10,
            textColor=colors.white, alignment=TA_CENTER,
        ),
        "table_cell": ParagraphStyle(
            "TableCell", parent=base["Normal"],
            fontName="Courier", fontSize=8, leading=10,
            textColor=colors.black, alignment=TA_RIGHT,
        ),
        "table_cell_left": ParagraphStyle(
            "TableCellLeft", parent=base["Normal"],
            fontName="Courier", fontSize=8, leading=10,
            textColor=colors.black, alignment=TA_LEFT,
        ),
    }


# ============================================================================
# 4. PDF PAGE TEMPLATES (Header / Footer)
# ============================================================================

@dataclass
class ProjectInfo:
    """Engineering calculation package header data."""
    project_name: str = "SAMPLE PROJECT"
    job_number: str = "2024-001"
    engineer_initials: str = "MBX"
    checker_initials: str = ""
    company_name: str = "Engineering Firm"
    report_date: str = ""
    sheet_prefix: str = "WIND"

    def __post_init__(self):
        if not self.report_date:
            self.report_date = date.today().strftime("%B %d, %Y")


def _draw_header_footer(canvas, doc, project: ProjectInfo):
    """Draw header and footer on every page."""
    canvas.saveState()
    w, h = letter

    # --- Header ---
    # Top border line
    canvas.setStrokeColor(CLR_HEADER_BG)
    canvas.setLineWidth(2)
    canvas.line(0.75 * inch, h - 0.55 * inch, w - 0.75 * inch, h - 0.55 * inch)

    # Left: Company
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(CLR_HEADER_BG)
    canvas.drawString(0.75 * inch, h - 0.50 * inch, project.company_name.upper())

    # Center: Project
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawCentredString(w / 2, h - 0.50 * inch, project.project_name.upper())

    # Right block
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(CLR_NOTE)
    right_x = w - 0.75 * inch
    canvas.drawRightString(right_x, h - 0.38 * inch, f"Job: {project.job_number}")
    canvas.drawRightString(right_x, h - 0.50 * inch, f"Date: {project.report_date}")

    # Second header line: engineer info
    canvas.setFont("Helvetica", 7)
    y2 = h - 0.68 * inch
    canvas.drawString(0.75 * inch, y2, f"Calc By: {project.engineer_initials}")
    if project.checker_initials:
        canvas.drawString(2.0 * inch, y2, f"Checked By: {project.checker_initials}")

    canvas.setStrokeColor(CLR_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, y2 - 4, w - 0.75 * inch, y2 - 4)

    # --- Footer ---
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(CLR_NOTE)
    canvas.drawString(0.75 * inch, 0.45 * inch, f"{project.sheet_prefix} — ASCE 7 Wind Load Calculations")
    canvas.drawCentredString(w / 2, 0.45 * inch, f"Page {doc.page}")
    canvas.drawRightString(right_x, 0.45 * inch, "Generated by Wind Load Suite v1.0")

    canvas.setStrokeColor(CLR_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.58 * inch, w - 0.75 * inch, 0.58 * inch)

    canvas.restoreState()


# ============================================================================
# 5. CONTENT BUILDERS — Flowables for each report section
# ============================================================================

def _build_equation_block(trace: CalculationTrace, styles: dict) -> list:
    """Convert a CalculationTrace into ReportLab flowables."""
    elements = []
    elements.append(Paragraph(trace.title, styles["section"]))
    elements.append(Paragraph(trace.section_ref, styles["ref"]))
    elements.append(Spacer(1, 4))

    for step in trace.steps:
        elements.append(Paragraph(f"<b>{step.label}</b>", styles["label"]))
        if step.reference and step.reference != trace.section_ref:
            elements.append(Paragraph(step.reference, styles["ref"]))
        elements.append(Paragraph(step.formula_symbolic, styles["formula"]))
        if step.formula_substituted:
            elements.append(Paragraph(step.formula_substituted, styles["substitution"]))
        elements.append(Paragraph(step.result, styles["result"]))
        if step.notes:
            elements.append(Paragraph(step.notes, styles["note"]))

    return elements


def _build_criteria_table(project, geometry, exposure, enclosure, ke, kzt, kd, G, gcpi, code_version, styles) -> list:
    """Build the Global Wind Criteria summary table."""
    elements = []
    elements.append(Paragraph("Global Wind Design Criteria", styles["section"]))

    data = [
        ["Parameter", "Value", "Parameter", "Value"],
        ["Building Code", code_version, "Exposure Category", exposure],
        [Paragraph("Wind Speed (V)", styles["table_cell_left"]),
         Paragraph(f"<b>{project['V_mph']}</b> mph", styles["table_cell"]),
         "Risk Category",
         project["risk_category"]],
        ["Enclosure", enclosure.replace("_", " ").title(),
         Paragraph("GC<sub>pi</sub>", styles["table_cell_left"]),
         Paragraph(f"±{gcpi}", styles["table_cell"])],
        ["Length (L)", f"{geometry['L_ft']} ft", "Width (B)", f"{geometry['B_ft']} ft"],
        [Paragraph("Mean Roof Ht (h)", styles["table_cell_left"]),
         f"{geometry['h_ft']} ft",
         Paragraph("Roof Angle (θ)", styles["table_cell_left"]),
         f"{geometry['roof_angle_deg']}°"],
        [Paragraph("K<sub>e</sub>", styles["table_cell_left"]),
         f"{ke:.6f}",
         Paragraph("K<sub>zt</sub>", styles["table_cell_left"]),
         f"{kzt:.4f}"],
        [Paragraph("K<sub>d</sub>", styles["table_cell_left"]),
         f"{kd}",
         "G (Gust Factor)", f"{G:.4f}"],
    ]

    col_widths = [1.7 * inch, 1.5 * inch, 1.7 * inch, 1.5 * inch]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Courier"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8))
    return elements


def _build_pressure_profile_table(pressures: list[dict], styles: dict) -> list:
    """Build the qz profile table."""
    elements = []
    elements.append(Paragraph("Velocity Pressure Profile", styles["label"]))

    header = ["z (ft)", Paragraph("K<sub>z</sub>", styles["table_header"]),
              Paragraph("K<sub>zt</sub>", styles["table_header"]),
              Paragraph("K<sub>e</sub>", styles["table_header"]),
              Paragraph("q<sub>z</sub> (psf)", styles["table_header"])]
    data = [header]
    for p in pressures:
        data.append([
            f"{p['z_ft']:.1f}",
            f"{p['kz']:.4f}",
            f"{p['kzt']:.4f}",
            f"{p['ke']:.4f}",
            f"{p['qz_psf']:.2f}",
        ])

    col_widths = [1.0 * inch, 1.1 * inch, 1.0 * inch, 1.0 * inch, 1.2 * inch]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Courier"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(t)
    return elements


def _build_mwfrs_section(mwfrs_result, styles) -> list:
    """Build the MWFRS Directional Procedure section."""
    elements = []
    r = mwfrs_result
    elements.append(Paragraph("MWFRS — Directional Procedure (Chapter 27)", styles["section"]))
    elements.append(Paragraph("ASCE 7 §27.3, Eq. 27.3-1", styles["ref"]))

    # Wall Cp summary
    data = [
        ["Surface", Paragraph("C<sub>p</sub>", styles["table_header"]), "p (+GCpi)", "p (−GCpi)"],
        ["Windward Wall", f"{r['cp_windward_wall']:.2f}", "varies by z", "varies by z"],
        ["Leeward Wall", f"{r['cp_leeward_wall']:.4f}",
         f"{r['p_leeward_wall_pos']:.2f} psf", f"{r['p_leeward_wall_neg']:.2f} psf"],
        ["Side Wall", f"{r['cp_side_wall']:.2f}",
         f"{r['p_side_wall_pos']:.2f} psf", f"{r['p_side_wall_neg']:.2f} psf"],
    ]
    t = Table(data, colWidths=[1.5*inch, 1.0*inch, 1.5*inch, 1.5*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Courier"),
        ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 6))

    # Parapet
    elements.append(Paragraph(
        f"Parapet Pressures (§26.10.3): Windward = <b>{r['parapet_windward_psf']:.2f} psf</b>, "
        f"Leeward = <b>{r['parapet_leeward_psf']:.2f} psf</b>",
        styles["body"],
    ))

    # Windward wall profile
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Windward Wall Pressure Profile", styles["label"]))
    header = ["z (ft)", Paragraph("K<sub>z</sub>", styles["table_header"]),
              Paragraph("q<sub>z</sub> (psf)", styles["table_header"]),
              "p (+GCpi)", "p (−GCpi)"]
    rows = [header]
    for row in r["p_windward_wall"]:
        rows.append([
            f"{row['z_ft']:.1f}", f"{row['kz']:.4f}", f"{row['qz_psf']:.2f}",
            f"{row['p_with_neg_gcpi']:.2f}", f"{row['p_with_pos_gcpi']:.2f}",
        ])
    t = Table(rows, colWidths=[0.9*inch, 1.0*inch, 1.1*inch, 1.2*inch, 1.2*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Courier"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(t)
    return elements


def _build_lowrise_section(lr_result, styles) -> list:
    """Build MWFRS Low-Rise section (or N/A notice)."""
    elements = []
    elements.append(Paragraph("MWFRS — Low-Rise Envelope Procedure (Chapter 28)", styles["section"]))
    r = lr_result

    if not r["is_applicable"]:
        elements.append(Paragraph(
            f"<b>NOT APPLICABLE</b> — {r['inapplicable_reason']}",
            ParagraphStyle("NA", parent=styles["body"], textColor=colors.HexColor("#c0392b"),
                           fontName="Helvetica-Bold", fontSize=9),
        ))
        elements.append(Paragraph(
            "The low-rise procedure requires h ≤ 60 ft and h ≤ B. "
            "Use the Directional Procedure (Ch. 27) above.",
            styles["note"],
        ))
        return elements

    elements.append(Paragraph(f"ASCE 7 §28.3, Figure 28.3-1", styles["ref"]))
    elements.append(Paragraph(
        f"q<sub>h</sub> = {r['qh_psf']:.2f} psf | End zone width (2a) = {r['end_zone_width_ft']:.1f} ft",
        styles["body"],
    ))

    for case_label, case_data in [("Case A — Transverse", r["case_a"]), ("Case B — Longitudinal", r["case_b"])]:
        if not case_data:
            continue
        elements.append(Paragraph(case_label, styles["label"]))
        header = ["Zone", Paragraph("GC<sub>pf</sub>", styles["table_header"]),
                  "p (+GCpi) psf", "p (−GCpi) psf"]
        rows = [header]
        # case_data is a dict: {zone_str: {gcpf, p_with_neg_gcpi, p_with_pos_gcpi}}
        if isinstance(case_data, dict):
            items = [(zone_key, vals) for zone_key, vals in case_data.items()]
        else:
            items = [(z["zone"], z) for z in case_data]
        for zone_key, vals in items:
            rows.append([zone_key, f"{vals['gcpf']:.4f}",
                         f"{vals['p_with_neg_gcpi']:.2f}", f"{vals['p_with_pos_gcpi']:.2f}"])
        t = Table(rows, colWidths=[0.8*inch, 1.2*inch, 1.5*inch, 1.5*inch], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 1), (-1, -1), "Courier"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 4))

    return elements


def _build_cc_section(
    cc_results: list[CCPressureResult],
    cc_traces: CalculationTrace,
    qh: float,
    gcpi: float,
    zone_a_ft: float,
    procedure_variant: str,
    angle_range: str,
    styles: dict,
) -> list:
    """Build the complete C&C section: equation traces + zone pressure matrix."""
    elements = []

    proc_label = "h ≤ 60 ft (§30.3)" if procedure_variant == "h_le_60" else "h > 60 ft (§30.4)"
    angle_label = angle_range.replace("_to_", "° to ").replace("0_", "0").rstrip("_") + "°"

    elements.append(Paragraph(
        f"Components &amp; Cladding — {proc_label}",
        styles["section"],
    ))
    elements.append(Paragraph(
        f"ASCE 7 {('Figure 30.3-1' if procedure_variant == 'h_le_60' else 'Figure 30.4-1')}"
        f" | Roof angle range: {angle_label}",
        styles["ref"],
    ))
    elements.append(Spacer(1, 2))

    # Zone dimension and parameters
    elements.append(Paragraph(
        f"q<sub>h</sub> = {qh:.2f} psf &nbsp;|&nbsp; "
        f"GC<sub>pi</sub> = ±{gcpi:.2f} &nbsp;|&nbsp; "
        f"Zone dimension a = {zone_a_ft:.1f} ft &nbsp;|&nbsp; "
        f"Minimum pressure = 16.0 psf",
        styles["body"],
    ))
    elements.append(Spacer(1, 4))

    # GCp interpolation traces (show one representative per zone)
    if cc_traces and cc_traces.steps:
        elements.append(Paragraph("GC<sub>p</sub> Coefficient Determination", styles["label"]))
        elements.append(Paragraph(cc_traces.section_ref, styles["ref"]))
        for step in cc_traces.steps:
            elements.append(Paragraph(f"<b>{step.label}</b>", styles["label"]))
            elements.append(Paragraph(step.formula_symbolic, styles["formula"]))
            if step.formula_substituted:
                elements.append(Paragraph(step.formula_substituted, styles["substitution"]))
            elements.append(Paragraph(step.result, styles["result"]))
        elements.append(Spacer(1, 6))

    # --- Zone pressure matrix grouped by surface type ---
    zone_labels = {
        "1":   "1 (Field)", "1p": "1′ (Interior)",
        "2":   "2 (Edge/Eave)", "3": "3 (Corner)",
        "oh1": "OH-1 (OH Field)", "oh2": "OH-2 (OH Edge)", "oh3": "OH-3 (OH Corner)",
        "4":   "4 (Wall Field)", "5":  "5 (Wall Corner)",
    }

    for surface_label, zone_ids in [
        ("Roof Pressures — Standard Zones", ["1", "1p", "2", "3"]),
        ("Roof Overhang Pressures (GC<sub>pi</sub>=0 per §30.6)", ["oh1", "oh2", "oh3"]),
        ("Wall Pressures", ["4", "5"]),
    ]:
        zone_results = [r for r in cc_results if r.zone in zone_ids]
        if not zone_results:
            continue

        elements.append(Paragraph(surface_label, styles["label"]))

        header = [
            "Zone",
            Paragraph("Area<br/>(sf)", styles["table_header"]),
            Paragraph("GC<sub>p</sub>(−)", styles["table_header"]),
            Paragraph("GC<sub>p</sub>(+)", styles["table_header"]),
            Paragraph("Max Suction<br/>(psf)", styles["table_header"]),
            Paragraph("Max Positive<br/>(psf)", styles["table_header"]),
        ]
        data = [header]

        for r in zone_results:
            data.append([
                zone_labels.get(r.zone, r.zone),
                f"{r.eff_wind_area_sf:.0f}",
                f"{r.gcp_negative:.3f}",
                f"{r.gcp_positive:.3f}",
                f"{r.p_neg_with_pos_gcpi:.2f}",
                f"{r.p_pos_with_neg_gcpi:.2f}",
            ])

        col_widths = [1.0*inch, 0.7*inch, 0.9*inch, 0.9*inch, 1.1*inch, 1.1*inch]
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("FONTNAME", (0, 1), (-1, -1), "Courier"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 6))

    # Minimum pressure note
    elements.append(Paragraph(
        "<i>Note: All pressures are subject to a minimum magnitude of 16 psf per §30.2.2. "
        "Negative values indicate suction (away from surface); positive values indicate "
        "pressure (toward surface).</i>",
        styles["note"],
    ))

    return elements


def _build_cc_parapet_section(
    parapet_results: list[CCParapetResult],
    qh: float,
    styles: dict,
) -> list:
    """Build C&C solid parapet §30.9 section."""
    elements = []
    elements.append(Paragraph("C&amp;C — Solid Parapet Pressures (§30.9)", styles["section"]))
    elements.append(Paragraph(
        f"ASCE 7 §30.9, Figure 30.9-1 | q<sub>h</sub> = {qh:.2f} psf | "
        f"p = q<sub>h</sub> × GC<sub>pn</sub> (no GC<sub>pi</sub> term)",
        styles["ref"],
    ))
    elements.append(Paragraph(
        "Case A: Positive GCpn on windward parapet face. "
        "Case B Interior/Corner: Negative GCpn on leeward parapet face.",
        styles["note"],
    ))

    header = [
        Paragraph("Area<br/>(sf)", styles["table_header"]),
        Paragraph("GC<sub>pn</sub><br/>Case A", styles["table_header"]),
        Paragraph("GC<sub>pn</sub><br/>Case B Int.", styles["table_header"]),
        Paragraph("GC<sub>pn</sub><br/>Case B Cor.", styles["table_header"]),
        Paragraph("p Case A<br/>(psf)", styles["table_header"]),
        Paragraph("p B Int.<br/>(psf)", styles["table_header"]),
        Paragraph("p B Cor.<br/>(psf)", styles["table_header"]),
    ]
    data = [header]
    for pr in parapet_results:
        data.append([
            f"{pr.eff_wind_area_sf:.0f}",
            f"{pr.gcpn_case_a:.4f}",
            f"{pr.gcpn_case_b_interior:.4f}",
            f"{pr.gcpn_case_b_corner:.4f}",
            f"{pr.p_case_a_psf:.2f}",
            f"{pr.p_case_b_int_psf:.2f}",
            f"{pr.p_case_b_cor_psf:.2f}",
        ])

    col_widths = [0.7*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.9*inch]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("FONTNAME", (0, 1), (-1, -1), "Courier"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(t)
    return elements


def _build_simple_diaphragm_section(sd: dict, styles: dict) -> list:
    """Build MWFRS Horizontal Simple Diaphragm §28.4 section."""
    elements = []
    elements.append(Paragraph(
        "MWFRS — Horizontal Simple Diaphragm Method (§28.4)", styles["section"]
    ))
    elements.append(Paragraph(
        f"ASCE 7 §28.4 | Zone dimension a = {sd['a_ft']:.1f} ft | "
        f"End zone width (2a) = {sd['end_zone_2a_ft']:.1f} ft",
        styles["ref"],
    ))
    elements.append(Paragraph(
        "Net horizontal pressures for simple diaphragm buildings: "
        "walls use Case B (Zones 5−6 / 5E−6E); roof uses Case A (Zones 2−3 / 2E−3E).",
        styles["note"],
    ))

    header = ["Direction", "Surface", "Interior Zone (psf)", "End Zone (psf)"]
    data = [header,
        ["Transverse", "Wall",
         f"{sd['int_wall_transverse']:.1f}", f"{sd['end_wall_transverse']:.1f}"],
        ["Transverse", "Roof",
         f"{sd['int_roof_transverse']:.1f}", f"{sd['end_roof_transverse']:.1f}"],
        ["Longitudinal", "Wall",
         f"{sd['int_wall_longitudinal']:.1f}", f"{sd['end_wall_longitudinal']:.1f}"],
    ]
    t = Table(data, colWidths=[1.4*inch, 1.0*inch, 1.8*inch, 1.8*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CLR_TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Courier"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, CLR_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLR_TABLE_ALT]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(t)
    elements.append(Paragraph(
        "<i>Note: Wall pressures subject to minimum of 16 psf per §28.4.4.</i>",
        styles["note"],
    ))
    return elements


# ============================================================================
# 6. MASTER PDF GENERATOR
# ============================================================================

def generate_calculation_package(
    project_info: ProjectInfo,
    project: dict,
    geometry: dict,
    code_version: str = "7-22",
    exposure: str = "C",
    enclosure: str = "enclosed",
    kd: float = 0.85,
    ground_elevation_ft: float = 0.0,
) -> bytes:
    """
    Generate the complete PE Calculation Package as a PDF.

    Returns raw PDF bytes suitable for:
      - FastAPI StreamingResponse
      - Writing to disk
      - Attaching to an email

    Parameters
    ----------
    project_info : ProjectInfo
        Header block data (company, job #, engineer initials).
    project : dict
        WindProjectSetup-like dict with V_mph, risk_category, etc.
    geometry : dict
        BuildingGeometry-like dict with L_ft, B_ft, h_ft, roof_angle_deg.
    """
    buf = BytesIO()
    styles = _build_styles()

    # --- Compute all results ---
    ke = calculate_ke(ground_elevation_ft, code_version)
    kzt = calculate_kzt(KztInputs(
        topo_type="flat", hill_height_ft=0, half_hill_length_ft=0,
        dist_from_crest_ft=0, upwind=True, z_ft=geometry["h_ft"], exposure=exposure,
    ))
    G = max(calculate_gust_rigid(geometry["h_ft"], geometry["B_ft"], exposure, code_version), 0.85)
    gcpi_map = {"enclosed": 0.18, "partially_enclosed": 0.55, "open": 0.0, "partially_open": 0.18}
    gcpi = gcpi_map.get(enclosure, 0.18)

    # qz at h
    qh_result = calculate_qz(
        project["V_mph"], exposure, geometry["h_ft"], kzt, ke, kd, code_version
    )

    # Build default z profile
    std = [15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 120, 140, 160, 180, 200, 250, 300]
    heights = [z for z in std if z <= geometry["h_ft"]]
    if not heights or heights[-1] < geometry["h_ft"]:
        heights.append(geometry["h_ft"])

    qz_results = [
        calculate_qz(project["V_mph"], exposure, z, kzt, ke, kd, code_version)
        for z in heights
    ]

    # MWFRS
    parapet_ht = geometry.get("parapet_height_ft", 0.0)
    mwfrs_dir = calculate_mwfrs_directional(
        project["V_mph"], exposure, geometry["h_ft"], geometry["L_ft"], geometry["B_ft"],
        kzt, ke, kd, G, gcpi, code_version,
        parapet_height_ft=parapet_ht,
    )
    mwfrs_lr = calculate_mwfrs_lowrise(
        project["V_mph"], exposure, geometry["h_ft"], geometry["B_ft"], geometry["L_ft"],
        kzt, ke, kd, gcpi, geometry.get("roof_angle_deg", 0), code_version,
    )

    # C&C
    roof_type = geometry.get("roof_type", "gable")
    roof_angle = geometry.get("roof_angle_deg", 0.0)
    procedure_variant = "h_le_60" if geometry["h_ft"] <= 60 else "h_gt_60"
    angle_range = _cc_angle_key(roof_type, roof_angle)  # Use new key for display
    # Full zone list including 1', overhangs; walls separate
    cc_roof_zones = ["1", "1p", "2", "3", "oh1", "oh2", "oh3"]
    cc_wall_zones = ["4", "5"]
    cc_all_zones  = cc_roof_zones + cc_wall_zones
    cc_areas = [10, 20, 50, 100, 200, 500]
    cc_zone_a = max(min(0.1 * min(geometry["L_ft"], geometry["B_ft"]),
                        0.4 * geometry["h_ft"]), 3.0)

    cc_results: list[CCPressureResult] = []
    for zone in cc_all_zones:
        for area in cc_areas:
            try:
                r = calculate_cc_pressure(
                    qh_psf=qh_result.qz_psf, eff_wind_area_sf=area,
                    zone=zone, gcpi=gcpi, code_version=code_version,
                    procedure_variant=procedure_variant,
                    roof_type=roof_type, roof_angle_deg=roof_angle,
                )
                cc_results.append(r)
            except ValueError:
                continue

    # C&C Solid Parapet §30.9
    cc_parapet_results: list[CCParapetResult] = []
    if parapet_ht > 0:
        for area in cc_areas:
            try:
                cc_parapet_results.append(
                    calculate_cc_parapet(qh_result.qz_psf, area, code_version)
                )
            except ValueError:
                continue

    # --- Build equation traces ---
    ke_trace = build_ke_trace(ground_elevation_ft, code_version, ke)
    kz_trace = build_kz_trace(
        geometry["h_ft"], code_version, exposure,
        qh_result.kz, qh_result.alpha, qh_result.zg_ft, qh_result.z_min_ft,
    )
    qz_trace = build_qz_trace(qh_result)

    velocity_calc = CalculationTrace(
        title="Velocity Pressure Determination",
        section_ref=f"ASCE {code_version} §26.10",
        steps=[ke_trace, kz_trace, qz_trace],
    )

    # Wall Cp trace
    lb_ratio = geometry["L_ft"] / geometry["B_ft"]
    cp_lw_trace = build_wall_cp_trace(lb_ratio, mwfrs_dir.cp_leeward_wall)
    lw_pressure_trace = build_surface_pressure_trace(
        "Leeward", qh_result.qz_psf, G, mwfrs_dir.cp_leeward_wall, gcpi,
    )
    mwfrs_calc = CalculationTrace(
        title="MWFRS Wall Pressure Coefficients",
        section_ref=f"ASCE {code_version} §27.3",
        steps=[cp_lw_trace, lw_pressure_trace],
    )

    # C&C GCp traces — one representative interpolation for key zones (1, 2, 3, 4, 5)
    cc_trace_steps: list[EquationStep] = []
    representative_area = 50.0
    for zone in ["1", "2", "3", "4", "5"]:  # Only main zones in traces (keeps PDF concise)
        zone_at_area = [r for r in cc_results
                        if r.zone == zone and math.isclose(r.eff_wind_area_sf, representative_area)]
        if not zone_at_area:
            zone_at_area = [r for r in cc_results if r.zone == zone]
        if zone_at_area:
            r = zone_at_area[0]
            cc_trace_steps.append(build_cc_gcp_trace(
                zone=r.zone, area=r.eff_wind_area_sf, gcp=r.gcp_negative,
                sign="negative", procedure_variant=procedure_variant,
                angle_range=angle_range,
                roof_type=roof_type, roof_angle_deg=roof_angle,
            ))
            cc_trace_steps.append(build_cc_pressure_trace(
                zone=r.zone, qh=r.qh_psf,
                gcp_neg=r.gcp_negative, gcp_pos=r.gcp_positive,
                gcpi=gcpi, p_neg_pos=r.p_neg_with_pos_gcpi,
                p_pos_neg=r.p_pos_with_neg_gcpi,
            ))

    cc_calc = CalculationTrace(
        title="C&C Coefficient Determination",
        section_ref=(f"ASCE {code_version} "
                     f"{'§30.3' if procedure_variant == 'h_le_60' else '§30.4'}"),
        steps=cc_trace_steps,
    )

    # --- Assemble document ---
    doc = BaseDocTemplate(buf, pagesize=letter,
                          leftMargin=0.75*inch, rightMargin=0.75*inch,
                          topMargin=0.85*inch, bottomMargin=0.7*inch)

    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="main",
    )

    def on_page(canvas, doc_inner):
        _draw_header_footer(canvas, doc_inner, project_info)

    doc.addPageTemplates([
        PageTemplate(id="all_pages", frames=[frame], onPage=on_page),
    ])

    story = []

    # Title block
    story.append(Paragraph("STRUCTURAL WIND LOAD CALCULATIONS", styles["title"]))
    story.append(Paragraph(
        f"{project_info.project_name} | Job #{project_info.job_number} | {project_info.report_date}",
        styles["subtitle"],
    ))
    story.append(Spacer(1, 6))

    # Global Criteria Table
    story.extend(_build_criteria_table(
        project, geometry, exposure, enclosure, ke, kzt, kd, G, gcpi, code_version, styles,
    ))

    # Velocity Pressure — Equation Trace
    story.extend(_build_equation_block(velocity_calc, styles))
    story.append(Spacer(1, 6))

    # Velocity Pressure — Profile Table
    profile_data = [
        {"z_ft": r.z_ft, "kz": r.kz, "kzt": r.kzt, "ke": r.ke, "qz_psf": r.qz_psf}
        for r in qz_results
    ]
    story.extend(_build_pressure_profile_table(profile_data, styles))
    story.append(Spacer(1, 8))

    # MWFRS Directional — Equation Trace + Tables
    story.extend(_build_equation_block(mwfrs_calc, styles))
    story.append(Spacer(1, 4))
    from dataclasses import asdict
    story.extend(_build_mwfrs_section(asdict(mwfrs_dir), styles))

    # MWFRS Low-Rise
    story.append(Spacer(1, 4))
    story.extend(_build_lowrise_section(asdict(mwfrs_lr), styles))

    # C&C Pressures
    story.append(Spacer(1, 4))
    story.extend(_build_cc_section(
        cc_results=cc_results,
        cc_traces=cc_calc,
        qh=qh_result.qz_psf,
        gcpi=gcpi,
        zone_a_ft=cc_zone_a,
        procedure_variant=procedure_variant,
        angle_range=angle_range,
        styles=styles,
    ))

    # C&C Solid Parapet §30.9
    if cc_parapet_results:
        story.append(Spacer(1, 4))
        story.extend(_build_cc_parapet_section(cc_parapet_results, qh_result.qz_psf, styles))

    # Simple Diaphragm §28.4
    if mwfrs_lr.is_applicable and mwfrs_lr.simple_diaphragm is not None:
        story.append(Spacer(1, 4))
        from dataclasses import asdict as _asdict
        story.extend(_build_simple_diaphragm_section(
            _asdict(mwfrs_lr.simple_diaphragm), styles
        ))

    # Build
    doc.build(story)
    return buf.getvalue()


# ============================================================================
# 7. SELF-TEST
# ============================================================================

if __name__ == "__main__":
    print("Generating PE Calculation Package PDF...")

    pdf_bytes = generate_calculation_package(
        project_info=ProjectInfo(
            project_name="WAREHOUSE EXPANSION — PHASE II",
            job_number="2024-W-0147",
            engineer_initials="MBX",
            checker_initials="JKR",
            company_name="Structural Solutions LLC",
        ),
        project={
            "V_mph": 120,
            "risk_category": "II",
            "code_version": "7-22",
        },
        geometry={
            "L_ft": 120,
            "B_ft": 80,
            "h_ft": 35,
            "roof_angle_deg": 4.0,
        },
        code_version="7-22",
        exposure="C",
        enclosure="enclosed",
        kd=0.85,
        ground_elevation_ft=500,
    )

    output_path = "/mnt/user-data/outputs/PE_Wind_Calculation_Package.pdf"
    with open(output_path, "wb") as f:
        f.write(pdf_bytes)

    print(f"PDF generated: {output_path} ({len(pdf_bytes):,} bytes)")
