"""Torque and torque-constant (Kt) from the Biot-Savart field.

For a rotor angle ``theta`` the magnet field ``B`` at each coil-segment midpoint
is needed once (it does not depend on the phase currents). Evaluating the field
directly at every coil segment is wasteful -- the coil mesh has tens of thousands
of segments (fine, for plotting), while the field is smooth over the annulus. So
we evaluate B on a **coarse polar grid** at each stator layer plane (one cheap
Biot-Savart call per layer per rotor angle) and interpolate it onto the coil
midpoints. This makes torque cost independent of the coil mesh density.

The Lorentz force on a segment carrying current ``I`` is ``dF = I (dL x B)`` and
its axial torque is ``(r x dF)_z``. Torque is linear in the phase currents, so we
decompose per phase: ``g_s = (r_s x (dL_s x B_s))_z`` per unit segment current,
and ``G[k] = sum over phase-k segments of direction_s * g_s``, giving
``torque = I_phase . G``. Kt is the commutation offset (torque angle) that
maximises the mean torque over one electrical period -- a cheap scan over
``G(theta)`` with no extra field evaluations.

Which field component matters? This is an **axial-flux** machine: the stator
current is radial (``J = J_r r_hat``) and the magnet field has components
``(B_r, B_phi, B_z)``. Then ``J x B = J_r B_phi z_hat - J_r B_z phi_hat`` and the
axial torque is ``tau_z = -r J_r B_z``. So the shaft torque is produced by the
radial current crossing the **axial** field ``B_z``; the circumferential field
``B_phi`` makes only an axial force (no torque). We integrate the *full* 3-D
``dL x B`` here (so B_r/B_phi are included -- they simply contribute zero axial
torque for the planar radial conductors), which is why the B_z map is the right
field to visualise and ``-J_r B_z`` is the torque-producing tangential shear.
See ``tests/test_torque.py::test_torque_comes_from_axial_field``.

``build_coil`` models a single stator; ``kt_nm_per_a`` scales by ``n_stators``.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from .design import MotorDesign, CoilGeometry
from .coils import build_coil
from .magnets import magnet_segments
from .field import b_field_at_points


def _field_interp(design: MotorDesign, theta_rad: float, z_m: float,
                  n_r: int, n_phi: int):
    """Build a periodic (r, phi) interpolator for B at plane z, rotor at theta."""
    rotor = design.rotor()
    r = np.linspace(design.r_inner_m, design.r_outer_m, n_r)
    phi = np.linspace(0.0, 2.0 * np.pi, n_phi, endpoint=False)
    R, PHI = np.meshgrid(r, phi, indexing="ij")            # (n_r, n_phi)
    pts = np.column_stack([
        (R * np.cos(PHI)).ravel(),
        (R * np.sin(PHI)).ravel(),
        np.full(R.size, z_m),
    ])
    from .iron import with_iron_images

    src = with_iron_images(magnet_segments(rotor, theta_rad=theta_rad), rotor)
    B = b_field_at_points(src, pts, design.coil_resolution_m).reshape(n_r, n_phi, 3)
    # Make phi periodic by appending the phi=0 slice at phi=2pi.
    phi_ext = np.concatenate([phi, [2.0 * np.pi]])
    B_ext = np.concatenate([B, B[:, :1, :]], axis=1)
    return RegularGridInterpolator(
        (r, phi_ext), B_ext, bounds_error=False, fill_value=None
    )


def _b_on_coil(design: MotorDesign, geo: CoilGeometry, theta_rad: float,
               n_r: int, n_phi: int) -> np.ndarray:
    """B (tesla) at every coil midpoint, via per-layer grid interpolation."""
    mids = geo.midpoints_m
    zs = mids[:, 2]
    r = np.hypot(mids[:, 0], mids[:, 1])
    r = np.clip(r, design.r_inner_m, design.r_outer_m)
    phi = np.mod(np.arctan2(mids[:, 1], mids[:, 0]), 2.0 * np.pi)
    query = np.column_stack([r, phi])

    B = np.empty((mids.shape[0], 3))
    for z in np.unique(np.round(zs, 9)):
        mask = np.isclose(zs, z)
        interp = _field_interp(design, theta_rad, float(z), n_r, n_phi)
        B[mask] = interp(query[mask])
    return B


def _phase_torque_vectors(geo: CoilGeometry, B: np.ndarray, n_phase: int) -> np.ndarray:
    """G[k] = sum over phase-k segments of (r x (dL x B))_z per unit phase current.

    ``geo.dvec_m`` already carries the winding sense (current direction), so the
    per-segment torque ``g_s`` is the contribution per unit *phase* current; we do
    NOT re-apply ``geo.direction``. Summing over a phase's conductors gives that
    phase's torque per amp -- including all cancellation from how conductors sit
    relative to the poles, which is exactly where the winding factor lives.
    """
    dF = np.cross(geo.dvec_m, B)                 # per unit phase current
    r = geo.midpoints_m.copy()
    r[:, 2] = 0.0
    g = np.cross(r, dF)[:, 2]
    G = np.zeros(n_phase)
    for k in range(n_phase):
        G[k] = g[geo.phase == k].sum()
    return G


def kt_and_torque(
    design: MotorDesign,
    geo: CoilGeometry | None = None,
    i_amp: float = 1.0,
    n_r: int = 10,
    delta_steps: int = 72,
) -> dict:
    """Compute Kt and diagnostics with proper 3-phase commutation.

    Phase currents ``I*cos(p*theta - k*2pi/n + delta)`` are synchronised to the
    rotor; the commutation offset ``delta`` is chosen to maximise mean torque.
    With the correct star-of-slots phase layout this yields the physical Kt with
    the winding factor baked in (no hand-applied kw). An ideal abs-sum upper bound
    is also computed so we can report a winding-utilisation diagnostic.

    Returns: kt_nm_per_a (machine, x n_stators), kt_per_stator, torque_nm,
    b_gap_mean_t, b_gap_peak_t, torque_ripple, winding_factor (analytic, from the
    coil layout), winding_utilisation (commutated/ideal).
    """
    if geo is None:
        geo = build_coil(design)
    p = max(1, design.pole_pairs)
    n_phase = int(geo.phase.max()) + 1
    steps = max(2, design.commutation_steps)
    n_phi = max(48, 2 * p * 6)

    elec = np.linspace(0.0, 2.0 * np.pi, steps, endpoint=False)
    Gs = np.zeros((steps, n_phase))
    ideal = np.zeros(steps)
    bmean = np.zeros(steps)
    bpeak = np.zeros(steps)
    for i, e in enumerate(elec):
        theta = e / p
        B = _b_on_coil(design, geo, theta, n_r, n_phi)
        Gs[i] = _phase_torque_vectors(geo, B, n_phase)
        # Ideal upper bound: every radial conductor optimally phased (abs-sum).
        dF = np.cross(geo.dvec_m, B)
        rr = geo.midpoints_m.copy(); rr[:, 2] = 0.0
        gabs = np.abs(np.cross(rr, dF)[:, 2])
        ideal[i] = gabs[geo.is_radial].sum()
        bz = np.abs(B[:, 2])
        bmean[i] = bz.mean(); bpeak[i] = bz.max()

    # Optimise the commutation offset (cheap; no extra field evals).
    deltas = np.linspace(0.0, 2.0 * np.pi, delta_steps, endpoint=False)
    phase_off = np.arange(n_phase) * 2.0 * np.pi / n_phase
    best = {"abs_mean": -np.inf, "mean": 0.0, "tau": np.zeros(steps)}
    for d in deltas:
        Iph = np.cos(elec[:, None] - phase_off[None, :] + d)   # peak amplitude 1
        tau = np.sum(Iph * Gs, axis=1)
        m = tau.mean()
        if abs(m) > best["abs_mean"]:
            best = {"abs_mean": abs(m), "mean": m, "tau": tau}

    tau = best["tau"]
    # Currents above use unit peak amplitude, so mean torque IS torque-per-amp (Kt).
    kt_per_stator = abs(best["mean"])
    kt = kt_per_stator * design.n_stators
    mean_tau = abs(best["mean"])
    ripple = float((np.abs(tau).max() - np.abs(tau).min()) / mean_tau) if mean_tau else 0.0
    ideal_mean = float(ideal.mean())
    utilisation = mean_tau / ideal_mean if ideal_mean else 0.0

    from .coils import winding_factor
    kw = winding_factor(design)

    return {
        "kt_nm_per_a": kt,
        "kt_per_stator": kt_per_stator,
        "torque_nm": kt * i_amp,
        "b_gap_mean_t": float(bmean.mean()),
        "b_gap_peak_t": float(bpeak.max()),
        "torque_ripple": ripple,
        "winding_factor": kw,
        "winding_utilisation": utilisation,
    }


def torque_vs_angle(
    design: MotorDesign,
    geo: CoilGeometry | None = None,
    i_amp: float = 1.0,
    n_steps: int = 120,
    n_r: int = 10,
    n_phi: int | None = None,
) -> dict:
    """Torque waveform as the rotor turns through one electrical period.

    Two excitations are returned, both at a continuous (DC) phase-current
    amplitude ``i_amp``:

    - ``tau_commutated`` -- the real operating torque: balanced 3-phase currents
      ``i_amp*cos(p*theta - k*2pi/n + delta)`` kept synchronised to the rotor
      (``delta`` chosen to maximise mean torque, as in :func:`kt_and_torque`).
      Its ``ripple`` = ``(max-min)/mean`` is the operating torque ripple -- small
      for this coreless machine because there are no slots/iron to cog against.

    - ``tau_dc`` -- the torque-angle *characteristic*: the phase currents are
      frozen at the commutation vector for ``theta=0`` and the rotor is then swept.
      This traces the (near-sinusoidal) torque function the commutation rides on,
      so its harmonic content is visible even when the commutated ripple is ~0.

    Angles are returned in electrical degrees over a full ``2*pi`` electrical period
    (= ``2*pi/p`` mechanical). Torque values are in N*m for the whole machine
    (``x n_stators``).
    """
    if geo is None:
        geo = build_coil(design)
    p = max(1, design.pole_pairs)
    n_phase = int(geo.phase.max()) + 1
    if n_phi is None:
        n_phi = max(48, 2 * p * 6)

    elec = np.linspace(0.0, 2.0 * np.pi, n_steps, endpoint=False)
    Gs = np.zeros((n_steps, n_phase))
    for i, e in enumerate(elec):
        theta = e / p
        B = _b_on_coil(design, geo, theta, n_r, n_phi)
        Gs[i] = _phase_torque_vectors(geo, B, n_phase)

    phase_off = np.arange(n_phase) * 2.0 * np.pi / n_phase

    # Optimal commutation offset over the period (same criterion as kt_and_torque).
    deltas = np.linspace(0.0, 2.0 * np.pi, 72, endpoint=False)
    best = {"abs_mean": -np.inf, "delta": 0.0, "mean": 0.0}
    for d in deltas:
        Iph = np.cos(elec[:, None] - phase_off[None, :] + d)
        m = np.sum(Iph * Gs, axis=1).mean()
        if abs(m) > best["abs_mean"]:
            best = {"abs_mean": abs(m), "delta": d, "mean": m}
    # Orient to positive motoring torque (sign is just the current/rotation
    # convention; +pi on delta flips current polarity).
    delta = best["delta"] + (np.pi if best["mean"] < 0 else 0.0)

    scale = i_amp * design.n_stators
    Iph_comm = np.cos(elec[:, None] - phase_off[None, :] + delta)
    tau_comm = np.sum(Iph_comm * Gs, axis=1) * scale

    # Frozen (DC) excitation: hold the theta=0 commutation vector, sweep the rotor.
    I_dc = np.cos(-phase_off + delta)
    tau_dc = (Gs @ I_dc) * scale

    mean_comm = float(np.abs(tau_comm).mean())
    ripple = float((tau_comm.max() - tau_comm.min()) / mean_comm) if mean_comm else 0.0

    return {
        "elec_deg": np.degrees(elec),
        "tau_commutated_nm": tau_comm,
        "tau_dc_nm": tau_dc,
        "mean_torque_nm": float(tau_comm.mean()),
        "ripple": ripple,
        "ripple_pct": 100.0 * ripple,
        "commutation_delta_rad": float(delta),
        "i_amp": i_amp,
    }


def optimal_foc_delta(design: MotorDesign, geo: CoilGeometry | None = None,
                      n_r: int = 10, n_phi: int | None = None,
                      steps: int = 24) -> float:
    """The FOC commutation offset (q-axis) that maximises mean motoring torque.

    A coarse scan over one electrical period -- enough to fix ``delta`` for
    rendering force/shear maps; oriented to positive torque.
    """
    if geo is None:
        geo = build_coil(design)
    p = max(1, design.pole_pairs)
    n_phase = int(geo.phase.max()) + 1
    if n_phi is None:
        n_phi = max(48, 2 * p * 6)
    elec = np.linspace(0.0, 2.0 * np.pi, steps, endpoint=False)
    Gs = np.array([_phase_torque_vectors(geo, _b_on_coil(design, geo, e / p, n_r, n_phi),
                                         n_phase) for e in elec])
    phase_off = np.arange(n_phase) * 2.0 * np.pi / n_phase
    deltas = np.linspace(0.0, 2.0 * np.pi, 72, endpoint=False)
    best = {"abs_mean": -np.inf, "delta": 0.0, "mean": 0.0}
    for d in deltas:
        m = np.sum(np.cos(elec[:, None] - phase_off[None, :] + d) * Gs, axis=1).mean()
        if abs(m) > best["abs_mean"]:
            best = {"abs_mean": abs(m), "delta": d, "mean": m}
    return float(best["delta"] + (np.pi if best["mean"] < 0 else 0.0))


def foc_segment_force(design: MotorDesign, geo: CoilGeometry, theta_rad: float,
                      i_amp: float, delta: float, n_r: int = 10,
                      n_phi: int | None = None) -> np.ndarray:
    """Per-coil-segment Lorentz force [N] at rotor angle ``theta`` under FOC.

    FOC phase currents ``I_k = i_amp*cos(p*theta - k*2pi/n + delta)`` are applied
    to the magnet field at the coil segments: ``dF_s = I_phase[k] * (dL_s x B_s)``
    (``geo.dvec_m`` already carries the winding sense). Returns an ``(S, 3)`` array
    of segment forces. The axial torque is ``sum (r_s x dF_s)_z``; the tangential
    component ``dF . phi_hat`` is the torque-producing shear (per machine, NOT
    x n_stators -- callers scale if needed).
    """
    p = max(1, design.pole_pairs)
    n_phase = int(geo.phase.max()) + 1
    if n_phi is None:
        n_phi = max(48, 2 * p * 6)
    phase_off = np.arange(n_phase) * 2.0 * np.pi / n_phase
    Iph = i_amp * np.cos(p * theta_rad - phase_off + delta)      # (n_phase,)
    B = _b_on_coil(design, geo, theta_rad, n_r, n_phi)
    return np.cross(geo.dvec_m, B) * Iph[geo.phase][:, None]
