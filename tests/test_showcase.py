"""Tests for the showcase report generator (pcb_motor.showcase).

The full-fidelity build is slow (production pages take minutes), so these
tests build at reduced torque/field sampling -- every code path is identical,
just fewer samples.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from pcb_motor import showcase
from pcb_motor.design import MotorDesign
from pcb_motor.evaluate import evaluate_design

DRAFT = dict(torque_steps=12, field_nr=12, field_nphi=72)

# A hand-shaped (but realistically-shaped) sweep payload so the trade-off
# charts render without paying for real engine sweeps in tests.
FAKE_SWEEP = {
    "param": "trace_width_m",
    "label": "Trace width",
    "unit": "mm",
    "picked_x": 0.15,
    "points": [
        {"x": 0.15, "tau_cont_mNm": 15.4, "kt_mNm_per_A": 48.5,
         "r_phase_20c_ohm": 20.4, "l_phase_uH": 76.9,
         "pwm_ripple_A_pp": 1.63, "l_ext_uH": 1235.0, "i_cont_A": 0.32},
        {"x": 0.3, "tau_cont_mNm": 16.2, "kt_mNm_per_A": 30.1,
         "r_phase_20c_ohm": 6.1, "l_phase_uH": 28.0,
         "pwm_ripple_A_pp": 4.5, "l_ext_uH": 700.0, "i_cont_A": 0.54},
    ],
}


@pytest.fixture(scope="module")
def default_design():
    return MotorDesign()


@pytest.fixture(scope="module")
def default_results(default_design):
    return evaluate_design(default_design)


@pytest.fixture(scope="module")
def built_page(tmp_path_factory, default_design, default_results):
    """One draft-fidelity page for the default design (which FAILS the gate)."""
    out = tmp_path_factory.mktemp("showcase") / "report.html"
    path = showcase.build_showcase(
        default_design, out, results=default_results, sweep_data=FAKE_SWEEP,
        **DRAFT)
    return Path(path).read_text(encoding="utf-8")


def _payload(html: str) -> dict:
    m = re.search(
        r'<script id="motor-data" type="application/json">(.*?)</script>',
        html, re.S)
    assert m, "embedded data blob missing"
    return json.loads(m.group(1).replace("<\\/", "</"))


# --------------------------------------------------------------------------- #
# The built page
# --------------------------------------------------------------------------- #
def test_page_is_self_contained(built_page):
    """No network dependencies: every http(s) occurrence is either the GitHub
    anchor link or the (non-network) SVG XML namespace constant."""
    allowed = ("https://github.com/", "http://www.w3.org/2000/svg")
    for url in re.findall(r"https?://[^\s\"'<>)]+", built_page):
        assert url.startswith(allowed), f"unexpected external reference: {url}"
    # and nothing tries to import/fetch anything
    assert "<link" not in built_page.lower()
    assert 'src="http' not in built_page.lower()
    assert "@import" not in built_page


def test_key_sections_present(built_page):
    for sec in ["brief", "architecture", "board", "hunt", "physics",
                "thermal", "drive", "fab", "params", "verdict"]:
        assert f'<section id="{sec}"' in built_page, f"missing section {sec}"
    # the four visual elements' mount points
    for mount in ["motor-anim", "copper-viewer", "stack-view", "sweep-charts",
                  "winding-ring", "torque-chart", "field-map"]:
        assert f'id="{mount}"' in built_page, f"missing mount {mount}"
    # the honesty note
    assert "±30%" in built_page


def test_embedded_json_parses_with_real_geometry(built_page, default_design):
    data = _payload(built_page)
    # copper layers: non-empty polygon paths on both sides
    assert len(data["artwork"]["fcu"]) >= default_design.n_slots
    assert len(data["artwork"]["bcu"]) >= default_design.n_slots
    for poly in data["artwork"]["fcu"] + data["artwork"]["bcu"]:
        assert len(poly["pts"]) >= 3
    assert data["artwork"]["pads"], "terminal pads missing"
    # field grid is complete and sane
    f = data["field"]
    assert len(f["bz"]) == f["nr"] * f["nphi"]
    assert f["bz_peak_T"] > 0.05          # an N42 rotor at 1 mm gap is not 0
    # torque curve spans one electrical period
    t = data["torque"]
    assert len(t["elec_deg"]) == DRAFT["torque_steps"]
    assert len(t["tau_comm_mNm"]) == len(t["elec_deg"])
    assert t["mean_mNm"] > 0
    # magnets: one item per pole (arc rotor)
    assert len(data["magnets"]["items"]) == 2 * default_design.pole_pairs
    # stack covers boards + rotor
    kinds = [it["kind"] for it in data["stack"]["items"]]
    assert kinds.count("board") == default_design.n_stators
    assert kinds.count("rotor") == 1
    # sweep data passed through for the charts
    assert data["sweep"]["points"][0]["tau_cont_mNm"] == 15.4


def test_generated_preview_announces_itself(built_page):
    """The default design is untapered, so the page must NOT get production
    artwork (which is always tapered-wedge copper) — it gets the preview plus
    a loud, unmissable banner saying so."""
    data = _payload(built_page)
    assert data["artwork"]["source"] == "generated"
    assert 'class="preview-warn"' in built_page
    assert "preview artwork" in built_page.lower()
    assert "not the production footprint" in built_page.lower()
    assert "pcb-motor footprint --session" in built_page


def test_fail_gate_verdict_is_loud_and_honest(built_page):
    """The default design fails the no-choke gate 17x -- the page must say so."""
    data = _payload(built_page)
    assert data["gate"]["passed"] is False
    assert data["gate"]["factor"] > 10
    assert "DRIVE GATE: FAIL" in built_page
    assert "FAIL — worst-case PWM ripple" in built_page
    assert "Choke shopping spec" in built_page
    assert "µH" in built_page


def test_pass_gate_renders_pass(tmp_path, default_design, default_results):
    """A design whose ripple budget is satisfied renders the PASS verdict."""
    d = dataclasses.replace(default_design, drive_ripple_frac=25.0)
    results = {**default_results, "warnings": []}
    out = tmp_path / "pass.html"
    showcase.build_showcase(d, out, results=results, **DRAFT)
    html = out.read_text(encoding="utf-8")
    data = _payload(html)
    assert data["gate"]["passed"] is True
    assert "DRIVE GATE: PASS" in html
    assert "Choke shopping spec" not in html


# --------------------------------------------------------------------------- #
# Artwork resolution: production footprint or an announced preview — never a
# silent fake of the board
# --------------------------------------------------------------------------- #
def test_auto_builds_production_footprint_for_tapered_session(tmp_path):
    """A tapered-traces session with no footprint gets the real production
    artwork auto-built (and saved into the session dir for reuse)."""
    from pcb_motor.session import Session
    d = dataclasses.replace(MotorDesign(), tapered_traces=True)
    s = Session("cand", root=tmp_path)
    s.save_motor(d)
    art, notice = showcase._resolve_artwork(d, s, None, True)
    assert notice is None
    assert art["source"] == "kicad_mod"
    assert len(art["fcu"]) == d.n_slots
    assert (s.dir / "stator_full_2side.kicad_mod").exists()


def test_untapered_design_gets_announced_preview():
    """Untapered designs must not silently receive tapered production copper
    (its turns/R/Kt differ from the simulated numbers on the page)."""
    d = MotorDesign()
    assert not d.tapered_traces
    art, notice = showcase._resolve_artwork(d, None, None, True)
    assert art["source"] == "generated"
    assert "not the production footprint" in notice.lower()
    assert "tapered_traces" in notice


def test_builder_failure_falls_back_to_announced_preview(monkeypatch):
    import pcb_motor.kicad as kicad_pkg

    def boom(*a, **k):
        raise RuntimeError("clearance check exploded")

    monkeypatch.setattr(kicad_pkg, "build_footprint", boom)
    d = dataclasses.replace(MotorDesign(), tapered_traces=True)
    art, notice = showcase._resolve_artwork(d, None, None, True)
    assert art["source"] == "generated"
    assert "build failed" in notice
    assert "clearance check exploded" in notice


# --------------------------------------------------------------------------- #
# Pure helpers (no engine run)
# --------------------------------------------------------------------------- #
def test_parse_narrative_sections():
    text = ("intro line before headings\n\n"
            "## hunt — the trade-off\n\nCustom **hunt** prose.\n\n"
            "- one\n- two\n\n"
            "## drive\n\n> the verdict quote\n")
    sec = showcase.parse_narrative(text)
    assert "intro line" in sec["hero"]
    assert "<strong>hunt</strong>" in sec["hunt"]
    assert "<ul><li>one</li><li>two</li></ul>" in sec["hunt"]
    assert "<blockquote>" in sec["drive"]


def test_narrative_overrides_only_named_sections(default_design,
                                                 default_results):
    payload, prose, gate, _ = showcase._collect_payload(
        default_design, results=default_results,
        narrative_text="## hunt\nMy own hunt story.")
    assert "My own hunt story." in prose["hunt"]
    assert "coreless" in prose["hero"].lower() or prose["hero"]  # fallback kept


def test_kicad_artwork_parser_on_committed_footprint():
    """The dualstator80-36n42p example's production footprint parses into real polygons."""
    root = Path(__file__).resolve().parents[1] / "examples" / "dualstator80-36n42p"
    mod = root / "stator_full_2side.kicad_mod"
    if not mod.exists():
        pytest.skip("example footprint not present")
    design = MotorDesign(**{
        k: v for k, v in json.loads((root / "motor.json").read_text()).items()
        if k in {f.name for f in dataclasses.fields(MotorDesign)}})
    art = showcase._artwork_from_kicad(mod, design)
    assert art["source"] == "kicad_mod"
    assert len(art["fcu"]) == design.n_slots
    assert len(art["bcu"]) == design.n_slots
    assert len(art["pads"]) == 2 * design.n_slots
    assert len(art["vias"]) == design.n_slots
    # tooth assignment covers every slot
    assert {p["tooth"] for p in art["fcu"]} == set(range(design.n_slots))


def test_trace_width_sweep_shape(default_design, monkeypatch):
    """Sweep helper hits the engine once per width and captures the trade."""
    calls = []

    def fake_eval(d):
        calls.append(d.trace_width_m)
        return {"tau_cont_mNm": 1.0, "kt_mNm_per_A": 2.0,
                "r_phase_20c_ohm": 3.0, "l_phase_uH": 10.0,
                "pwm_ripple_A_pp": 5.0, "l_ext_uH": 100.0, "i_cont_A": 0.5,
                "warnings": []}

    monkeypatch.setattr(showcase, "evaluate_design", fake_eval)
    sw = showcase.trace_width_sweep(default_design, [0.2e-3, 0.4e-3])
    assert calls == [0.2e-3, 0.4e-3]
    assert [p["x"] for p in sw["points"]] == [0.2, 0.4]
    assert sw["picked_x"] == default_design.trace_width_m * 1e3
    assert all("l_ext_uH" in p for p in sw["points"])
