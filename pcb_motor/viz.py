"""Plotting helpers for the pcb_motor simulator.

Three figures: the stator coil layout (top-down x-y), the magnet rotor ring,
and a B_z contour over the stator plane. All use the non-interactive Agg
backend so they render headless (CI, bencher image attachments).
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # headless backend; must precede pyplot import

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Wedge

from .design import CoilGeometry, MotorDesign, RotorConfig


def plot_coil_layout(geo: CoilGeometry) -> "matplotlib.figure.Figure":
    """Top-down (x-y) view of the stator coil.

    Each polyline in ``geo.polylines`` is drawn; segments are coloured by phase
    (0/1/2) so the three windings are distinguishable. If ``polylines`` is empty
    we fall back to drawing each per-segment vector from
    ``midpoints_m - dvec_m/2`` to ``midpoints_m + dvec_m/2``.
    """
    fig, ax = plt.subplots(figsize=(6, 6))

    phase_colors = {0: "tab:red", 1: "tab:green", 2: "tab:blue"}

    if geo.polylines:
        # Colour each polyline by the phase of its nearest segment midpoint.
        mids = geo.midpoints_m[:, :2] if len(geo.midpoints_m) else None
        for pl in geo.polylines:
            pl = np.asarray(pl, dtype=float)
            if pl.shape[0] < 2:
                continue
            color = "tab:gray"
            if mids is not None and len(mids):
                centroid = pl[:, :2].mean(axis=0)
                idx = int(np.argmin(np.linalg.norm(mids - centroid, axis=1)))
                color = phase_colors.get(int(geo.phase[idx]), "tab:gray")
            ax.plot(pl[:, 0], pl[:, 1], color=color, linewidth=0.8)
    else:
        # Fall back to per-segment vectors, coloured by phase.
        mids = np.asarray(geo.midpoints_m, dtype=float)
        dvec = np.asarray(geo.dvec_m, dtype=float)
        starts = mids - dvec / 2.0
        ends = mids + dvec / 2.0
        for i in range(mids.shape[0]):
            color = phase_colors.get(int(geo.phase[i]), "tab:gray")
            ax.plot(
                [starts[i, 0], ends[i, 0]],
                [starts[i, 1], ends[i, 1]],
                color=color,
                linewidth=0.8,
            )

    # Legend proxies for the phases.
    handles = [
        plt.Line2D([0], [0], color=c, label=f"phase {p}")
        for p, c in phase_colors.items()
    ]
    ax.legend(handles=handles, loc="upper right", fontsize="small")

    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title(f"Coil layout: {geo.n_turns} turns x {geo.n_layers} layers")
    fig.tight_layout()
    return fig


def plot_motor_config(rotor: RotorConfig) -> "matplotlib.figure.Figure":
    """Draw the magnet ring as alternating N (red) / S (blue) wedges.

    ``2 * rotor.pole_pairs`` poles fill the annulus between
    ``magnet_r_inner_m`` and ``magnet_r_outer_m``. ``pole_coverage`` shrinks each
    wedge about its centre, leaving an inter-pole gap.
    """
    fig, ax = plt.subplots(figsize=(6, 6))

    n_poles = 2 * rotor.pole_pairs
    _draw_magnets(ax, rotor)

    handles = [
        plt.Line2D([0], [0], marker="s", color="w", markerfacecolor="tab:red",
                   markersize=10, label="N"),
        plt.Line2D([0], [0], marker="s", color="w", markerfacecolor="tab:blue",
                   markersize=10, label="S"),
    ]
    ax.legend(handles=handles, loc="upper right", fontsize="small")
    ax.set_title(f"Magnet ring: {rotor.pole_pairs} pole pairs ({n_poles} poles)")
    fig.tight_layout()
    return fig


def _draw_coil(ax, geo: CoilGeometry, one_layer: bool = True) -> None:
    """Draw the winding top-down on ``ax``, coloured by phase (A/B/C)."""
    phase_colors = {0: "tab:red", 1: "tab:green", 2: "tab:blue"}
    z_keep = None
    if one_layer and geo.polylines:
        z_keep = geo.polylines[0][0, 2]
    mids = geo.midpoints_m[:, :2]
    for pl in geo.polylines:
        pl = np.asarray(pl, dtype=float)
        if pl.shape[0] < 2:
            continue
        if z_keep is not None and not np.isclose(pl[0, 2], z_keep):
            continue
        centroid = pl[:, :2].mean(axis=0)
        idx = int(np.argmin(np.linalg.norm(mids - centroid, axis=1)))
        ax.plot(pl[:, 0] * 1e3, pl[:, 1] * 1e3,
                color=phase_colors.get(int(geo.phase[idx]), "tab:gray"), lw=0.6)
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
    ax.legend(handles=[plt.Line2D([0], [0], color=c, label=f"phase {'ABC'[p]}")
                       for p, c in phase_colors.items()], loc="upper right", fontsize="small")


def _draw_stack(ax, design: MotorDesign) -> None:
    """Axial r-z cross-section of the ACTUAL configuration: magnet rotor(s),
    ``n_stators`` stator PCBs (+copper layers), back-iron, air gaps.

    - ``n_stators == 2``: rotor at z=0, one board each side (the default).
    - ``n_stators == 1``: rotor at z=0, one board on the +z side only.
    - ``rotor_sides == 2``: dual-rotor sandwich -- the single board sits between
      the z=0 rotor and a second magnet plane centred at ``2 * stator_z``.
    """
    from matplotlib.patches import Rectangle
    ri = design.r_inner_m * 1e3
    ro = design.r_outer_m * 1e3
    t = design.magnet_thickness_m * 1e3
    gap = design.air_gap_m * 1e3
    bd = design.board_thickness_m * 1e3
    iron = 0.5

    z_min, z_max = 0.0, 0.0

    def band(z0, z1, col, label):
        nonlocal z_min, z_max
        ax.add_patch(Rectangle((ri, z0), ro - ri, z1 - z0, facecolor=col,
                               edgecolor="k", lw=0.6))
        ax.text(ro + 1, 0.5 * (z0 + z1), label, va="center", fontsize=7)
        z_min = min(z_min, z0, z1); z_max = max(z_max, z0, z1)

    band(-t / 2, t / 2, "#cf9bcf", "rotor magnet")
    sides = (+1, -1) if design.n_stators >= 2 else (+1,)
    for sgn in sides:
        b0 = sgn * (t / 2 + gap); b1 = b0 + sgn * bd
        zlo, zhi = min(b0, b1), max(b0, b1)
        band(zlo, zhi, "#ffcc66", "stator PCB")
        for L in range(design.copper_layers):
            zc = zlo + (L + 0.5) / design.copper_layers * (zhi - zlo)
            ax.plot([ri, ro], [zc, zc], color="#b5651d", lw=1.4)
        if design.back_iron:
            i0 = b1; i1 = i0 + sgn * iron
            band(min(i0, i1), max(i0, i1), "#888888", "back iron")
    if design.rotor_sides == 2:
        # Second magnet plane of the sandwich, centred at 2*stator_z.
        zc2 = t + 2 * gap + bd            # = 2 * stator_z_m, in mm
        band(zc2 - t / 2, zc2 + t / 2, "#cf9bcf", "rotor magnet (2nd)")
    ax.axhline(0, color="gray", lw=0.4, ls=":")
    pad = 0.4 * max(z_max - z_min, 1.0)
    ax.set_xlim(ri - 6, ro + 13); ax.set_ylim(z_min - pad, z_max + pad)
    ax.set_xlabel("radius [mm]"); ax.set_ylabel("axial z [mm]")


def _stack_desc(design: MotorDesign) -> str:
    """Human description of the axial topology (correct singular/plural)."""
    s = f"{design.n_stators} stator" + ("" if design.n_stators == 1 else "s")
    if design.rotor_sides == 2:
        s = "dual rotor, " + s
    return s


def _draw_magnets(ax, rotor: RotorConfig) -> None:
    """Draw the rotor magnets (arc wedges or round two-ring discs) in mm."""
    from matplotlib.patches import Circle
    from .magnets import active_rings, is_round
    n_poles = 2 * rotor.pole_pairs
    pitch = 360.0 / n_poles
    if is_round(rotor.magnet_topology):
        rings = [(ring_r * 1e3, disc_d * 1e3 / 2) for ring_r, disc_d in active_rings(rotor)]
        for k in range(n_poles):
            ang = np.deg2rad((k + 0.5) * pitch)
            color = "tab:red" if k % 2 == 0 else "tab:blue"
            for ring_r, disc_r in rings:
                ax.add_patch(Circle((ring_r * np.cos(ang), ring_r * np.sin(ang)),
                                    disc_r, facecolor=color, edgecolor="k", lw=0.4))
        lim = max(ring_r + disc_r for ring_r, disc_r in rings) * 1.12
    else:
        ri = rotor.magnet_r_inner_m * 1e3
        ro = rotor.magnet_r_outer_m * 1e3
        arc = pitch * rotor.pole_coverage
        for k in range(n_poles):
            c = k * pitch
            ax.add_patch(Wedge((0, 0), ro, c - arc / 2, c + arc / 2, width=ro - ri,
                               facecolor="tab:red" if k % 2 == 0 else "tab:blue",
                               edgecolor="k", lw=0.3))
        lim = ro * 1.12
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_aspect("equal")
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")


def plot_stack(design: MotorDesign) -> "matplotlib.figure.Figure":
    """Axial stack cross-section (r-z slice)."""
    fig, ax = plt.subplots(figsize=(6, 5))
    _draw_stack(ax, design)
    ax.set_title(f"Axial stack: {_stack_desc(design)}, gap "
                 f"{design.air_gap_m*1e3:.1f}mm, {design.copper_layers} Cu/board")
    fig.tight_layout()
    return fig


def plot_setup(design: MotorDesign, geo: CoilGeometry | None = None
               ) -> "matplotlib.figure.Figure":
    """Combined setup figure: winding, magnet rotor, and axial stack -- one image
    per design point for the bencher report."""
    from .coils import build_coil, winding_factor
    if geo is None:
        geo = build_coil(design)
    rotor = design.rotor()
    kw = winding_factor(design)
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    _draw_coil(axes[0], geo)
    axes[0].set_title(f"{design.winding_topology}: {geo.n_turns} turns/phase "
                      f"x{geo.n_layers}L (1 layer shown)")

    n_poles = 2 * rotor.pole_pairs
    _draw_magnets(axes[1], rotor)
    axes[1].set_title(f"Rotor ({rotor.magnet_topology}): {n_poles} poles "
                      f"({rotor.pole_pairs}pp), {rotor.magnet_grade}")

    _draw_stack(axes[2], design)
    axes[2].set_title(f"Axial stack: {_stack_desc(design)}, gap "
                      f"{design.air_gap_m*1e3:.1f}mm")
    fig.suptitle(f"{design.n_slots}N{n_poles}P  (winding factor kw1={kw:.3f})", fontsize=11)
    fig.tight_layout()
    return fig


def plot_b_field(
    design: MotorDesign,
    n_grid: int = 48,
    use_real_field: bool = True,
) -> "matplotlib.figure.Figure":
    """Filled contour of B_z over the stator plane.

    By default this computes the REAL B_z from the rotor magnets (Amperian
    loops via ``magnets.magnet_segments`` + the vectorised Biot-Savart kernel
    ``field.b_field_at_points``, with iron images when ``back_iron`` is set).
    The kernel is vectorised over grid points, so this is cheap: the default
    48x48 grid takes ~1.5 s -- fine for reports and one-off figures, though
    still not something to put inside a sweep's inner loop.

    Set ``use_real_field=False`` for the old *synthetic placeholder*: a smooth
    ``cos(p*phi)`` pattern over the magnet annulus in arbitrary units --
    instant and deterministic, good enough to sketch the pole pattern (e.g. in
    tests), but NOT physical.
    """
    rotor = design.rotor()
    r_out = rotor.magnet_r_outer_m
    z_plane = rotor.stator_z_m()

    lim = r_out * 1.1
    xs = np.linspace(-lim, lim, n_grid)
    ys = np.linspace(-lim, lim, n_grid)
    xx, yy = np.meshgrid(xs, ys)

    use_real = bool(use_real_field)
    if use_real:
        from .field import b_field_at_points
        from .iron import with_iron_images
        from .magnets import magnet_segments

        # Visualisation only: a coarse magnet discretisation and field
        # resolution keep this quick. Accuracy here is for a readable
        # contour, not torque.
        source = with_iron_images(magnet_segments(rotor, n_arc=8), rotor)
        pts = np.column_stack(
            [xx.ravel(), yy.ravel(), np.full(xx.size, z_plane)]
        )
        b = b_field_at_points(source, pts, resolution_m=2.0e-3)
        bz = b[:, 2].reshape(xx.shape)

    if not use_real:
        phi = np.arctan2(yy, xx)
        rr = np.hypot(xx, yy)
        annulus = (rr >= rotor.magnet_r_inner_m) & (rr <= r_out)
        bz = np.cos(rotor.pole_pairs * phi)
        bz = np.where(annulus, bz, 0.0)

    fig, ax = plt.subplots(figsize=(6.5, 6))
    cf = ax.contourf(xx, yy, bz, levels=20, cmap="RdBu_r")
    fig.colorbar(cf, ax=ax, label="B_z [T]" if use_real else "B_z [a.u.]")
    ax.set_aspect("equal")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    src = "Biot-Savart" if use_real else "placeholder"
    ax.set_title(f"B_z at stator plane (z={z_plane*1e3:.2f} mm, {src})")
    fig.tight_layout()
    return fig


def fig_to_png_bytes(fig) -> bytes:
    """Render a figure to PNG bytes (for bencher ``ResultImage`` attachments)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Real-field figures (Biot-Savart): torque ripple, coil/magnet field, GIF,
