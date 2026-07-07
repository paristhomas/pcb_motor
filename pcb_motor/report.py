"""One self-contained HTML report for a single motor design.

Combines, in one shareable ``design_report.html`` with no server dependency:
the setup figure (winding + rotor + axial stack) and the key-parameters table
(``session.headline_rows``). The page shell and CSS are inlined here so the
report has no external dependency beyond matplotlib.
"""

from __future__ import annotations

import base64
import html

from .design import MotorDesign
from .evaluate import evaluate_design
from . import session, viz


CSS = """
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
         background: #eef1f4; color: #1c2833; }
  .wrap { max-width: 1000px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 1.5rem; margin: 0 0 4px; }
  .sub { color: #5d6d7e; margin-bottom: 18px; }
  .band { font-size: 0.95rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
          padding: 8px 14px; border-radius: 8px; margin: 22px 0 12px; }
  .band.calc { background: #d5f5e3; color: #145a32; }
  .band small { font-weight: 400; text-transform: none; letter-spacing: 0; color: inherit; opacity: .8; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  section { background: #fff; border-radius: 10px; padding: 14px 18px;
            box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  h2 { font-size: 1.02rem; margin: 0 0 8px; border-bottom: 1px solid #eaecee; padding-bottom: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  td { padding: 3px 0; vertical-align: top; }
  td:first-child { color: #5d6d7e; width: 46%; }
  td:last-child { font-variant-numeric: tabular-nums; }
  .warn { grid-column: 1 / -1; border-left: 4px solid #b9770e; }
  .chart { grid-column: 1 / -1; text-align: center; }
  .chart img { max-width: 100%; }
  footer { color: #85929e; font-size: 0.8rem; margin-top: 18px; }
"""


