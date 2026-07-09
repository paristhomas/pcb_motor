"""The showcase report: one self-contained narrative HTML page per design.

Where ``report.py`` is the quick engineering printout (setup figure + table),
this module builds the page you'd put in front of people: a scrolling story --
brief, architecture, the board itself, the design hunt, the physics, and an
honest drive-feasibility verdict -- with the real engine data embedded and
rendered live in the browser:

- a **spinning motor animation** (canvas): the rotor magnets turning over the
  coil artwork, the Biot-Savart airgap field rotating with them, coils tinted
  by their instantaneous commutated phase current, live torque readout;
- an **interactive copper viewer** (SVG): zoom/pan the actual board artwork
  (the session's production ``.kicad_mod``, auto-built via
  :func:`pcb_motor.kicad.build_footprint` when missing; a winding-geometry
  preview is used only as a loudly-announced last resort), with layer toggles;
- an **exploded stack** (SVG): the stator/rotor sandwich pulled apart, every
  dimension from the design;
- **trade-off charts** (SVG): the design-hunt sweep with hover values.

Everything is inlined -- CSS/JS from package data (``showcase.css`` /
``showcase.js``), all numbers as one JSON blob -- so the page works offline,
from a file:// URL, and on GitHub Pages. No CDN, no external requests.

Narrative prose comes from an optional ``narrative.md`` next to the session's
``motor.json`` (see :func:`parse_narrative` for the section headings); any
section you don't write falls back to auto-generated text, so every design
gets a complete page.

Entry points: :func:`build_showcase` (module API) and
``pcb-motor showcase --session <name>`` (CLI).
"""

from __future__ import annotations

import dataclasses
import html as _html
import json
import math
import re
import tempfile
from pathlib import Path

import numpy as np

from .design import MotorDesign
from .evaluate import evaluate_design

# --------------------------------------------------------------------------- #
# Package-data assets (inlined into every page at build time)
# --------------------------------------------------------------------------- #
_ASSET_DIR = Path(__file__).parent