# and the exported KiCad spiral traces. These call the vectorised field kernel,
# so they are fast enough for a one-off report but are NOT meant for sweeps.
# --------------------------------------------------------------------------- #
def _bz_grid(source, lim_m, z_plane_m, n_grid, resolution_m):
    """B_z [T] of ``source`` on a square x-y grid at height ``z_plane_m``."""
    from .field import b_field_at_points
    xs = np.linspace(-lim_m, lim_m, n_grid)
    ys = np.linspace(-lim_m, lim_m, n_grid)
    xx, yy = np.meshgrid(xs, ys)
    pts = np.column_stack([xx.ravel(), yy.ravel(), np.full(xx.size, z_plane_m)])
    bz = b_field_at_points(source, pts, resolution_m=resolution_m)[:, 2].reshape(xx.shape)
    return xx, yy, bz


def plot_torque_ripple(design: MotorDesign, i_amp: float = 1.0,
                       n_steps: int = 180, n_phi: int = 168
                       ) -> "matplotlib.figure.Figure":
    """FOC torque vs rotor angle over one electrical period.

    Field-oriented control (``i_d=0``, ``i_q`` constant, currents locked to the
    rotor) holds the torque ~constant; this plot zooms onto the residual **ripple**
    and annotates the mean torque and ripple %. The y-axis is auto-scaled to the
    ripple band (with a small floor) so a near-flat trace is still legible.
    """
    from .torque import torque_vs_angle
    out = torque_vs_angle(design, i_amp=i_amp, n_steps=n_steps, n_phi=n_phi)
    ang = out["elec_deg"]
    tc = out["tau_commutated_nm"] * 1e3   # mNm
    mean = out["mean_torque_nm"] * 1e3
    dev = float(np.max(np.abs(tc - mean)))
    pp = float(tc.max() - tc.min())        # peak-to-peak

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.plot(ang, tc, color="tab:red", lw=2.0, label="FOC torque (i_d=0, i_q=const)")
    ax.axhline(mean, color="tab:red", lw=0.8, ls=":", label=f"mean {mean:.2f} mNm")
    # Zoom to the ripple band: mean +/- a few x the deviation, with a sane floor
    # so a sub-percent ripple still shows as a readable band rather than a line.
    pad = max(3.0 * dev, 0.02 * abs(mean), 1e-3)
    ax.set_ylim(mean - pad, mean + pad)
    ax.set_xlim(0, 360)
    ax.set_xlabel("rotor angle [electrical degrees]")
    ax.set_ylabel(f"machine torque [mNm] @ {i_amp:g} A")
    ax.set_title(f"FOC torque vs angle — ripple {out['ripple_pct']:.2f}% "
                 f"(peak-to-peak {pp:.3f} mNm, mean {mean:.2f} mNm)")
    ax.legend(loc="upper right", fontsize="small")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig


