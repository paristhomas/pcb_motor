"""Export generated coil geometry to a KiCad footprint (``.kicad_mod``).

A coil's raw ``polylines`` (list of ``(K, 3)`` arrays in metres) are emitted as
constant-width ``fp_line`` segments on a copper layer. In KiCad those copper
graphic lines render as actual copper -- i.e. the trace shape -- so the file can
be dropped straight into a project (``File > Import > Graphics`` is *not* needed;
just place it as a footprint, or open it in the footprint editor) and the coil
appears as copper you can connect to.

Coordinates are converted to millimetres. KiCad's Y axis points *down* whereas
the simulator (and matplotlib) use Y *up*, so Y is negated by default
(``flip_y=True``) to keep the artwork visually identical to the report figure.

Only the X-Y shape and trace width are exported; layer stack-up, vias and pad
connections are left to the user (a PCB coil is normally hand-stitched between
layers anyway). For the production two-sided filled-copper footprint with
net-bearing terminal pads and via stitching, see
:mod:`pcb_motor.kicad.footprint`.
"""

from __future__ import annotations

import numpy as np

# Modern KiCad (7/8) footprint file-format version stamp.
_KICAD_VERSION = "20240108"


def coil_to_kicad_mod(
    polylines: list[np.ndarray],
    trace_width_m: float,
    *,
    name: str = "pcb_motor_coil",
    layer: str = "F.Cu",
    flip_y: bool = True,
    width_fn=None,
) -> str:
    """Render ``polylines`` as a KiCad ``.kicad_mod`` footprint string.

    ``trace_width_m`` sets the copper line width. ``layer`` is any KiCad copper
    layer name (``F.Cu``, ``B.Cu``, ``In1.Cu`` ...). Each polyline becomes a run
    of connected ``fp_line`` segments at that width.

    ``width_fn`` (optional) maps a segment-midpoint radius [m] to a trace width
    [m], overriding ``trace_width_m`` per segment -- this renders tapered (wedge)
    windings as stepped-width segments, which at the coil discretisation
    (~``coil_resolution_m`` per step) is well within fab tolerance of the true
    wedge outline.
    """
    w_mm = trace_width_m * 1.0e3
    ysign = -1.0 if flip_y else 1.0

    lines: list[str] = [
        f'(footprint "{name}"',
        f"  (version {_KICAD_VERSION})",
        '  (generator "pcb_motor")',
        f'  (layer "{layer}")',
        "  (attr through_hole)",
        f'  (fp_text reference "REF**" (at 0 0) (layer "F.SilkS") (hide yes)'
        " (effects (font (size 1 1) (thickness 0.15))))",
        f'  (fp_text value "{name}" (at 0 0) (layer "F.Fab") (hide yes)'
        " (effects (font (size 1 1) (thickness 0.15))))",
    ]

    for pl in polylines:
        pl = np.asarray(pl, dtype=float)
        if pl.shape[0] < 2:
            continue
        xy = pl[:, :2] * 1.0e3  # metres -> mm
        if width_fn is not None:
            mid = 0.5 * (pl[:-1, :2] + pl[1:, :2])
            r_mid = np.hypot(mid[:, 0], mid[:, 1])
            seg_w_mm = np.asarray(width_fn(r_mid), dtype=float) * 1.0e3
        else:
            seg_w_mm = np.full(xy.shape[0] - 1, w_mm)
        for ((x0, y0), (x1, y1)), sw in zip(zip(xy[:-1], xy[1:]), seg_w_mm):
            lines.append(
                f"  (fp_line (start {x0:.4f} {ysign * y0:.4f})"
                f" (end {x1:.4f} {ysign * y1:.4f})"
                f' (stroke (width {sw:.4f}) (type solid)) (layer "{layer}"))'
            )

    lines.append(")")
    return "\n".join(lines) + "\n"


def write_coil_kicad_mod(
    path: str,
    polylines: list[np.ndarray],
    trace_width_m: float,
    **kwargs,
) -> int:
    """Write a ``.kicad_mod`` footprint to ``path``; return the ``fp_line`` count.

    The file is written with CRLF line endings -- KiCad saves CRLF, so anything
    else makes every subsequent in-KiCad save a whole-file diff.
    """
    text = coil_to_kicad_mod(polylines, trace_width_m, **kwargs)
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)
    return text.count("(fp_line ")


def _layer0_polylines(polylines: list[np.ndarray]) -> list[np.ndarray]:
    """Keep only the polylines on the nearest (first) copper layer.

    Geometry is stacked in z by ``board_thickness`` per copper layer; for a
    footprint on a single layer we export just the plane closest to the rotor.
    """
    if not polylines:
        return []
    z0 = min(float(np.asarray(pl)[0, 2]) for pl in polylines)
    return [pl for pl in polylines if np.isclose(float(np.asarray(pl)[0, 2]), z0)]


def _first_sector_polylines(
    polylines: list[np.ndarray], n_slots: int
) -> list[np.ndarray]:
    """Keep polylines whose centroid falls in the first angular sector.

    For the concentrated topology this isolates a single tooth's coil; for the
    other topologies it just slices out one wedge of the artwork.
    """
    if not polylines:
        return []
    half = np.pi / max(1, int(n_slots))  # half-sector half-width
    out = []
    for pl in polylines:
        pl = np.asarray(pl, dtype=float)
        c = pl[:, :2].mean(axis=0)
        ang = float(np.arctan2(c[1], c[0]))
        # wrap to (-pi, pi]; first sector is centred on angle 0.
        if -half <= ang < half:
            out.append(pl)
    return out