def _page(title: str, body: str, extra_css: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}{extra_css}</style></head>
<body><div class="wrap">{body}</div></body></html>"""


def _table(title: str, rows: list[tuple[str, str]], cls: str = "") -> str:
    body = "\n".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    klass = f' class="{cls}"' if cls else ""
    return f'<section{klass}><h2>{title}</h2><table>{body}</table></section>'


def _fig_b64(fig) -> str:
    """Base64 PNG of a matplotlib figure (and close it)."""
    import matplotlib.pyplot as plt
    png = viz.fig_to_png_bytes(fig)
    plt.close(fig)
    return base64.b64encode(png).decode("ascii")


def _setup_chart_png(design: MotorDesign) -> str:
    """Base64 PNG of the combined setup figure (winding + rotor + stack)."""
    return _fig_b64(viz.plot_setup(design))


def _img_section(title: str, b64: str, *, mime: str = "image/png",
                 note: str = "") -> str:
    """One report section wrapping a base64 image (PNG or GIF)."""
    cap = f'<p class="sub">{html.escape(note)}</p>' if note else ""
    return (f'<section class="chart"><h2>{html.escape(title)}</h2>'
            f'<img alt="{html.escape(title)}" src="data:{mime};base64,{b64}">{cap}</section>')


def _rich_sections(design: MotorDesign, results: dict) -> list[str]:
    """The rotating-field & torque analysis figures (Biot-Savart; slow-ish).

    Driven at the evaluated continuous current so the torque plot is the real
    operating torque. Returns a list of HTML <section> blocks.
    """
    i_cont = float(results.get("i_cont_A") or 1.0)
    out = []
    out.append(_img_section(
        "Torque vs angle (FOC ripple)",
        # 120x120 is plenty for the report visual; the standalone default (180x168)
        # is only needed to pin the sub-0.1% ripple magnitude, not for the picture.
        _fig_b64(viz.plot_torque_ripple(design, i_amp=i_cont, n_steps=120, n_phi=120)),
        note=f"Field-oriented control (i_d=0, i_q=const) at {i_cont:.2f} A continuous. "
             "Torque holds ~constant as the rotor turns; the plot is zoomed onto the "
             "residual ripple (near the numerical floor for this coreless stator)."))
    out.append(_img_section(
        "Field interaction — torque-producing shear",
        _fig_b64(viz.plot_shear_stress(design, i_amp=i_cont, n_grid=60)),
        note="The tangential (sideways) force per unit area, -J_r*B_z, where the coil "
             "current crosses the magnet axial field. Its integral x radius is the shaft "
             "torque. Concentrated where the coils overlap the magnet rings."))
    gif_b64 = base64.b64encode(
        viz.shear_interaction_gif_bytes(design, i_amp=i_cont, n_frames=24, n_grid=48)
    ).decode("ascii")
    out.append(_img_section(
        "Rotating field interaction", gif_b64, mime="image/gif",
        note="The shear pattern sweeping round as the rotor turns one electrical period "
             "under FOC -- its integral (torque) stays ~constant."))
    out.append(_img_section(
        "Exported KiCad traces", _fig_b64(viz.plot_kicad_traces(design)),
        note="The continuous spiral coils written to the .kicad_mod footprint, coloured by phase."))
    out.append(_img_section(
        "Rotor-magnet field B_z (reference)",
        _fig_b64(viz.plot_magnet_field(design, n_grid=64)),
        note="B_z from the rotor magnets alone. For an axial-flux machine B_z is the "
             "torque-coupling component: radial current x B_z gives the shear above."))
    out.append(_img_section(
        "Stator-coil field B_z (reference)",
        _fig_b64(viz.plot_coil_field(design, i_amp=i_cont, n_grid=60)),
        note=f"B_z from the energised coils alone at {i_cont:.2f} A. The map looks "
             "lopsided only because of the instantaneous FOC phase split (phase A at "
             "peak gives currents 1 : -0.5 : -0.5) -- not a per-phase imbalance; each "
             "phase alone makes an equal-strength field (see next)."))
    out.append(_img_section(
        "Stator field over one electrical period (FOC)",
        _fig_b64(viz.plot_stator_field_sequence(design, i_amp=i_cont, n_panels=4)),
        note="The same stator field at four commutation instants. As the FOC currents "
             "advance, the lobe pattern rotates symmetrically around the ring -- no "
             "phase is privileged, confirming the single-frame lopsidedness is just the "
             "current split."))
    return out


def render_design_report(design: MotorDesign, *,
                         name: str | None = None, results: dict | None = None,
                         rich: bool = False) -> str:
    """Return a complete HTML document for ``design``.

    With ``rich=True`` the report also embeds the Biot-Savart rotating-field and
    torque-ripple analysis (torque vs angle, torque-producing shear, KiCad
    traces, coil/magnet field, and an animated rotating-field GIF). That path
    runs the field kernel many times, so it takes ~a minute -- fine for a one-off
    report.
    """
    if results is None:
        results = evaluate_design(design)

    n_poles = 2 * design.pole_pairs
    title = name or f"{design.winding_topology} {design.n_slots}N{n_poles}P {design.magnet_grade}"

    chart = _setup_chart_png(design)
    rows = [(html.escape(k), v) for k, v in session.headline_rows(design, results)]

    header = (
        f"<h1>PCB Motor Design &mdash; {html.escape(title)}</h1>"
        '<div class="sub">pcb-motor Biot-Savart model</div>'
    )

    # Grid: setup figure, the key-parameters table, and any warnings.
    grid = [
        '<section class="chart"><h2>Setup</h2>'
        f'<img alt="setup figure" src="data:image/png;base64,{chart}"></section>',
        _table("Key design parameters", rows),
    ]
    if results.get("warnings"):
        items = "\n".join(f"<li>{html.escape(w)}</li>" for w in results["warnings"])
        grid.append(f'<section class="warn"><h2>Warnings</h2><ul>{items}</ul></section>')

    body = (
        header
        + '<div class="band calc">Key design parameters '
          '<small>&mdash; what you chose and what it buys you</small></div>'
        + '<div class="grid">' + "".join(grid) + "</div>"
    )
    if rich:
        rich_grid = "".join(_rich_sections(design, results))
        body += (
            '<div class="band calc">Field &amp; torque analysis '
            '<small>&mdash; Biot-Savart rotating-field figures</small></div>'
            + '<div class="grid">' + rich_grid + "</div>"
        )
    body += (
        '<footer>pcb-motor analytical Biot-Savart model, roughly &#177;30% on '
        'absolute torque. Calibrate against FEMM or a bench coil before '
        'committing to a build.</footer>'
    )
    return _page(f"PCB Motor Design - {title}", body)


def build_design_report(design: MotorDesign, out_path: str, *,
                        name: str | None = None, results: dict | None = None,
                        rich: bool = False) -> str:
    """Render the report and write it to ``out_path``; return the path."""
    html_text = render_design_report(design, name=name, results=results, rich=rich)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return out_path