def plot_magnet_field(design: MotorDesign, n_grid: int = 64,
                      resolution_m: float = 1.5e-3) -> "matplotlib.figure.Figure":
    """Filled B_z contour from the rotor magnets alone, at the stator plane."""
    from .magnets import magnet_segments
    rotor = design.rotor()
    z = rotor.stator_z_m()
    lim = _magnet_extent_m(rotor) * 1.08
    from .iron import with_iron_images
    src = with_iron_images(magnet_segments(rotor), rotor)
    xx, yy, bz = _bz_grid(src, lim, z, n_grid, resolution_m)

    fig, ax = plt.subplots(figsize=(6.5, 6))
    vmax = np.max(np.abs(bz)) or 1.0
    cf = ax.contourf(xx * 1e3, yy * 1e3, bz, levels=24, cmap="RdBu_r",
                     vmin=-vmax, vmax=vmax)
    fig.colorbar(cf, ax=ax, label="B_z [T]")
    _overlay_magnet_outlines(ax, rotor)
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
    ax.set_title(f"Magnet field B_z at stator plane (z={z*1e3:.2f} mm)")
    fig.tight_layout()
    return fig


def plot_coil_field(design: MotorDesign, i_amp: float = 1.0, n_grid: int = 64,
                    resolution_m: float = 1.0e-3) -> "matplotlib.figure.Figure":
    """Filled B_z contour from the energised stator coils alone.

    Phases are driven at one commutation instant (peak on phase A) at amplitude
    ``i_amp``; the plane sits a hair above the front copper layer.
    """
    from .coils import coil_current_source
    n_phases = int(design.n_phases)
    phase_off = np.arange(n_phases) * 2.0 * np.pi / n_phases
    i_phase = i_amp * np.cos(phase_off)            # instant with phase A at peak
    src = coil_current_source(design, i_phase)
    z = design.rotor().stator_z_m() - 0.3e-3       # just rotor-side of the copper
    lim = design.r_outer_m * 1.08
    xx, yy, bz = _bz_grid(src, lim, z, n_grid, resolution_m)

    fig, ax = plt.subplots(figsize=(6.5, 6))
    vmax = np.max(np.abs(bz)) or 1.0
    cf = ax.contourf(xx * 1e3, yy * 1e3, bz, levels=24, cmap="PuOr_r",
                     vmin=-vmax, vmax=vmax)
    fig.colorbar(cf, ax=ax, label="B_z [T]")
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
    # State the per-phase currents on the plot: with phase A at its peak the 3-phase
    # set is at 1 : -0.5 : -0.5 (cos of the phase offsets), so B and C coils carry
    # half current of opposite sign. That, not a bug, is why the map looks lopsided.
    names = [chr(ord("A") + k) for k in range(n_phases)]
    cur_str = ", ".join(f"{n}={c:+.2f}" for n, c in zip(names, i_phase))
    ax.set_title(f"Stator-coil field B_z @ {i_amp:g} A (phase A at peak)\n"
                 f"phase currents [A]: {cur_str}")
    fig.tight_layout()
    return fig