def _asset(name: str) -> str:
    return (_ASSET_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #
def _fmt(v, nd: int = 4) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    return f"{v:.{nd}g}"


def _fmt_big(v) -> str:
    """Format that avoids exponent notation for human-sized values (µH etc.)."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3g}"


def _sig(v, nd: int = 5):
    """Round a float to ``nd`` significant digits (keeps the JSON small)."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if not math.isfinite(v):
        return None
    return float(f"{v:.{nd}g}")


def _sig_list(arr, nd: int = 5) -> list:
    return [_sig(float(v), nd) for v in np.asarray(arr).ravel()]


def _esc(s: str) -> str:
    return _html.escape(str(s), quote=False)


# --------------------------------------------------------------------------- #
# Narrative: optional per-session markdown, with auto-generated fallbacks
# --------------------------------------------------------------------------- #
# Section ids a narrative.md can address with "## <id>" headings.
NARRATIVE_SECTIONS = (
    "hero", "brief", "architecture", "board", "hunt",
    "physics", "thermal", "drive", "fab", "verdict",
)

_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_EM = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")


def _md_inline(text: str) -> str:
    """Minimal inline markdown (already HTML-escaped input)."""
    text = _MD_CODE.sub(r"<code>\1</code>", text)
    text = _MD_BOLD.sub(r"<strong>\1</strong>", text)
    text = _MD_EM.sub(r"<em>\1</em>", text)
    text = _MD_LINK.sub(r'<a href="\2">\1</a>', text)
    return text


def _md_to_html(md: str) -> str:
    """Tiny markdown-to-HTML: paragraphs, bullet lists, blockquotes, inline
    code/bold/em/links. Enough for narrative prose; not a general converter."""
    out: list[str] = []
    block: list[str] = []
    mode = "p"

    def flush() -> None:
        nonlocal block, mode
        if not block:
            return
        if mode == "ul":
            items = "".join(f"<li>{_md_inline(b)}</li>" for b in block)
            out.append(f"<ul>{items}</ul>")
        elif mode == "quote":
            out.append(f"<blockquote><p>{_md_inline(' '.join(block))}</p></blockquote>")
        else:
            out.append(f"<p>{_md_inline(' '.join(block))}</p>")
        block, mode = [], "p"

    for raw in md.splitlines():
        line = _esc(raw.rstrip())
        s = line.strip()
        if not s:
            flush()
            continue
        if s.startswith("- ") or s.startswith("* "):
            if mode != "ul":
                flush()
                mode = "ul"
            block.append(s[2:].strip())
        elif s.startswith("&gt;"):
            if mode != "quote":
                flush()
                mode = "quote"
            block.append(s[4:].strip())
        else:
            if mode != "p":
                flush()
            block.append(s)
    flush()
    return "\n".join(out)


def parse_narrative(text: str) -> dict[str, str]:
    """Split a narrative.md into per-section HTML.

    Sections are addressed with ``## <id>`` headings where ``<id>`` is one of
    :data:`NARRATIVE_SECTIONS` (case-insensitive; anything after the id on the
    heading line is ignored, so ``## hunt — the trade-off`` works). Text before
    the first heading belongs to ``hero``. Unknown headings are kept as titled
    prose inside the previous section.
    """
    sections: dict[str, list[str]] = {}
    current = "hero"
    for line in text.splitlines():
        m = re.match(r"^##\s+([A-Za-z]+)\b(.*)$", line)
        if m and m.group(1).lower() in NARRATIVE_SECTIONS:
            current = m.group(1).lower()
            sections.setdefault(current, [])
            continue
        if line.startswith("# "):        # a top-level title line: ignore
            continue
        sections.setdefault(current, []).append(line)
    return {k: _md_to_html("\n".join(v).strip()) for k, v in sections.items()
            if "\n".join(v).strip()}


def _auto_narrative(design: MotorDesign, results: dict, gate: dict,
                    title: str) -> dict[str, str]:
    """Sensible generated prose so any design gets a complete page."""
    n_poles = 2 * design.pole_pairs
    r = results
    tau = r["tau_cont_mNm"]
    verdict = ("it passes its own drive-feasibility gate"
               if gate["passed"] else
               f"driving it choke-free fails the ripple gate by {gate['factor']:.0f}x "
               f"-- budget ~{gate['l_ext_uH']:.0f} uH of external inductance per phase")
    return {
        "hero": _md_to_html(
            f"A coreless axial-flux motor whose stator is a circuit board: "
            f"{design.n_slots} copper spiral coils on {design.n_stators} "
            f"{design.copper_layers}-layer PCB{'s' if design.n_stators != 1 else ''}, "
            f"a {n_poles}-pole {design.magnet_grade} rotor, and nothing else in the "
            f"magnetic circuit. The model says **{_fmt(tau, 3)} mNm continuous "
            f"(±30%)** at {_fmt(r['i_cont_A'], 3)} A -- and {verdict}."),
        "brief": _md_to_html(
            "What this machine was asked to be, from the session's "
            "`requirements.yaml`. The design and its requirements travel together, "
            "so the verdicts below are judged against these numbers, not vibes."),
        "architecture": _md_to_html(
            f"A {design.n_slots}-slot / {n_poles}-pole fractional-slot concentrated "
            f"winding: each tooth is one continuous spiral coil, phased and signed by "
            f"the star-of-slots layout, giving a fundamental winding factor "
            f"kw1 = {_fmt(r['winding_factor'], 3)}. "
            + (f"Two stator boards sandwich a single rotor disk and are wired in "
               f"series, so their torques add at the same current."
               if design.n_stators == 2 else
               f"{design.n_stators} stator board(s) face the rotor across a "
               f"{design.air_gap_m*1e3:.1f} mm air gap.")
            + " There is no iron: no cogging, no saturation, and little "
              "inductance -- that constraint surfaces in the drive section."),
        "board": _md_to_html(
            "The actual copper. Scroll to zoom, drag to pan, toggle layers. "
            "The back layer mirrors the front about each coil's centre-line so the "
            "two layers carry current the same way around the tooth -- series "
            "connection through a via stitch, torques adding rather than cancelling."),
        "hunt": _md_to_html(
            "The knob that matters most on a PCB winding is trace width: narrow "
            "traces pack more turns (more Kt, more inductance, much more resistance); "
            "wide traces run cooler but leave inductance behind. The sweep below is "
            "the engine re-evaluating the full design at each width -- same rotor, "
            "same annulus, same rules."),
        "physics": _md_to_html(
            "Everything here is computed from the actual geometry: vectorised "
            "Biot-Savart on the Amperian current-loop magnets gives the airgap "
            "field; the Lorentz force on every discretised copper segment, under "
            "proper 3-phase commutation, gives torque. The field map is the "
            "torque-coupling component B_z at the front copper plane."),
        "thermal": _md_to_html(
            "Continuous rating is set by I²R self-heating: a lumped convection "
            "balance holds the board at the temperature limit and asks what phase "
            "current gets there. Good for 'is this thermally plausible', not for "
            "hot-spot prediction."),
        "drive": _md_to_html(
            "The part most PCB-motor write-ups skip: an air-core winding has tiny "
            "inductance, so a stock FOC drive's PWM turns into large ripple current. "
            "The gate compares worst-case ripple `v_bus / (4·L·f_pwm)` against a "
            "budget of "
            f"{design.drive_ripple_frac:.0%} of the continuous current."),
        "fab": _md_to_html(
            "What you'd actually order. Boards from any fab that does "
            f"{design.copper_weight_oz:g} oz copper at these widths; magnets are "
            "off-the-shelf stock; glue, a printed carrier, and a bearing complete it."),
        "verdict": _md_to_html(
            f"On the model's numbers, **{title}** makes {_fmt(tau, 3)} mNm "
            f"continuously (±30%) from {_fmt(r['copper_mass_g'], 3)} g of copper -- "
            f"and {verdict}."),
    }


# --------------------------------------------------------------------------- #
# Requirements (session YAML -> display rows)
# --------------------------------------------------------------------------- #
def _requirements_rows(session) -> list[dict]:
    if session is None:
        return []
    text = session.load_requirements()
    if not text:
        return []
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    labels = {
        "torque_mNm": ("Continuous torque target", "mNm"),
        "speed_rev_s": ("Operating speed", "rev/s"),
        "voltage_V": ("Bus voltage", "V"),
        "envelope_od_mm": ("Max outer diameter", "mm"),
        "envelope_axial_mm": ("Max axial stack", "mm"),
        "duty": ("Duty", ""),
    }
    rows = []
    for k, v in data.items():
        if v is None:
            continue
        label, unit = labels.get(k, (k.replace("_", " "), ""))
        rows.append({"label": label, "value": str(v), "unit": unit})
    return rows


# --------------------------------------------------------------------------- #
# Ripple gate (recomputed here so it always matches the design's drive params)
# --------------------------------------------------------------------------- #
def _gate(design: MotorDesign, results: dict) -> dict:
    l_phase = results["l_phase_uH"] * 1e-6
    i_cont = results["i_cont_A"]
    v_bus = float(design.drive_v_bus)
    f_pwm = float(design.drive_f_pwm_hz)
    frac = float(design.drive_ripple_frac)
    k = v_bus * 0.25 / f_pwm if f_pwm > 0 else float("nan")
    ripple = k / l_phase if l_phase > 0 else float("inf")
    budget = frac * i_cont
    l_req = k / budget if budget > 0 else float("inf")
    l_ext = max(0.0, (l_req - l_phase)) * 1e6
    passed = math.isfinite(ripple) and budget > 0 and ripple <= budget
    factor = ripple / budget if budget > 0 else float("inf")
    if passed:
        statement = (f"PASS — worst-case PWM ripple {_fmt(ripple, 3)} A pp fits the "
                     f"{_fmt(budget, 3)} A budget at {v_bus:g} V bus / "
                     f"{f_pwm/1e3:g} kHz. No choke needed.")
    else:
        statement = (f"FAIL — worst-case PWM ripple {_fmt(ripple, 3)} A pp is "
                     f"{factor:.0f}× the {_fmt(budget, 3)} A budget at {v_bus:g} V "
                     f"bus / {f_pwm/1e3:g} kHz. Not drivable bare: budget "
                     f"~{l_ext:.0f} µH of external inductance per phase.")
    return {
        "passed": bool(passed),
        "ripple_pp": _sig(ripple),
        "budget_a": _sig(budget),
        "factor": _sig(factor, 4),
        "l_phase_uH": _sig(results["l_phase_uH"]),
        "l_ext_uH": _sig(l_ext),
        "v_bus": v_bus,
        "f_pwm_khz": f_pwm / 1e3,
        "ripple_frac": frac,
        "statement": statement,
    }


_CHOKE_E_SERIES = [10, 15, 22, 33, 47, 68, 100, 150, 220, 330, 470, 680,
                   1000, 1500, 2200, 3300, 4700, 6800, 10000]


def _choke_spec(design: MotorDesign, results: dict, gate: dict) -> dict | None:
    if gate["passed"]:
        return None
    need = gate["l_ext_uH"]
    std = [v for v in _CHOKE_E_SERIES if v >= need]
    lo = std[0] if std else need
    hi = std[1] if len(std) > 1 else lo
    i_sat = 1.5 * results["i_cont_A"]
    dcr = 0.1 * results["r_phase_20c_ohm"]
    return {
        "need_uH": _sig(need, 3),
        "buy": f"{lo:g}–{hi:g} µH",
        "i_sat_a": _sig(i_sat, 2),
        "dcr_ohm": _sig(dcr, 2),
        "text": (f"≥ {need:.0f} µH per phase → buy {lo:g}–{hi:g} µH shielded "
                 f"drum-core power inductors, I_sat ≥ {i_sat:.1f} A, "
                 f"DCR ≤ {dcr:.2g} Ω — three of them, one in series with each "
                 f"phase lead."),
    }


# --------------------------------------------------------------------------- #
# Geometry payloads: magnets, artwork, field grid, torque curve, stack
# --------------------------------------------------------------------------- #
def _magnet_extent(rotor) -> tuple[float, float]:
    from .magnets import active_rings, is_round
    if is_round(rotor.magnet_topology):
        rings = active_rings(rotor)
        return (min(rr - dd / 2 for rr, dd in rings),
                max(rr + dd / 2 for rr, dd in rings))
    return rotor.magnet_r_inner_m, rotor.magnet_r_outer_m


def _magnet_items(design: MotorDesign) -> dict:
    """Rotor magnets at theta=0, in mm, with per-pole polarity (+1 = N up)."""
    from .magnets import active_rings, is_round
    rotor = design.rotor()
    n_poles = 2 * rotor.pole_pairs
    pitch = 2.0 * math.pi / n_poles
    items = []
    if is_round(rotor.magnet_topology):
        for k in range(n_poles):
            ang = (k + 0.5) * pitch
            pol = 1 if k % 2 == 0 else -1
            for ring_r, disc_d in active_rings(rotor):
                items.append({
                    "kind": "circle",
                    "cx": _sig(ring_r * 1e3 * math.cos(ang), 5),
                    "cy": _sig(ring_r * 1e3 * math.sin(ang), 5),
                    "r": _sig(disc_d * 1e3 / 2, 4),
                    "pol": pol,
                })
    else:
        arc = rotor.pole_coverage * pitch
        r_in, r_out = rotor.magnet_r_inner_m * 1e3, rotor.magnet_r_outer_m * 1e3
        for k in range(n_poles):
            c = (k + 0.5) * pitch
            a0, a1 = c - arc / 2, c + arc / 2
            angs = np.linspace(a0, a1, 12)
            pts = ([[r_in * math.cos(a), r_in * math.sin(a)] for a in angs]
                   + [[r_out * math.cos(a), r_out * math.sin(a)] for a in angs[::-1]])
            items.append({
                "kind": "poly",
                "pts": [[_sig(x, 5), _sig(y, 5)] for x, y in pts],
                "pol": 1 if k % 2 == 0 else -1,
            })
    ext = _magnet_extent(rotor)
    return {
        "topology": rotor.magnet_topology,
        "poles": n_poles,
        "thickness_mm": _sig(rotor.magnet_thickness_m * 1e3, 3),
        "grade": rotor.magnet_grade,
        "r_in_mm": _sig(ext[0] * 1e3, 4),
        "r_out_mm": _sig(ext[1] * 1e3, 4),
        "items": items,
    }


def _ring_pts(coords, nd: int = 3) -> list:
    return [[round(float(x), nd), round(float(y), nd)] for x, y in coords]


def _tooth_of(cx: float, cy: float, n_slots: int) -> int:
    sector = 2.0 * math.pi / n_slots
    return int(round(math.atan2(cy, cx) / sector)) % n_slots


def _phase_of_pad(name: str) -> int | None:
    """Phase index (A/B/C -> 0/1/2) from a routed-board pad net name.

    Routed boards carry their series interconnect as phase-named nets
    (``AL``/``AE``, ``BL``/``BE``, ``CL``/``CE``), so the net name is an exact
    phase label — far more reliable than guessing from copper centroids, which
    on a fully-routed board don't separate into clean per-coil regions.
    """
    return {"A": 0, "B": 1, "C": 2}.get(name[:1].upper()) if name else None


def _artwork_from_kicad(path: str | Path, design: MotorDesign) -> dict:
    """Parse the production ``.kicad_mod``: filled copper polys, pads, vias.

    KiCad footprint y points down; we flip it so +y is up, matching the
    engine's coordinate frame. Units: mm.
    """
    txt = Path(path).read_text(encoding="utf-8", errors="replace")
    n_slots = int(design.n_slots)
    layers: dict[str, list] = {"F.Cu": [], "B.Cu": []}
    for m in re.finditer(
            r'\(fp_poly\s*\(pts(.*?)\)\s*\(stroke.*?\(layer "([^"]+)"', txt, re.S):
        pts = [(float(x), -float(y))
               for x, y in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", m.group(1))]
        if len(pts) >= 3 and m.group(2) in layers:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            layers[m.group(2)].append(
                {"tooth": _tooth_of(cx, cy, n_slots), "pts": _ring_pts(pts)})

    # Custom pads: one polygon each on the standard footprint (the terminal
    # pads), but MANY polygons per pad on the routed boards, where every bit
    # of copper is a net-bearing pad. Parse per pad block and keep them all.
    pads = []
    pad_polys: list[tuple[str, list]] = []   # (F|B, ring pts) for every poly
    for block in txt.split('(pad "')[1:]:
        head = re.match(
            r'([^"]+)" smd custom \(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)',
            block)
        lay = re.search(r'\(layers "?([^")\s]+)"?\)', block)
        if not head or not lay:
            continue
        name, cx, cy = head.group(1), float(head.group(2)), float(head.group(3))
        rot = math.radians(float(head.group(4) or 0.0))
        layer = "F" if lay.group(1).startswith("F") else "B"
        cosr, sinr = math.cos(rot), math.sin(rot)
        polys = []
        for g in re.finditer(r"\(gr_poly \(pts (.*?)\) \(width", block, re.S):
            pts = []
            for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", g.group(1)):
                x, y = float(a), float(b)
                # rotate primitive by the pad angle (KiCad rotates CCW in its
                # frame), translate to the pad centre, then flip y into our frame.
                xr = x * cosr + y * sinr
                yr = -x * sinr + y * cosr
                pts.append((cx + xr, -(cy + yr)))
            if len(pts) >= 3:
                polys.append(_ring_pts(pts))
        if polys:
            pads.append({"name": name, "layer": layer, "pts": polys[0]})
            phase = _phase_of_pad(name)
            pad_polys.extend((layer, p, phase) for p in polys)

    if not layers["F.Cu"] and not layers["B.Cu"] and pad_polys:
        # Routed-style board: ALL copper lives in net-bearing pads. Show it as
        # copper (per layer); the thru-hole markers below cover the terminals.
        # Phase comes from the pad net name (exact), not the centroid (which on
        # routed copper does not resolve into clean per-coil sectors).
        for layer, pts, phase in pad_polys:
            key = "F.Cu" if layer == "F" else "B.Cu"
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            poly = {"tooth": _tooth_of(cx, cy, n_slots), "pts": pts}
            if phase is not None:
                poly["phase"] = phase
            layers[key].append(poly)
        pads = []

    vias = [[round(float(a), 3), round(-float(b), 3)]
            for a, b in re.findall(r"thru_hole \w+ \(at ([-\d.]+) ([-\d.]+)\)", txt)]
    mvia = re.search(r"thru_hole \w+ \(at [-\d.]+ [-\d.]+\) \(size ([-\d.]+)", txt)
    via_d = float(mvia.group(1)) if mvia else 0.6

    return {
        "units": "mm",
        "source": "kicad_mod",
        "fcu": layers["F.Cu"],
        "bcu": layers["B.Cu"],
        "pads": pads,
        "vias": vias,
        "via_d": via_d,
    }


def _buffer_polyline(design: MotorDesign, poly_m: np.ndarray, simplify_mm: float):
    """Filled copper polygon (shapely, mm) for one coil polyline.

    Constant-width coils buffer the whole trace at once; tapered coils buffer
    radius-grouped chunks at their local width and union them.
    """
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    from .coil_spiral import trace_width_at

    xy_mm = poly_m[:, :2] * 1e3
    if not getattr(design, "tapered_traces", False):
        w = design.trace_width_m * 1e3
        return LineString(xy_mm).buffer(w / 2, quad_segs=4).simplify(simplify_mm)
    # Tapered: chunk the polyline, buffer each chunk at its mid-radius width.
    chunk = 24
    parts = []
    for i in range(0, len(xy_mm) - 1, chunk):
        seg = xy_mm[i:i + chunk + 1]
        if len(seg) < 2:
            continue
        r_mid = float(np.hypot(seg[:, 0], seg[:, 1]).mean()) * 1e-3
        w = float(trace_width_at(design, r_mid)) * 1e3
        parts.append(LineString(seg).buffer(w / 2, quad_segs=4))
    return unary_union(parts).simplify(simplify_mm)


def _artwork_generated(design: MotorDesign, simplify_mm: float = 0.01) -> dict:
    """Regenerate board artwork from the winding geometry (no .kicad_mod).

    F.Cu is the drivable spiral per tooth; B.Cu is its mirror about the tooth
    centre-line (the production series-connection convention -- the mirrored
    back layer is what keeps the two layers' torques adding). Terminal
    locations are marked as pads.
    """
    from .coil_spiral import coil_spiral_polyline

    n_slots = int(design.n_slots)
    sector = 2.0 * math.pi / n_slots
    z = design.layer_z_m(0)

    def rings_of(geom):
        geoms = getattr(geom, "geoms", [geom])
        return [g for g in geoms if g.geom_type == "Polygon" and not g.is_empty]

    fcu, bcu, pads = [], [], []
    for k in range(n_slots):
        center = k * sector
        poly = coil_spiral_polyline(design, center, z)
        if poly.shape[0] < 2:
            continue
        buf = _buffer_polyline(design, poly, simplify_mm)
        for g in rings_of(buf):
            fcu.append({"tooth": k, "pts": _ring_pts(g.exterior.coords)})
        # Mirror about the tooth centre-line: (r, phi) -> (r, 2*center - phi).
        r = np.hypot(poly[:, 0], poly[:, 1])
        phi = 2.0 * center - np.arctan2(poly[:, 1], poly[:, 0])
        mirrored = np.column_stack([r * np.cos(phi), r * np.sin(phi), poly[:, 2]])
        mbuf = _buffer_polyline(design, mirrored, simplify_mm)
        for g in rings_of(mbuf):
            bcu.append({"tooth": k, "pts": _ring_pts(g.exterior.coords)})
        # Terminal A of the front spiral, as a pad marker.
        tw_mm = design.trace_width_m * 1e3
        for name, pt in ((f"{k}A", poly[0]), (f"{k}B", poly[-1])):
            cx, cy = pt[0] * 1e3, pt[1] * 1e3
            circ = [[round(cx + tw_mm * math.cos(a), 3),
                     round(cy + tw_mm * math.sin(a), 3)]
                    for a in np.linspace(0, 2 * math.pi, 13)]
            pads.append({"name": name, "layer": "F", "pts": circ})
    return {
        "units": "mm",
        "source": "generated",
        "fcu": fcu,
        "bcu": bcu,
        "pads": pads,
        "vias": [],
        "via_d": 0.6,
    }


def _field_grid(design: MotorDesign, nr: int, nphi: int | None) -> dict:
    """|Bz| polar grid (signed, mT ints) at the front copper plane, rotor at 0.

    The rotor field rotates rigidly with the rotor, so the browser can animate
    the field by rotating this one grid -- no per-frame physics needed.
    """
    from .magnets import magnet_segments
    from .iron import with_iron_images

    rotor = design.rotor()
    ext = _magnet_extent(rotor)
    r0 = min(design.r_inner_m, ext[0])
    r1 = max(design.r_outer_m, ext[1])
    poles = 2 * rotor.pole_pairs
    if nphi is None:
        nphi = int(min(720, max(180, poles * 12)))
    z = design.layer_z_m(0)
    r = np.linspace(r0, r1, nr)
    phi = np.linspace(0.0, 2.0 * math.pi, nphi, endpoint=False)
    R, PHI = np.meshgrid(r, phi, indexing="ij")
    pts = np.column_stack([(R * np.cos(PHI)).ravel(),
                           (R * np.sin(PHI)).ravel(),
                           np.full(R.size, z)])
    from .field import b_field_at_points
    src = with_iron_images(magnet_segments(rotor, 0.0), rotor)
    bz = b_field_at_points(src, pts, design.coil_resolution_m)[:, 2].reshape(nr, nphi)
    bz_mT = np.rint(bz * 1e4).astype(int)  # 0.1 mT integer steps
    return {
        "r0_mm": _sig(r0 * 1e3, 5),
        "r1_mm": _sig(r1 * 1e3, 5),
        "nr": nr,
        "nphi": nphi,
        "z_mm": _sig(z * 1e3, 4),
        "scale": 1e-4,           # value * scale = tesla
        "bz": bz_mT.ravel().tolist(),
        "bz_peak_T": _sig(float(np.abs(bz).max()), 4),
    }


def _torque_payload(design: MotorDesign, geo, i_amp: float, n_steps: int) -> dict:
    from .torque import torque_vs_angle
    tq = torque_vs_angle(design, geo, i_amp=i_amp, n_steps=n_steps)
    return {
        "elec_deg": _sig_list(tq["elec_deg"], 4),
        "tau_comm_mNm": _sig_list(tq["tau_commutated_nm"] * 1e3, 5),
        "tau_dc_mNm": _sig_list(tq["tau_dc_nm"] * 1e3, 5),
        "mean_mNm": _sig(tq["mean_torque_nm"] * 1e3, 5),
        "ripple_pct": _sig(tq["ripple_pct"], 3),
        "i_amp": _sig(i_amp, 4),
        "delta_rad": _sig(tq["commutation_delta_rad"], 5),
    }


def _stack_payload(design: MotorDesign) -> dict:
    """The axial sandwich, top to bottom, every dimension from the design."""
    rotor = design.rotor()
    zs = rotor.stator_z_m() * 1e3
    t_b = design.board_thickness_m * 1e3
    t_m = rotor.magnet_thickness_m * 1e3
    gap = design.air_gap_m * 1e3
    ext = _magnet_extent(rotor)
    board_od = 2 * (design.r_outer_m * 1e3 + 2.0)
    rotor_od = 2 * (ext[1] * 1e3 + 2.0)
    coil = [design.r_inner_m * 1e3, design.r_outer_m * 1e3]
    mag = [ext[0] * 1e3, ext[1] * 1e3]

    def board(z, label):
        return {"kind": "board", "label": label, "z": _sig(z, 4), "t": _sig(t_b, 3),
                "od": _sig(board_od, 4), "copper": [_sig(coil[0], 4), _sig(coil[1], 4)],
                "note": (f"{design.copper_layers}-layer FR4 {t_b:g} mm, "
                         f"{design.copper_weight_oz:g} oz Cu")}

    def rotor_item(z, label):
        return {"kind": "rotor", "label": label, "z": _sig(z, 4),
                "t": _sig(t_m + rotor.carrier_thickness_m * 1e3, 3),
                "od": _sig(rotor_od, 4), "mag": [_sig(mag[0], 4), _sig(mag[1], 4)],
                "note": (f"{2*rotor.pole_pairs} poles {rotor.magnet_grade}, "
                         f"{t_m:g} mm magnets on a "
                         f"{rotor.carrier_thickness_m*1e3:g} mm carrier")}

    def air(z, label=None):
        return {"kind": "gap", "label": label or f"air gap {gap:g} mm",
                "z": _sig(z, 4), "t": _sig(gap, 3), "od": _sig(board_od, 4)}

    # Back iron: a flat return plate flush on the *outboard* face of each stator
    # board (away from the rotor). The physics models it as an ideal mu->inf
    # plane, so its thickness is not a design variable; we draw a representative
    # 1 mm mild-steel plate purely so the reader can see it is (or is not) there.
    t_iron = 1.0
    stand = design.iron_standoff_m * 1e3

    def iron(z, label):
        return {"kind": "iron", "label": label, "z": _sig(z, 4), "t": _sig(t_iron, 3),
                "od": _sig(board_od, 4),
                "note": "≈1 mm mild-steel return plate (fixed; modelled as an ideal plane)"}

    items: list[dict]
    if design.rotor_sides == 2:
        z2 = 2 * rotor.stator_z_m() * 1e3
        items = [rotor_item(z2, "rotor (top)"), air(z2 / 2 + t_b / 2),
                 board(zs, "stator board"), air(zs - t_b / 2 - gap / 2),
                 rotor_item(0.0, "rotor (bottom)")]
        shift = zs
        items = [{**it, "z": _sig(it["z"] - shift, 4)} for it in items]
    elif design.n_stators == 2:
        items = [board(zs, "stator board (top)"), air((zs - t_b / 2 + t_m / 2) / 2),
                 rotor_item(0.0, "rotor"), air(-(zs - t_b / 2 + t_m / 2) / 2),
                 board(-zs, "stator board (bottom)")]
        if design.back_iron:
            zi = zs + t_b / 2 + stand + t_iron / 2
            items = ([iron(zi, "back iron (top)")] + items
                     + [iron(-zi, "back iron (bottom)")])
    else:
        items = [board(zs, "stator board"), air((zs - t_b / 2 + t_m / 2) / 2),
                 rotor_item(0.0, "rotor")]
        if design.back_iron:
            zi = zs + t_b / 2 + stand + t_iron / 2
            items = [iron(zi, "back iron")] + items
    total = (max(it["z"] + it["t"] / 2 for it in items)
             - min(it["z"] - it["t"] / 2 for it in items))
    n_iron = sum(1 for it in items if it["kind"] == "iron")
    return {"items": items, "gap_mm": _sig(gap, 3), "total_mm": _sig(total, 4),
            "back_iron": bool(design.back_iron), "n_iron": n_iron}


# --------------------------------------------------------------------------- #
# Design-hunt sweep (recompute with the engine, embed as chart data)
# --------------------------------------------------------------------------- #
def trace_width_sweep(design: MotorDesign, widths_m, *, progress=None) -> dict:
    """Re-evaluate ``design`` at each trace width; returns showcase sweep data.

    This is the design-hunt chart's data source: full engine runs (expect tens
    of seconds per point for large machines), never hardcoded numbers.
    """
    points = []
    for w in widths_m:
        d = dataclasses.replace(design, trace_width_m=float(w))
        r = evaluate_design(d)
        g = _gate(d, r)
        points.append({
            "x": _sig(float(w) * 1e3, 4),
            "tau_cont_mNm": _sig(r["tau_cont_mNm"]),
            "kt_mNm_per_A": _sig(r["kt_mNm_per_A"]),
            "r_phase_20c_ohm": _sig(r["r_phase_20c_ohm"]),
            "l_phase_uH": _sig(r["l_phase_uH"]),
            "pwm_ripple_A_pp": _sig(r["pwm_ripple_A_pp"]),
            "l_ext_uH": _sig(g["l_ext_uH"]),
            "i_cont_A": _sig(r["i_cont_A"]),
        })
        if progress:
            progress(points[-1])
    return {
        "param": "trace_width_m",
        "label": "Trace width",
        "unit": "mm",
        "picked_x": _sig(design.trace_width_m * 1e3, 4),
        "points": points,
    }


# --------------------------------------------------------------------------- #
# Grouped design-parameter table
# --------------------------------------------------------------------------- #
def _param_groups(design: MotorDesign, results: dict, gate: dict) -> list[dict]:
    d, r = design, results
    n_poles = 2 * d.pole_pairs

    def row(label, value, unit=""):
        return {"label": label, "value": value, "unit": unit}

    from .magnets import active_rings, is_round
    if is_round(d.magnet_topology):
        rings = ", ".join(f"Ø{dd*1e3:g} discs @ r={rr*1e3:g} mm"
                          for rr, dd in active_rings(d.rotor()))
        magnet_geo = row("Disc rings", rings)
    else:
        magnet_geo = row("Pole arc",
                         f"{d.magnet_r_inner_m*1e3:g}–{d.magnet_r_outer_m*1e3:g} mm, "
                         f"{d.pole_coverage*100:.0f}% coverage")

    if getattr(d, "tapered_traces", False):
        from .coil_spiral import trace_width_at
        w_out = float(trace_width_at(d, d.r_outer_m)) * 1e3
        trace = row("Trace width", f"{d.trace_width_m*1e3:.3f}–{w_out:.3f} (tapered)", "mm")
    else:
        trace = row("Trace width", f"{d.trace_width_m*1e3:.3f}", "mm")

    return [
        {"group": "Architecture", "rows": [
            row("Winding", f"{d.winding_topology}, {d.n_slots}N{n_poles}P"),
            row("Phases", f"{d.n_phases} (WYE), {d.parallel_paths} parallel path(s)"),
            row("Stators", f"{d.n_stators} × {d.copper_layers}-layer, in series"),
            row("Winding factor kw1", _fmt(r["winding_factor"], 3)),
            row("Turns / phase / layer-set", str(r["n_turns"])),
        ]},
        {"group": "Rotor & magnets", "rows": [
            row("Magnets", f"{d.magnet_grade}, {d.magnet_topology}, "
                           f"{n_poles} poles ({d.pole_pairs} pp)"),
            magnet_geo,
            row("Magnet thickness", f"{d.magnet_thickness_m*1e3:g}", "mm"),
            row("Carrier thickness", f"{d.carrier_thickness_m*1e3:g}", "mm"),
            row("Rotor sides", "2 (dual-rotor sandwich)" if d.rotor_sides == 2 else "1"),
        ]},
        {"group": "Board & copper", "rows": [
            row("Active annulus", f"{d.r_inner_m*1e3:.1f} – {d.r_outer_m*1e3:.1f}", "mm"),
            trace,
            row("Trace spacing", f"{d.trace_space_m*1e3:.3f}", "mm"),
            row("Copper weight", f"{d.copper_weight_oz:g}", "oz"),
            row("Board thickness", f"{d.board_thickness_m*1e3:g}", "mm FR4"),
            row("Air gap (per side)", f"{d.air_gap_m*1e3:.2f}", "mm"),
            row("Back iron", "yes" if d.back_iron else "none (coreless)"),
        ]},
        {"group": "Electromagnetics (computed)", "rows": [
            row("Kt (torque constant)", _fmt(r["kt_mNm_per_A"]), "mNm/A"),
            row("Mean / peak airgap |Bz|",
                f"{_fmt(r['b_gap_mean_T'])} / {_fmt(r['b_gap_peak_T'])}", "T"),
            row("Phase resistance @20 °C", _fmt(r["r_phase_20c_ohm"]), "Ω"),
            row("Phase inductance (air-core)", _fmt(r["l_phase_uH"]), "µH"),
            row("Torque ripple (commutated)", f"{r['torque_ripple']*100:.2g}", "%"),
            row("Winding utilisation", _fmt(r["winding_utilisation"], 3)),
        ]},
        {"group": "Thermal & continuous rating (computed)", "rows": [
            row("Continuous current", _fmt(r["i_cont_A"]), "A"),
            row("Continuous torque", _fmt(r["tau_cont_mNm"]), "mNm ±30%"),
            row("Copper loss at limit", _fmt(r["copper_loss_W"]), "W"),
            row("Temp limit / ambient", f"{d.temp_limit_c:g} / {d.ambient_c:g}", "°C"),
            row("Convection h", f"{d.h_conv:g}", "W/m²K"),
            row("Current density (neck)", _fmt(r["current_density_A_mm2"]), "A/mm²"),
            row("Drive voltage @ I_cont", _fmt(r["v_drive_cont_V"]), "V"),
        ]},
        {"group": "Drive & parasitics", "rows": [
            row("Reference drive", f"{d.drive_v_bus:g} V bus, "
                                   f"{d.drive_f_pwm_hz/1e3:g} kHz PWM"),
            row("Ripple budget", f"{d.drive_ripple_frac:.0%} of I_cont "
                                 f"= {_fmt(gate['budget_a'], 3)} A"),
            row("PWM ripple (bare winding)", _fmt(gate["ripple_pp"], 3), "A pp"),
            row("Ripple gate", "PASS" if gate["passed"]
                else f"FAIL ({gate['factor']:.0f}×)"),
            row("External L for budget", _fmt_big(gate["l_ext_uH"]), "µH/phase"),
            row("Eddy loss @ ref speed", _fmt(r["eddy_loss_W_ref"], 3), "W"),
        ]},
        {"group": "Mechanical", "rows": [
            row("Rotor inertia", _fmt(r["j_rotor_kgm2"], 3), "kg·m²"),
            row("Total inertia", _fmt(r["j_total_kgm2"], 3), "kg·m²"),
            row("Continuous acceleration", _fmt(r["accel_cont_rad_s2"]), "rad/s²"),
            row("Copper mass", _fmt(r["copper_mass_g"]), "g"),
            row("Airgap shear", _fmt(r["shear_stress_kPa"]), "kPa"),
            row("Axial stack height", _fmt(_stack_payload(design)["total_mm"], 3), "mm"),
        ]},
    ]


# --------------------------------------------------------------------------- #
# Fab + BOM
# --------------------------------------------------------------------------- #
def _bom(design: MotorDesign, results: dict, gate: dict) -> dict:
    from .magnets import active_rings, is_round
    rotor = design.rotor()
    n_poles = 2 * rotor.pole_pairs
    board_od = 2 * (design.r_outer_m * 1e3 + 2.0)
    boards = {
        "qty": design.n_stators,
        "desc": (f"{design.copper_layers}-layer FR4 "
                 f"{design.board_thickness_m*1e3:g} mm, "
                 f"{design.copper_weight_oz:g} oz copper, ~Ø{board_od:.0f} mm"),
        "rule": (f"min trace/space used: {design.trace_width_m*1e3:.3f} / "
                 f"{design.trace_space_m*1e3:.3f} mm (JLC 1 oz class rules)"),
    }
    magnets = []
    if is_round(rotor.magnet_topology):
        for ring_r, disc_d in active_rings(rotor):
            magnets.append({
                "qty": n_poles,
                "desc": (f"Ø{disc_d*1e3:g} × {rotor.magnet_thickness_m*1e3:g} mm "
                         f"{rotor.magnet_grade} disc magnets, axially magnetised "
                         f"(ring radius {ring_r*1e3:g} mm)"),
            })
    else:
        magnets.append({
            "qty": n_poles,
            "desc": (f"arc segment magnets {rotor.magnet_r_inner_m*1e3:g}–"
                     f"{rotor.magnet_r_outer_m*1e3:g} mm radius, "
                     f"{rotor.pole_coverage*100:.0f}% pole arc, "
                     f"{rotor.magnet_thickness_m*1e3:g} mm "
                     f"{rotor.magnet_grade} (custom -- or substitute round discs)"),
        })
    return {
        "boards": boards,
        "magnets": magnets,
        "choke": _choke_spec(design, results, gate),
        "extras": [
            "3D-printed rotor carrier + hub (size the pockets for the disc repulsion)",
            "bearing + shaft to hold the air gap "
            f"({design.air_gap_m*1e3:g} mm per side -- the model's most "
            "sensitive parameter)",
        ],
    }


# --------------------------------------------------------------------------- #
# Artwork resolution (never silently fake the board)
# --------------------------------------------------------------------------- #
def _resolve_artwork(design: MotorDesign, session, artwork_mod,
                     auto_footprint: bool):
    """Pick the board artwork for the page, never silently faking the board.

    Preference order:

    1. an explicit ``artwork_mod`` / the session's committed production
       ``stator_full_2side.kicad_mod``;
    2. auto-build the production footprint with
       :func:`pcb_motor.kicad.build_footprint` (into the session dir, so the
       session gains the real file) — but only when the design was simulated
       with ``tapered_traces=true``, because the production builder always
       emits tapered-wedge copper and the page's numbers must describe the
       copper it shows;
    3. regenerate a preview from the winding geometry, with a **prominent
       notice** on the page saying it is not the production footprint.

    Returns ``(artwork_payload, notice)``; ``notice`` is ``None`` only when
    the artwork is real production copper.
    """
    sess_name = session.name if session is not None else "<name>"

    art_path = None
    if artwork_mod is not None:
        art_path = Path(artwork_mod)
    elif session is not None:
        # committed production copper: the general filled-copper footprint, or a
        # verbatim routed footprint (gimbal90) -- both are real board copper.
        for cand_name in ("stator_full_2side.kicad_mod",
                          "stator_routed_2side_tabs.kicad_mod",
                          "stator_routed_2side.kicad_mod"):
            cand = session.dir / cand_name
            if cand.exists():
                art_path = cand
                break

    reason = None
    if art_path is None and auto_footprint:
        if getattr(design, "tapered_traces", False):
            try:
                from .kicad import build_footprint
                dest = (session.dir / "stator_full_2side.kicad_mod"
                        if session is not None else
                        Path(tempfile.mkdtemp(prefix="pcb_motor_showcase_"))
                        / "stator_full_2side.kicad_mod")
                rep = build_footprint(design, str(dest))
                if rep.passed:
                    art_path = dest
                else:
                    reason = ("the production footprint build FAILED its "
                              "clearance verification")
            except Exception as exc:            # noqa: BLE001 — fall back, loudly
                reason = f"the production footprint build failed: {exc}"
        else:
            reason = ("this design was simulated with constant-width traces, "
                      "but production artwork is always tapered-wedge copper "
                      "whose turns/resistance/Kt would not match this page — "
                      "re-evaluate with tapered_traces=true first")

    if art_path is not None and art_path.exists():
        artwork = _artwork_from_kicad(art_path, design)
        if artwork["fcu"]:
            return artwork, None
        reason = reason or "the footprint file parsed to no copper polygons"

    notice = ("PREVIEW ARTWORK — regenerated from the winding geometry, NOT "
              "the production footprint ("
              + (reason or "no production footprint in this session")
              + f"). Run pcb-motor footprint --session {sess_name} to build "
                "the real artwork.")
    artwork = _artwork_generated(design)
    artwork["notice"] = notice
    return artwork, notice


def _circle_poly(r: float, n: int = 128) -> list:
    """Closed circle as a y-up polyline (mm), for a plain board edge / bore."""
    return [[round(r * math.cos(2 * math.pi * k / n), 3),
             round(r * math.sin(2 * math.pi * k / n), 3)] for k in range(n)]


def _board_outline(design: MotorDesign, session) -> list:
    """Board Edge.Cuts outline(s) as closed y-up polylines (mm), ~centred.

    Prefers a committed ``tabs_outline.json`` next to the session -- the exact
    routed-board edge, mounting tabs and all -- so the viewer shows the real
    board shape. Otherwise a plain circular edge derived from the design. The
    winding inner bore is added as a second ring. Concentric with the copper,
    which is likewise centred, so the two overlay.
    """
    import json

    polys = []
    tabs = None
    if session is not None:
        cand = session.dir / "tabs_outline.json"
        if cand.exists():
            try:
                tabs = json.loads(cand.read_text(encoding="utf-8"))["outline_mm"]
            except Exception:
                tabs = None
    if tabs:
        # KiCad Edge.Cuts is y-down; flip to y-up to match the copper frame.
        polys.append([[round(float(x), 3), round(-float(y), 3)] for x, y in tabs])
    else:
        polys.append(_circle_poly(design.r_outer_m * 1e3 + 2.0))
    bore = design.r_inner_m * 1e3
    if bore > 1.0:
        polys.append(_circle_poly(bore))
    return polys


# --------------------------------------------------------------------------- #
# Payload assembly
# --------------------------------------------------------------------------- #
def _collect_payload(design: MotorDesign, *, session=None, name=None,
                     narrative_text=None, results=None, sweep_data=None,
                     artwork_mod=None, torque_steps=60, field_nr=36,
                     field_nphi=None, auto_footprint=True) -> dict:
    from .coils import build_coil, _coil_layout

    if results is None:
        results = evaluate_design(design)
    gate = _gate(design, results)
    n_poles = 2 * design.pole_pairs
    combo = f"{design.n_slots}N{n_poles}P"
    title = name or (session.name if session is not None else
                     f"{design.winding_topology} {combo} {design.magnet_grade}")

    # Narrative: user sections override the generated fallbacks per-section.
    narrative = _auto_narrative(design, results, gate, title)
    if narrative_text:
        narrative.update(parse_narrative(narrative_text))

    # Artwork: production footprint (existing or auto-built), else a preview
    # that announces itself.
    artwork, _art_notice = _resolve_artwork(design, session, artwork_mod,
                                            auto_footprint)
    artwork["outline"] = _board_outline(design, session)

    geo = build_coil(design)
    i_cont = float(results["i_cont_A"]) or 1.0
    torque = _torque_payload(design, geo, i_cont, torque_steps)
    field = _field_grid(design, field_nr, field_nphi)
    layout = [{"phase": p, "sign": s}
              for p, s in _coil_layout(design.n_slots, design.n_phases)]

    clean_results = {k: (_sig(v) if isinstance(v, float) else v)
                     for k, v in results.items() if k != "warnings"}

    payload = {
        "meta": {
            "title": title,
            "combo": combo,
            "slots": design.n_slots,
            "poles": n_poles,
            "pole_pairs": design.pole_pairs,
            "phases": design.n_phases,
            "topology": design.winding_topology,
            "grade": design.magnet_grade,
            "n_stators": design.n_stators,
            "layers": design.copper_layers,
            "od_mm": _sig(2 * (design.r_outer_m * 1e3 + 2.0), 4),
            "session": session.name if session is not None else None,
        },
        "results": clean_results,
        "warnings": list(results.get("warnings", [])),
        "gate": gate,
        "layout": layout,
        "artwork": artwork,
        "magnets": _magnet_items(design),
        "field": field,
        "torque": torque,
        "stack": _stack_payload(design),
        "sweep": sweep_data,
        "coil_annulus_mm": [_sig(design.r_inner_m * 1e3, 4),
                            _sig(design.r_outer_m * 1e3, 4)],
    }
    return payload, narrative, gate, results


# --------------------------------------------------------------------------- #
# HTML assembly
# --------------------------------------------------------------------------- #
def _stat_tile(value: str, unit: str, label: str) -> str:
    return (f'<div class="tile"><div class="tile-v">{_esc(value)}'
            f'<span class="tile-u">{_esc(unit)}</span></div>'
            f'<div class="tile-l">{_esc(label)}</div></div>')


def _section(sec_id: str, kicker: str, heading: str, prose: str, body: str) -> str:
    return (f'<section id="{sec_id}">'
            f'<div class="kicker">{_esc(kicker)}</div>'
            f'<h2>{_esc(heading)}</h2>'
            f'<div class="prose">{prose}</div>{body}</section>')


def render_showcase(design: MotorDesign, *, session=None, name=None,
                    narrative=None, results=None, sweep_data=None,
                    artwork_mod=None, torque_steps=60, field_nr=36,
                    field_nphi=None, auto_footprint=True) -> str:
    """Render the complete showcase HTML document (see :func:`build_showcase`)."""
    payload, prose, gate, results = _collect_payload(
        design, session=session, name=name, narrative_text=narrative,
        results=results, sweep_data=sweep_data, artwork_mod=artwork_mod,
        torque_steps=torque_steps, field_nr=field_nr, field_nphi=field_nphi,
        auto_footprint=auto_footprint)

    r = results
    meta = payload["meta"]
    title = meta["title"]
    verdict_cls = "pass" if gate["passed"] else "fail"
    verdict_word = "DRIVE GATE: PASS" if gate["passed"] else "DRIVE GATE: FAIL"

    # ---- hero -------------------------------------------------------------
    tiles = "".join([
        _stat_tile(_fmt(r["tau_cont_mNm"], 3), "mNm", "continuous torque (±30%)"),
        _stat_tile(_fmt(r["kt_mNm_per_A"], 3), "mNm/A", "Kt"),
        _stat_tile(_fmt(r["i_cont_A"], 3), "A", "continuous current"),
        _stat_tile(_fmt(meta["od_mm"], 3), "mm", "board OD"),
    ])
    hero = f"""
<header class="hero">
  <nav class="topnav">
    <span class="brand">pcb-motor</span>
    <div class="navlinks">
      <a href="#architecture">architecture</a><a href="#board">the board</a>
      <a href="#hunt">the hunt</a><a href="#physics">physics</a>
      <a href="#drive">drive</a><a href="#fab">build it</a>
    </div>
  </nav>
  <div class="hero-grid">
    <div class="hero-copy">
      <div class="kicker">coreless axial-flux PCB motor</div>
      <h1>{_esc(title)}</h1>
      <div class="prose lead">{prose['hero']}</div>
      <div class="tiles">{tiles}</div>
      <div class="chip {verdict_cls}" title="{_esc(gate['statement'])}">{verdict_word}</div>
    </div>
    <div class="hero-viz">
      <div class="anim-wrap">
        <canvas id="motor-anim" aria-label="spinning motor and field animation"></canvas>
        <div class="anim-hud" id="anim-hud"></div>
      </div>
      <div class="anim-controls" id="anim-controls"></div>
      <div class="caption">Live: the {meta['poles']}-pole rotor turning over the real
      coil artwork. Colour wash = the Biot&#8211;Savart airgap field B<sub>z</sub>
      rotating with the magnets; coils light up with their commutated phase current;
      the bar tracks instantaneous torque.</div>
    </div>
  </div>
</header>"""

    # ---- brief ------------------------------------------------------------
    req_rows = _requirements_rows(session)
    if req_rows:
        cells = "".join(
            f'<div class="req"><div class="req-l">{_esc(x["label"])}</div>'
            f'<div class="req-v">{_esc(x["value"])}'
            f'{(" " + _esc(x["unit"])) if x["unit"] else ""}</div></div>'
            for x in req_rows)
        req_html = f'<div class="req-grid">{cells}</div>'
    else:
        req_html = ('<p class="muted">No requirements file in this session — the '
                    'page judges the design against its own drive settings.</p>')
    brief = _section("brief", "stage 1", "The brief", prose["brief"], req_html)

    # ---- architecture (winding ring + exploded stack) -----------------------
    if design.back_iron:
        _n_iron = payload["stack"].get("n_iron", 0)
        iron_note = (f"<strong>Back iron: yes</strong> — {_n_iron} mild-steel return "
                     f"plate{'s' if _n_iron != 1 else ''} (grey, hatched) flush behind "
                     f"the stator{'s' if _n_iron != 1 else ''}.")
    else:
        iron_note = ("<strong>Back iron: none</strong> — fully coreless; the only "
                     "solids are FR4, copper and the magnet rotor.")
    arch_body = f"""
<div class="duo">
  <figure class="panel">
    <div id="winding-ring"></div>
    <figcaption>Star-of-slots: {meta['slots']} tooth coils dealt to three phases
    (A/B/C, ±&nbsp;winding sense), {meta['poles']} rotor poles.
    Fundamental winding factor kw<sub>1</sub> = {_fmt(r['winding_factor'], 3)}.</figcaption>
  </figure>
  <figure class="panel">
    <div id="stack-view"></div>
    <div class="stack-controls"><label>explode
      <input type="range" id="explode" min="0" max="1" step="0.01" value="0"></label></div>
    <figcaption>The axial sandwich, to scale from the design dimensions.
    Drag the slider (or just scroll past) to pull it apart. {iron_note}</figcaption>
  </figure>
</div>"""
    architecture = _section("architecture", "the machine", "Architecture",
                            prose["architecture"], arch_body)

    # ---- board (copper viewer) ---------------------------------------------
    if payload["artwork"]["source"] == "kicad_mod":
        art_banner = ""
        src_note = "parsed from the production <code>.kicad_mod</code> footprint"
    else:
        art_banner = (f'<div class="preview-warn"><strong>&#9888;</strong> '
                      f'{_esc(payload["artwork"]["notice"])}</div>')
        src_note = "regenerated from the winding geometry (see the notice above)"
    board_body = f"""
{art_banner}<div class="viewer-wrap panel">
  <div class="viewer-toolbar" id="viewer-toolbar"></div>
  <div id="copper-viewer" class="viewer"></div>
  <div class="caption">Board artwork {src_note}. Scroll/pinch to zoom, drag to pan,
  double-click to reset.</div>
</div>"""
    board = _section("board", "the stator", "The board itself",
                     prose["board"], board_body)

    # ---- hunt (sweep charts) -------------------------------------------------
    if payload["sweep"]:
        hunt_body = ('<div id="sweep-charts" class="chart-grid"></div>'
                     '<div class="caption">Each point is a full engine re-evaluation '
                     'of the design at that trace width. The marked width is the one '
                     'this design committed to.</div>')
    else:
        hunt_body = ('<p class="muted">No sweep data was attached to this build — '
                     'run <code>pcb-motor showcase --sweep 0.15,0.2,0.3,0.4,0.5</code> '
                     'to add the trade-off study.</p>')
    hunt = _section("hunt", "the trade-off", "The design hunt",
                    prose["hunt"], hunt_body)

    # ---- physics --------------------------------------------------------------
    physics_body = f"""
<div class="duo">
  <figure class="panel">
    <div class="field-wrap"><canvas id="field-map"></canvas></div>
    <div class="caption" id="field-caption">Airgap B<sub>z</sub> at the front copper
    plane (z = {_fmt(payload['field']['z_mm'], 3)} mm), computed by Biot&#8211;Savart
    on the Amperian magnet loops. Peak |B<sub>z</sub>| =
    {_fmt(payload['field']['bz_peak_T'], 3)} T.
    Hover for values.</div>
  </figure>
  <div class="col">
    <figure class="panel"><div id="bz-profile"></div>
      <figcaption>B<sub>z</sub> around the ring at the mean coil radius — the wave the
      copper rides on.</figcaption></figure>
    <figure class="panel"><div id="torque-chart"></div>
      <figcaption>Torque through one electrical period at
      {_fmt(payload['torque']['i_amp'], 3)} A: commutated (flat — coreless, nothing to
      cog against; ripple {_fmt(payload['torque']['ripple_pct'], 3)}%) and the frozen-current
      torque-angle characteristic it rides on.</figcaption></figure>
  </div>
</div>"""
    physics = _section("physics", "the model", "Physics, from the real geometry",
                       prose["physics"], physics_body)

    # ---- thermal ---------------------------------------------------------------
    th_tiles = "".join([
        _stat_tile(_fmt(r["i_cont_A"], 3), "A", "continuous phase current"),
        _stat_tile(_fmt(r["copper_loss_W"], 3), "W", "copper loss at the limit"),
        _stat_tile(f"{design.temp_limit_c:g}", "°C", "board temperature limit"),
        _stat_tile(_fmt(r["current_density_A_mm2"], 3), "A/mm²",
                   "current density (neck)"),
        _stat_tile(_fmt(r["v_drive_cont_V"], 3), "V", "drive voltage at I_cont"),
        _stat_tile(_fmt(r["r_phase_hot_ohm"], 3), "Ω", "phase R, hot"),
    ])
    thermal = _section("thermal", "how hard can you push it",
                       "Thermal & continuous rating",
                       prose["thermal"], f'<div class="tiles wide">{th_tiles}</div>')

    # ---- drive gate ---------------------------------------------------------------
    choke = _bom(design, results, gate)["choke"]
    gate_rows = f"""
<div class="gate {verdict_cls}">
  <div class="gate-word">{'PASS' if gate['passed'] else 'FAIL'}</div>
  <div class="gate-body">
    <p class="gate-statement">{_esc(gate['statement'])}</p>
    <div class="gate-nums">
      <div><span>{_fmt(gate['l_phase_uH'], 3)} µH</span>phase inductance (air-core)</div>
      <div><span>{_fmt(gate['ripple_pp'], 3)} A pp</span>worst-case PWM ripple</div>
      <div><span>{_fmt(gate['budget_a'], 3)} A</span>ripple budget
        ({design.drive_ripple_frac:.0%} of I_cont)</div>
      <div><span>{('—' if gate['passed'] else _fmt_big(gate['l_ext_uH']) + ' µH')}</span>
        external L needed / phase</div>
    </div>
    {'' if gate['passed'] else
     f'<blockquote class="choke"><strong>Choke shopping spec:</strong> '
     f'{_esc(choke["text"]) if choke else ""}</blockquote>'}
  </div>
</div>"""
    drive = _section("drive", "the honest part", "Can you actually drive it?",
                     prose["drive"], gate_rows)

    # ---- fab & BOM -----------------------------------------------------------------
    bom = _bom(design, results, gate)
    mag_rows = "".join(
        f'<tr><td class="qty">{m["qty"]}×</td><td>{_esc(m["desc"])}</td></tr>'
        for m in bom["magnets"])
    choke_row = ("" if bom["choke"] is None else
                 f'<tr><td class="qty">3×</td><td>{_esc(bom["choke"]["buy"])} '
                 f'shielded power inductor, I_sat ≥ {bom["choke"]["i_sat_a"]:g} A, '
                 f'DCR ≤ {bom["choke"]["dcr_ohm"]:g} Ω (series, one per phase)</td></tr>')
    extra_rows = "".join(f'<tr><td class="qty"></td><td>{_esc(x)}</td></tr>'
                         for x in bom["extras"])
    fab_body = f"""
<div class="duo">
  <div class="panel">
    <h3>Boards</h3>
    <table class="bom"><tbody>
      <tr><td class="qty">{bom['boards']['qty']}×</td>
          <td>{_esc(bom['boards']['desc'])}</td></tr>
      <tr><td class="qty"></td><td class="muted">{_esc(bom['boards']['rule'])}</td></tr>
    </tbody></table>
  </div>
  <div class="panel">
    <h3>Magnets &amp; the rest</h3>
    <table class="bom"><tbody>{mag_rows}{choke_row}{extra_rows}</tbody></table>
  </div>
</div>"""
    fab = _section("fab", "shopping list", "Fab & BOM", prose["fab"], fab_body)

    # ---- full parameter table ---------------------------------------------------------
    groups = _param_groups(design, results, gate)
    gtables = []
    for g in groups:
        rows = "".join(
            f'<tr><td>{_esc(x["label"])}</td><td>{_esc(x["value"])}'
            f'<span class="unit"> {_esc(x["unit"])}</span></td></tr>'
            for x in g["rows"])
        gtables.append(f'<div class="pgroup"><h3>{_esc(g["group"])}</h3>'
                       f'<table><tbody>{rows}</tbody></table></div>')
    warn_html = ""
    if payload["warnings"]:
        items = "".join(f"<li>{_esc(w)}</li>" for w in payload["warnings"])
        warn_html = f'<div class="warnbox"><h3>Model warnings</h3><ul>{items}</ul></div>'
    params = _section(
        "params", "every number", "Full design parameters",
        '<p>The complete grouped parameter set — inputs the design chose and '
        'outputs the engine computed. This is the same data the datasheet carries.</p>',
        f'<div class="pgrid">{"".join(gtables)}</div>{warn_html}')

    # ---- verdict / honesty ------------------------------------------------------------
    honesty = f"""
<section id="verdict">
  <div class="kicker">the fine print</div>
  <h2>Verdict</h2>
  <div class="prose">{prose['verdict']}</div>
  <div class="honesty panel">
    <p><strong>Model honesty:</strong> this is an analytical, feasibility-grade
    model — treat absolute torque as <strong>±30%</strong>. The field solver is
    validated to &lt;1% against closed-form solutions; the error budget is dominated
    by magnet Br tolerance and fringing, your actual assembled air gap (the single
    most sensitive parameter), and copper etching variation. Relative comparisons
    between designs are much better than ±30%. Calibrate against FEMM or a bench
    coil before committing money to a build.</p>
  </div>
</section>"""

    footer = """
<footer>
  <p>Generated by <a href="https://github.com/paristhomas/pcb_motor">pcb-motor</a>.
  This page is self-contained: every number, polygon and field sample was computed
  by the engine and embedded at build time.</p>
</footer>"""

    data_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    css = _asset("showcase.css")
    js = _asset("showcase.js")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{_esc(title)} — a PCB motor, shown properly</title>
<style>
{css}
</style>
</head>
<body>
<script id="motor-data" type="application/json">{data_json}</script>
{hero}
<main>
{brief}
{architecture}
{board}
{hunt}
{physics}
{thermal}
{drive}
{fab}
{params}
{honesty}
</main>
{footer}
<script>
{js}
</script>
</body>
</html>"""


def build_showcase(design: MotorDesign, out_path: str | Path, *, session=None,
                   name=None, narrative=None, results=None, sweep_data=None,
                   artwork_mod=None, torque_steps: int = 60, field_nr: int = 36,
                   field_nphi: int | None = None, auto_footprint: bool = True) -> str:
    """Build the showcase page for ``design`` and write it to ``out_path``.

    Parameters
    ----------
    session:
        Optional :class:`~pcb_motor.session.Session`; supplies the title, the
        requirements panel, a ``narrative.md`` (if present and ``narrative`` is
        not given) and the production footprint artwork (if present).
    narrative:
        Markdown text (or a path to it) with ``## <section>`` headings — see
        :data:`NARRATIVE_SECTIONS`. Missing sections fall back to generated prose.
    sweep_data:
        Output of :func:`trace_width_sweep` (or the same shape) for the
        design-hunt charts. ``None`` renders the section with a how-to note.
    torque_steps / field_nr / field_nphi:
        Fidelity knobs (mostly for tests; defaults are production quality).
    auto_footprint:
        When the session has no production footprint, build one automatically
        (tapered designs only — see :func:`_resolve_artwork`). Set ``False``
        to force the announced preview artwork.
    """
    if narrative is None and session is not None:
        cand = session.dir / "narrative.md"
        if cand.exists():
            narrative = cand.read_text(encoding="utf-8")
    elif narrative is not None and isinstance(narrative, (str, Path)):
        p = Path(narrative)
        # Treat as a path only if it plausibly is one (short, exists on disk).
        if len(str(narrative)) < 4096 and p.exists() and p.is_file():
            narrative = p.read_text(encoding="utf-8")
    html_text = render_showcase(
        design, session=session, name=name, narrative=narrative, results=results,
        sweep_data=sweep_data, artwork_mod=artwork_mod, torque_steps=torque_steps,
        field_nr=field_nr, field_nphi=field_nphi, auto_footprint=auto_footprint)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return str(out)
