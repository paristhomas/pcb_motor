"""Drive-side parasitics: phase inductance, PWM current ripple, eddy loss.

A coreless PCB winding has *air-core* inductance -- a few uH -- which is far
below what a typical FOC driver's current loop assumes. These estimates exist
so every design reports (a) its phase inductance, (b) the PWM ripple current it
would see on a given bus/switching frequency, (c) the external series
inductance needed to tame that ripple, and (d) the eddy-current loss in the
(wide, tapered) traces at a reference speed.

Inductance method: Neumann double sum over the discretised conductor of one
phase, with a regularised kernel ``1/sqrt(r^2 + d_i*d_j)`` where ``d`` is the
rectangular-section GMD ``0.2235*(w + t)``. Including the ``i == j`` diagonal
with this kernel approximates the finite-section self term, avoiding the
short-segment breakdown of textbook partial-inductance formulas. Air-core only
(no iron anywhere in this machine), accuracy ~+/-20%: right for "do I need
external inductors", not for filter design.

Eddy method: thin-strip lamination formula ``P/V = pi^2 f^2 B^2 w^2 / (6 rho)``
per segment, with ``w`` the local (tapered) trace width and ``B`` the mean
airgap amplitude -- an order-of-magnitude screen that flags high-speed reuse,
deliberately conservative-simple.
"""

from __future__ import annotations

import numpy as np

from .constants import RHO_CU_20
from .design import CoilGeometry, MotorDesign

MU0 = 4e-7 * np.pi


def _segment_widths(design: MotorDesign, geo: CoilGeometry, mask) -> np.ndarray:
    from .coil_spiral import trace_width_at

    mid = geo.midpoints_m[mask]
    r = np.hypot(mid[:, 0], mid[:, 1])
    return np.asarray(trace_width_at(design, r))


def phase_inductance(design: MotorDesign, geo: CoilGeometry, phase: int = 0) -> float:
    """Air-core inductance [H] of one phase, all stators in series.

    Mutual coupling *between* the two stators is ignored (they are ~an air gap
    plus a rotor apart; the omission slightly underestimates L), so the series
    total is ``n_stators * L_one_stator``. Parallel paths divide by ``p**2``,
    mirroring the resistance convention.
    """
    from .coils import _copper_thickness

    mask = geo.phase == phase
    if not np.any(mask):
        return 0.0
    dl = geo.dvec_m[mask]                     # sense-carrying dL vectors
    mid = geo.midpoints_m[mask]
    t_cu = _copper_thickness(float(design.copper_weight_oz))
    w = _segment_widths(design, geo, mask)
    d = 0.2235 * (w + t_cu)                   # rectangular-section GMD

    # With stator back iron, the winding's flux is reinforced by its images in
    # the two plates: extend the j-sum over the imaged conductor as well. The
    # images are far (>= 2*(board + gap + standoff)) so no regularisation is
    # needed, but the shared kernel is reused for simplicity. Each reflection
    # mirrors midpoints across a plane and flips the z-component of dL (image
    # currents keep their in-plane direction for a mu->infinity boundary).
    mid_j, dl_j, d_j = mid, dl, d
    if design.back_iron:
        from .iron import iron_images, iron_plane_z
        from .design import CurrentSource

        order = 3
        z_p = iron_plane_z(design.rotor())
        src = CurrentSource(mid, np.ones(mid.shape[0]))
        img_mids, img_dls, img_ds = [mid], [dl], [d]
        for k, im in enumerate(iron_images(src, z_p, -z_p, order=order)):
            img_mids.append(im.vertices)
            dlk = dl.copy()
            if ((k % order) + 1) % 2 == 1:    # odd number of reflections
                dlk[:, 2] *= -1.0
            img_dls.append(dlk)
            img_ds.append(d)
        mid_j = np.vstack(img_mids)
        dl_j = np.vstack(img_dls)
        d_j = np.concatenate(img_ds)

    n = dl.shape[0]
    L = 0.0
    chunk = 800                                # bound the n^2 memory footprint
    for i0 in range(0, n, chunk):
        i1 = min(i0 + chunk, n)
        diff = mid[i0:i1, None, :] - mid_j[None, :, :]
        r2 = np.einsum("ijk,ijk->ij", diff, diff)
        dots = dl[i0:i1] @ dl_j.T
        L += float((dots / np.sqrt(r2 + np.outer(d[i0:i1], d_j))).sum())
    L *= MU0 / (4.0 * np.pi)

    paths = int(design.parallel_paths)
    return L * int(design.n_stators) / (paths ** 2)


def pwm_ripple(design: MotorDesign, l_phase_h: float, v_drive: float,
               i_cont: float) -> dict:
    """Peak-to-peak PWM ripple current and the series L needed to tame it.

    Uses the **worst-case** (D = 0.5) ripple ``ripple_pp = v_bus / (4 L f_pwm)``,
    the form every drive vendor publishes (maxon, Celera). In a 3-phase FOC
    inverter at low speed the modulation index is small, so each half-bridge leg
    dwells near 50 % duty and the phase inductor sees ~worst-case ripple
    essentially all the time -- the operating-duty value badly understates it
    here. ``v_drive`` is retained only for reference (the actual operating duty).
    ``l_ext_h`` is the *extra* series inductance per phase to bring ripple
    <= ``design.drive_ripple_frac`` of the continuous current (0 if the native
    winding already meets it). Accuracy ~+/- tens of %: the scheme-dependent
    constant (3-phase SVPWM partially cancels) is between ~4 and ~6; the 4 here
    is the conservative (largest-ripple) choice. Right for "do I need a choke",
    not for filter design.
    """
    v_bus = float(design.drive_v_bus)
    f = float(design.drive_f_pwm_hz)
    frac = float(design.drive_ripple_frac)
    if v_bus <= 0 or f <= 0 or l_phase_h <= 0:
        return {"pwm_ripple_a_pp": float("nan"), "l_ext_h": float("nan")}
    k = v_bus * 0.25 / f                       # worst-case (D=0.5) volt-seconds
    l_req = k / (frac * i_cont) if i_cont > 0 else float("inf")
    return {
        "pwm_ripple_a_pp": k / l_phase_h,
        "l_ext_h": max(0.0, l_req - l_phase_h),
    }


def eddy_loss(design: MotorDesign, geo: CoilGeometry, b_amp_t: float) -> float:
    """Eddy loss [W] in all traces (all phases, all stators) at the reference
    speed ``design.ref_speed_rev_s`` (electrical f = pole_pairs * rev/s)."""
    from .coils import _copper_thickness

    f_e = float(design.pole_pairs) * float(design.ref_speed_rev_s)
    if f_e <= 0 or b_amp_t <= 0:
        return 0.0
    t_cu = _copper_thickness(float(design.copper_weight_oz))
    mask = np.ones(geo.phase.shape[0], dtype=bool)
    w = _segment_widths(design, geo, mask)
    seg_len = np.linalg.norm(geo.dvec_m, axis=1)
    # P = sum over segments of (pi^2 f^2 B^2 w^2 / 6 rho) * (w * t * len)
    p_density = (np.pi ** 2) * f_e ** 2 * b_amp_t ** 2 / (6.0 * RHO_CU_20)
    p = p_density * float((w ** 3 * t_cu * seg_len).sum())
    return p * int(design.n_stators)