def plot_stator_field_sequence(design: MotorDesign, i_amp: float = 1.0,
                               n_panels: int = 4, n_grid: int = 48,
                               resolution_m: float = 1.5e-3
                               ) -> "matplotlib.figure.Figure":
    """Stator B_z at several FOC commutation instants -- it rotates symmetrically.

    Under field-oriented control the three phase currents are
    ``I_k = i_amp*cos(omega t - k*2pi/n)``; stepping ``omega t`` over one electrical
    period sweeps the stator-field lobe pattern smoothly around the ring. Showing a
    few instants side by side makes it clear the apparent lopsidedness of any single
    frame is just the instantaneous current split, and that the time-evolution is
    rotationally symmetric (no phase is privileged).
    """
    from .coils import coil_current_source
    n_phases = int(design.n_phases)
    phase_off = np.arange(n_phases) * 2.0 * np.pi / n_phases
    z = design.rotor().stator_z_m() - 0.3e-3
    lim = design.r_outer_m * 1.08
    elec = np.linspace(0.0, 2.0 * np.pi, n_panels, endpoint=False)

    # Field is linear in the phase currents: evaluate each phase alone (unit current)
    # once, then every panel is a cheap cos()-weighted superposition. This is ~3
    # evals total instead of one full-stator eval per panel.
    bz_phase = []
    xx = yy = None
    for k in range(n_phases):
        ik = np.zeros(n_phases)
        ik[k] = 1.0
        xx, yy, bzk = _bz_grid(coil_current_source(design, ik), lim, z,
                               n_grid, resolution_m)
        bz_phase.append(bzk)
    bz_phase = np.array(bz_phase)                      # (n_phases, ny, nx)

    grids, vmax = [], 1e-9
    for wt in elec:
        i_phase = i_amp * np.cos(wt - phase_off)
        bz = np.tensordot(i_phase, bz_phase, axes=1)   # superpose phase fields
        grids.append((xx, yy, bz, i_phase))
        vmax = max(vmax, float(np.max(np.abs(bz))))

    fig, axes = plt.subplots(1, n_panels, figsize=(3.1 * n_panels, 3.4),
                             constrained_layout=True)
    axes = np.atleast_1d(axes)
    names = [chr(ord("A") + k) for k in range(n_phases)]
    cf = None
    for ax, wt, (xx, yy, bz, i_phase) in zip(axes, elec, grids):
        cf = ax.contourf(xx * 1e3, yy * 1e3, bz, levels=np.linspace(-vmax, vmax, 24),
                         cmap="PuOr_r", vmin=-vmax, vmax=vmax)
        ax.set_aspect("equal")
        ax.set_xlabel("x [mm]")
        cur_str = " ".join(f"{n}{c:+.2f}" for n, c in zip(names, i_phase))
        ax.set_title(f"ωt={np.degrees(wt):.0f}°\n{cur_str}", fontsize=8)
    axes[0].set_ylabel("y [mm]")
    fig.colorbar(cf, ax=axes, label="B_z [T]", shrink=0.85)
    fig.suptitle(f"Stator field over one electrical period (FOC, {i_amp:g} A) "
                 "— rotates symmetrically", fontsize=10)
    return fig


