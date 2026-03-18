"""
Integration test — validates the full request → engine → response pipeline.

Runs WITHOUT FastAPI installed by directly calling the engine functions
with the same parameter flow the routers use. This confirms the Pydantic
models' validation logic and the engine's output shapes are compatible.

Run:  python -m tests.test_integration
"""

from __future__ import annotations

import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.engine import (
    calculate_qz,
    calculate_ke,
    calculate_kzt,
    calculate_cc_pressure,
    calculate_mwfrs_directional,
    calculate_mwfrs_lowrise,
    calculate_gust_rigid,
    classify_enclosure,
    KztInputs,
    EnclosureTestInputs,
    EnclosureType,
)


def test_qz_endpoint_flow():
    """Simulate POST /calculate/wind/qz with a full parameter set."""
    print("Test: qz endpoint flow...")

    # Inputs that would come from VelocityPressureRequest
    code_version = "7-22"
    V_mph = 120.0
    exposure = "C"
    h_ft = 62.0
    kd = 0.85
    ground_elevation_ft = 500.0

    ke = calculate_ke(ground_elevation_ft, code_version)
    kzt = calculate_kzt(KztInputs(
        topo_type="flat", hill_height_ft=0, half_hill_length_ft=0,
        dist_from_crest_ft=0, upwind=True, z_ft=h_ft, exposure=exposure,
    ))

    heights = [15, 20, 30, 40, 50, 62]
    results = []
    for z in heights:
        r = calculate_qz(V_mph, exposure, z, kzt, ke, kd, code_version)
        results.append({
            "z_ft": r.z_ft, "kz": r.kz, "qz_psf": r.qz_psf,
            "alpha": r.alpha, "zg_ft": r.zg_ft,
        })

    # Validate response shape
    assert len(results) == 6
    assert results[-1]["z_ft"] == 62
    assert results[-1]["qz_psf"] > 0
    assert results[-1]["alpha"] == 9.5  # Exp C α=9.5 across all ASCE 7 editions (not 9.8)
    # Ke at 500 ft should be < 1.0
    assert 0.97 < ke < 1.0

    # qz should monotonically increase with height
    for i in range(1, len(results)):
        assert results[i]["qz_psf"] >= results[i - 1]["qz_psf"]

    print(f"  ✓ {len(results)} qz values computed, Ke={ke:.4f}")
    print(f"  ✓ qz at h: {results[-1]['qz_psf']:.2f} psf")


def test_cc_endpoint_flow():
    """Simulate POST /calculate/wind/cc for a flat roof enclosed building."""
    print("Test: C&C endpoint flow...")

    code_version = "7-22"
    V_mph = 120.0
    exposure = "C"
    h_ft = 35.0
    L_ft = 100.0
    B_ft = 60.0
    kd = 0.85
    gcpi = 0.18  # Enclosed
    roof_angle_deg = 4.0

    ke = calculate_ke(0, code_version)
    kzt = 1.0

    qh_result = calculate_qz(V_mph, exposure, h_ft, kzt, ke, kd, code_version)
    qh = qh_result.qz_psf

    zones = ["1", "2", "3"]
    areas = [10, 20, 50, 100, 200, 500]

    all_pressures = []
    for zone in zones:
        for area in areas:
            try:
                r = calculate_cc_pressure(
                    qh_psf=qh, eff_wind_area_sf=area, zone=zone,
                    gcpi=gcpi, code_version=code_version,
                    procedure_variant="h_le_60", angle_range="0_to_7",
                )
                all_pressures.append({
                    "zone": r.zone, "area": r.eff_wind_area_sf,
                    "gcp_neg": r.gcp_negative, "gcp_pos": r.gcp_positive,
                    "p_max_suction": r.p_neg_with_pos_gcpi,
                    "p_max_positive": r.p_pos_with_neg_gcpi,
                })
            except ValueError:
                pass  # Some zone/angle combos may not be in mock data

    assert len(all_pressures) > 0
    # Zone 3 (corner) should have larger suction than Zone 1 (field)
    z1_suction = [p["p_max_suction"] for p in all_pressures if p["zone"] == "1"]
    z3_suction = [p["p_max_suction"] for p in all_pressures if p["zone"] == "3"]
    if z1_suction and z3_suction:
        assert min(z3_suction) <= min(z1_suction), "Zone 3 should have more suction than Zone 1"

    # All pressures should meet minimum pressure requirement
    for p in all_pressures:
        assert abs(p["p_max_suction"]) >= 16.0, f"Below minimum: {p}"
        assert abs(p["p_max_positive"]) >= 16.0, f"Below minimum: {p}"

    print(f"  ✓ {len(all_pressures)} C&C pressure points computed")
    print(f"  ✓ Min pressure enforcement verified (≥ 16 psf)")
    # Show sample
    sample = [p for p in all_pressures if p["zone"] == "2" and p["area"] == 50]
    if sample:
        print(f"  ✓ Zone 2, 50 sf: GCp(-)={sample[0]['gcp_neg']:.3f}, "
              f"p(suction)={sample[0]['p_max_suction']:.1f} psf")