def plot_kicad_traces(design: MotorDesign) -> "matplotlib.figure.Figure":
    """The exported KiCad spiral traces (full ring), coloured by phase A/B/C."""
    from .coil_spiral import export_spiral_polylines
    from .coils import _coil_layout
    n_slots = int(design.n_slots)
    layout = _coil_layout(n_slots, int(design.n_phases))
    phase_colors = {0: "tab:red", 1: "tab:green", 2: "tab:blue"}
    polys = export_spiral_polylines(design, single_coil=False)

    fig, ax = plt.subplots(figsize=(6.5, 6))
    for k, pl in enumerate(polys):
        ph = layout[k % n_slots][0]
        ax.plot(pl[:, 0] * 1e3, pl[:, 1] * 1e3,
                color=phase_colors.get(ph, "tab:gray"), lw=0.6)
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
    ax.legend(handles=[plt.Line2D([0], [0], color=c, label=f"phase {'ABC'[p]}")
                       for p, c in phase_colors.items()],
              loc="upper right", fontsize="small")
    ax.set_title(f"Exported KiCad traces: {len(polys)} continuous spiral coils")
    fig.tight_layout()
    return fig


def _magnet_extent_m(rotor: RotorConfig) -> float:
    """Outer radial extent [m] of the magnets (round rings or arc annulus)."""
    from .magnets import active_rings, is_round
    if is_round(rotor.magnet_topology):
        return max(ring_r + disc_d / 2 for ring_r, disc_d in active_rings(rotor))
    return rotor.magnet_r_outer_m