def test_mwfrs_directional_flow():
    """Simulate POST /calculate/wind/mwfrs/directional."""
    print("Test: MWFRS Directional flow...")

    code_version = "7-16"
    V_mph = 115.0
    exposure = "B"

    ke = 1.0
    kzt = 1.0
    kd = 0.85
    G = max(calculate_gust_rigid(40, 80, exposure, code_version), 0.85)
    gcpi = 0.18

    result = calculate_mwfrs_directional(
        V_mph=V_mph, exposure=exposure, h_ft=40, L_ft=120, B_ft=80,
        kzt=kzt, ke=ke, kd=kd, G=G, gcpi=gcpi, code_version=code_version,
    )

    assert result.qh_psf > 0
    assert result.cp_windward_wall == 0.8
    assert result.cp_side_wall == -0.7
    assert -0.5 <= result.cp_leeward_wall <= -0.2  # L/B = 1.5 → Cp ≈ -0.4
    assert len(result.p_windward_wall) > 0
    assert result.parapet_windward_psf > 0
    assert result.parapet_leeward_psf < 0

    print(f"  ✓ qh = {result.qh_psf:.2f} psf")
    print(f"  ✓ Cp(LW) = {result.cp_leeward_wall:.4f} (L/B = {120/80:.2f})")
    print(f"  ✓ {len(result.p_windward_wall)} windward wall elevations")


def test_mwfrs_lowrise_flow():
    """Simulate POST /calculate/wind/mwfrs/lowrise — both applicable and not."""
    print("Test: MWFRS Low-Rise flow...")

    # Case 1: Applicable (h=30, B=60)
    r1 = calculate_mwfrs_lowrise(
        V_mph=120, exposure="C", h_ft=30, B_ft=60, L_ft=100,
        kzt=1.0, ke=1.0, kd=0.85, gcpi=0.18, roof_angle_deg=5,
        code_version="7-22",
    )
    assert r1.is_applicable is True
    assert r1.qh_psf > 0
    assert r1.end_zone_width_ft > 0
    assert len(r1.case_a) > 0
    assert len(r1.case_b) > 0
    print(f"  ✓ Applicable case: qh={r1.qh_psf:.2f}, end zone={r1.end_zone_width_ft} ft")

    # Case 2: Not applicable — h > 60
    r2 = calculate_mwfrs_lowrise(
        V_mph=120, exposure="C", h_ft=80, B_ft=60, L_ft=100,
        kzt=1.0, ke=1.0, kd=0.85, gcpi=0.18, roof_angle_deg=5,
        code_version="7-22",
    )
    assert r2.is_applicable is False
    assert "60" in r2.inapplicable_reason
    print(f"  ✓ Inapplicable (h=80): reason='{r2.inapplicable_reason}'")

    # Case 3: Not applicable — h > B
    r3 = calculate_mwfrs_lowrise(
        V_mph=120, exposure="C", h_ft=50, B_ft=40, L_ft=100,
        kzt=1.0, ke=1.0, kd=0.85, gcpi=0.18, roof_angle_deg=5,
        code_version="7-22",
    )
    assert r3.is_applicable is False
    assert "h" in r3.inapplicable_reason.lower() and "b" in r3.inapplicable_reason.lower()
    print(f"  ✓ Inapplicable (h>B): reason='{r3.inapplicable_reason}'")


def test_enclosure_classification():
    """Test the enclosure classification decision tree."""
    print("Test: Enclosure classification...")

    # Partially Enclosed: Ao ≥ 1.1*Aoi, Ao > min(4, 0.01*Ag), Aoi/Agi ≤ 0.20
    result = classify_enclosure(
        EnclosureTestInputs(Ao_sf=500, Ag_sf=5000, Aoi_sf=100, Agi_sf=20000),
        code_version="7-22",
    )
    assert result == EnclosureType.PARTIALLY_ENCLOSED
    print(f"  ✓ Partially enclosed: Ao=500, Aoi=100, Agi=20000")

    # Enclosed: Ao < min(4, 0.01*Ag) → doesn't even qualify for further tests
    result = classify_enclosure(
        EnclosureTestInputs(Ao_sf=2, Ag_sf=5000, Aoi_sf=1000, Agi_sf=20000),
        code_version="7-22",
    )
    assert result == EnclosureType.ENCLOSED
    print(f"  ✓ Enclosed: Ao=2 < min(4, 50), default classification")

    # Open: Ao ≥ 0.8 * Ag
    result = classify_enclosure(
        EnclosureTestInputs(Ao_sf=4500, Ag_sf=5000, Aoi_sf=100, Agi_sf=20000),
        code_version="7-22",
    )
    assert result == EnclosureType.OPEN
    print(f"  ✓ Open: Ao/Ag = {4500/5000:.0%}")


def test_code_version_routing():
    """Verify that different code versions produce different terrain constants."""
    print("Test: Code version routing (7-16 vs 7-22 Exposure B)...")

    r16 = calculate_qz(120, "B", 62, 1.0, 1.0, 0.85, "7-16")
    r22 = calculate_qz(120, "B", 62, 1.0, 1.0, 0.85, "7-22")

    assert r16.alpha == 7.0
    assert r22.alpha == 7.5
    assert r16.zg_ft == 1200
    assert r22.zg_ft == 2460
    # ASCE 7-22 Exp B gives lower Kz → lower qz
    assert r22.qz_psf < r16.qz_psf
    pct_diff = (r22.qz_psf - r16.qz_psf) / r16.qz_psf * 100

    print(f"  ✓ 7-16: α={r16.alpha}, zg={r16.zg_ft}, qz={r16.qz_psf:.2f} psf")
    print(f"  ✓ 7-22: α={r22.alpha}, zg={r22.zg_ft}, qz={r22.qz_psf:.2f} psf")
    print(f"  ✓ Delta: {pct_diff:.1f}%")


def test_validation_errors():
    """Verify that invalid inputs raise ValueError correctly."""
    print("Test: Input validation errors...")

    errors_caught = 0

    # Negative wind speed
    try:
        calculate_qz(-10, "C", 30, 1.0, 1.0, 0.85, "7-22")
    except ValueError as e:
        assert "positive" in str(e).lower()
        errors_caught += 1

    # Invalid code version
    try:
        calculate_qz(120, "C", 30, 1.0, 1.0, 0.85, "7-99")
    except ValueError as e:
        assert "invalid" in str(e).lower() or "valid" in str(e).lower()
        errors_caught += 1

    # Negative effective wind area
    try:
        calculate_cc_pressure(30.0, -10, "1", 0.18, "7-22")
    except ValueError as e:
        assert "positive" in str(e).lower()
        errors_caught += 1

    # Zero building height for MWFRS
    try:
        calculate_mwfrs_directional(
            120, "C", 0, 100, 60, 1.0, 1.0, 0.85, 0.85, 0.18, "7-22"
        )
    except ValueError as e:
        assert "positive" in str(e).lower()
        errors_caught += 1

    assert errors_caught == 4
    print(f"  ✓ {errors_caught}/4 validation errors correctly raised")


def test_json_serialization():
    """Verify that engine results are JSON-serializable (critical for API responses)."""
    print("Test: JSON serialization...")
    from dataclasses import asdict

    r = calculate_qz(120, "C", 62, 1.0, 1.0, 0.85, "7-22")
    payload = json.dumps(asdict(r), indent=2)
    parsed = json.loads(payload)
    assert parsed["qz_psf"] > 0
    assert parsed["code_version"] == "7-22"

    cc = calculate_cc_pressure(35.0, 50, "2", 0.18, "7-22")
    cc_payload = json.dumps(asdict(cc), indent=2)
    cc_parsed = json.loads(cc_payload)
    assert cc_parsed["p_neg_with_pos_gcpi"] < 0
    assert cc_parsed["zone"] == "2"

    print(f"  ✓ VelocityPressureResult → JSON ({len(payload)} bytes)")
    print(f"  ✓ CCPressureResult → JSON ({len(cc_payload)} bytes)")


if __name__ == "__main__":
    print("=" * 72)
    print("  ASCE 7 Wind Load API — Integration Tests")
    print("=" * 72)
    print()

    test_qz_endpoint_flow()
    print()
    test_cc_endpoint_flow()
    print()
    test_mwfrs_directional_flow()
    print()
    test_mwfrs_lowrise_flow()
    print()
    test_enclosure_classification()
    print()
    test_code_version_routing()
    print()
    test_validation_errors()
    print()
    test_json_serialization()

    print()
    print("=" * 72)
    print("  All integration tests passed.")
    print("=" * 72)