def _overlay_magnet_outlines(ax, rotor: RotorConfig) -> None:
    """Thin black outlines of the magnets over a field contour (mm axes)."""
    from matplotlib.patches import Circle, Wedge
    from .magnets import active_rings, is_round
    n_poles = 2 * rotor.pole_pairs
    pitch = 360.0 / n_poles
    if is_round(rotor.magnet_topology):
        rings = [(rr * 1e3, dd * 1e3 / 2) for rr, dd in active_rings(rotor)]
        for k in range(n_poles):
            ang = np.deg2rad((k + 0.5) * pitch)
            for rr, dr in rings:
                ax.add_patch(Circle((rr * np.cos(ang), rr * np.sin(ang)), dr,
                                    fill=False, edgecolor="k", lw=0.5))
    else:
        ri = rotor.magnet_r_inner_m * 1e3
        ro = rotor.magnet_r_outer_m * 1e3
        arc = pitch * rotor.pole_coverage
        for k in range(n_poles):
            c = k * pitch
            ax.add_patch(Wedge((0, 0), ro, c - arc / 2, c + arc / 2, width=ro - ri,
                               fill=False, edgecolor="k", lw=0.4))


def magnet_field_gif_bytes(design: MotorDesign, n_frames: int = 24,
                           n_grid: int = 48, resolution_m: float = 2.0e-3,
                           fps: int = 12) -> bytes:
    """Animated GIF (bytes) of magnet B_z as the rotor turns one pole-pair pitch.

    One electrical period (= ``2*pi/pole_pairs`` mechanical) brings the N/S
    pattern back onto itself, so the loop is seamless. Uses the vectorised field
    kernel at a modest grid -- a one-off artifact, not a sweep-time call.
    """
    from matplotlib.animation import FuncAnimation, PillowWriter
    from .magnets import magnet_segments
    rotor = design.rotor()
    z = rotor.stator_z_m()
    lim = _magnet_extent_m(rotor) * 1.08
    thetas = np.linspace(0.0, 2.0 * np.pi / rotor.pole_pairs, n_frames, endpoint=False)

    # Pre-compute every frame (and a global colour scale) up front.
    frames = []
    vmax = 1e-9
    for th in thetas:
        from .iron import with_iron_images
        src = with_iron_images(magnet_segments(rotor, theta_rad=float(th)), rotor)
        xx, yy, bz = _bz_grid(src, lim, z, n_grid, resolution_m)
        frames.append((xx, yy, bz))
        vmax = max(vmax, float(np.max(np.abs(bz))))

    fig, ax = plt.subplots(figsize=(5.5, 5.2))
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
    levels = np.linspace(-vmax, vmax, 24)

    def draw(i):
        ax.clear()
        xx, yy, bz = frames[i]
        ax.contourf(xx * 1e3, yy * 1e3, bz, levels=levels, cmap="RdBu_r",
                    vmin=-vmax, vmax=vmax)
        ax.set_aspect("equal")
        ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
        ax.set_title(f"Magnet B_z, rotor {np.degrees(thetas[i]):.0f}° mech")
        return []

    draw(0)
    fig.colorbar(plt.cm.ScalarMappable(
        norm=plt.Normalize(-vmax, vmax), cmap="RdBu_r"), ax=ax, label="B_z [T]")
    anim = FuncAnimation(fig, draw, frames=n_frames, blit=False)
    # PillowWriter writes to a path, not a buffer; round-trip via a temp file.
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".gif")
    os.close(fd)
    try:
        anim.save(path, writer=PillowWriter(fps=fps))
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        os.unlink(path)
        plt.close(fig)
    return data


# --------------------------------------------------------------------------- #
# Field-interaction shear stress: the tangential ("sideways") force per unit
# area that the coil current x magnet B_z produces -- i.e. what makes torque.
# --------------------------------------------------------------------------- #
def _shear_map(design: MotorDesign, geo, theta_rad: float, i_amp: float,
               delta: float, lim_m: float, n_grid: int, n_phi: int):
    """Tangential force per unit area [N/m^2] on an (x, y) grid at rotor ``theta``.

    Per coil segment, force ``dF = I_phase (dL x B_magnet)``; its tangential
    component ``dF.phi_hat`` is binned onto the grid and divided by the cell area
    (binning the force then dividing by area preserves the integral = torque).
    Returns ``(xx, yy, shear, torque_check)`` where ``torque_check = sum r*dF_phi``
    (one stator) should match the FOC torque at this angle.
    """
    from scipy.ndimage import gaussian_filter
    from .torque import foc_segment_force
    dF = foc_segment_force(design, geo, theta_rad, i_amp, delta, n_phi=n_phi)
    mid = geo.midpoints_m
    x, y = mid[:, 0], mid[:, 1]
    phi = np.arctan2(y, x)
    dF_phi = -dF[:, 0] * np.sin(phi) + dF[:, 1] * np.cos(phi)   # tangential force [N]

    edges = np.linspace(-lim_m, lim_m, n_grid + 1)
    H, _, _ = np.histogram2d(x, y, bins=[edges, edges], weights=dF_phi)
    cell_area = (2.0 * lim_m / n_grid) ** 2
    shear = gaussian_filter(H.T / cell_area, sigma=0.8)         # (ny, nx), N/m^2
    centers = 0.5 * (edges[:-1] + edges[1:])
    xx, yy = np.meshgrid(centers, centers)
    torque_check = float(np.sum(np.hypot(x, y) * dF_phi))
    return xx, yy, shear, torque_check


def plot_shear_stress(design: MotorDesign, i_amp: float = 1.0, n_grid: int = 60,
                      n_phi: int = 120) -> "matplotlib.figure.Figure":
    """Static map of the torque-producing tangential shear (force per unit area).

    The coil current interacting with the rotor's axial field B_z gives a
    "sideways" force per area ``-J_r*B_z``; its area-integral (x radius) is the
    shaft torque. Driven at FOC currents (``i_amp``) at the peak-torque instant.
    """
    from .torque import optimal_foc_delta
    from .coils import build_coil
    geo = build_coil(design)
    delta = optimal_foc_delta(design, geo, n_phi=n_phi)
    rotor = design.rotor()
    lim = _magnet_extent_m(rotor) * 1.08
    xx, yy, shear, tcheck = _shear_map(design, geo, 0.0, i_amp, delta, lim, n_grid, n_phi)

    fig, ax = plt.subplots(figsize=(6.5, 6))
    vmax = np.max(np.abs(shear)) or 1.0
    cf = ax.contourf(xx * 1e3, yy * 1e3, shear, levels=24, cmap="RdBu_r",
                     vmin=-vmax, vmax=vmax)
    fig.colorbar(cf, ax=ax, label="tangential shear [N/m$^2$]")
    _overlay_magnet_outlines(ax, rotor)
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
    ax.set_title(f"Field interaction: torque-producing shear @ {i_amp:g} A\n"
                 f"(coil current x magnet B_z; integral = {tcheck*1e3*design.n_stators:.2f} mNm)")
    fig.tight_layout()
    return fig


def shear_interaction_gif_bytes(design: MotorDesign, i_amp: float = 1.0,
                                n_frames: int = 24, n_grid: int = 48,
                                n_phi: int = 96, fps: int = 12) -> bytes:
    """Animated GIF of the rotating tangential-shear pattern under FOC.

    As the rotor turns one electrical period the FOC currents rotate with it, so
    the shear pattern sweeps around while its integral (torque) stays ~constant --
    showing how the field interaction makes steady torque.
    """
    from matplotlib.animation import FuncAnimation, PillowWriter
    from .torque import optimal_foc_delta
    from .coils import build_coil
    geo = build_coil(design)
    delta = optimal_foc_delta(design, geo, n_phi=n_phi)
    rotor = design.rotor()
    p = max(1, rotor.pole_pairs)
    lim = _magnet_extent_m(rotor) * 1.08
    thetas = np.linspace(0.0, 2.0 * np.pi / p, n_frames, endpoint=False)

    frames, vmax = [], 1e-9
    for th in thetas:
        xx, yy, shear, _ = _shear_map(design, geo, float(th), i_amp, delta,
                                      lim, n_grid, n_phi)
        frames.append((xx, yy, shear))
        vmax = max(vmax, float(np.max(np.abs(shear))))
    levels = np.linspace(-vmax, vmax, 24)

    fig, ax = plt.subplots(figsize=(5.6, 5.3))

    def draw(i):
        ax.clear()
        xx, yy, shear = frames[i]
        ax.contourf(xx * 1e3, yy * 1e3, shear, levels=levels, cmap="RdBu_r",
                    vmin=-vmax, vmax=vmax)
        _overlay_magnet_outlines(ax, rotor)
        ax.set_aspect("equal")
        ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
        ax.set_title(f"Torque-producing shear, rotor {np.degrees(thetas[i]):.0f}° mech")
        return []

    draw(0)
    fig.colorbar(plt.cm.ScalarMappable(
        norm=plt.Normalize(-vmax, vmax), cmap="RdBu_r"), ax=ax,
        label="tangential shear [N/m$^2$]")
    anim = FuncAnimation(fig, draw, frames=n_frames, blit=False)
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".gif")
    os.close(fd)
    try:
        anim.save(path, writer=PillowWriter(fps=fps))
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        os.unlink(path)
        plt.close(fig)
    return data
