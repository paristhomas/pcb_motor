"""Core single-design-point evaluation: design -> all result quantities.

Pure function, no bencher dependency, so both the CLI ``point`` command and the
bencher sweep worker share it. Ties together the simulated Kt (Biot-Savart),
the thermally-limited continuous current, and the rotor inertia into the
objective: continuous acceleration ``a_cont = tau_cont / J_total``.

Stator series convention: ``build_coil`` models one stator's winding; for a
dual-stator machine the stators are wired in series, so both Kt and phase
resistance scale by ``n_stators`` (kt already scaled inside torque; resistance
scaled here). Same current, both stators contribute torque.
"""

from __future__ import annotations

import math

from . import constants as C

from .design import MotorDesign
from .coils import build_coil, phase_resistance
from .torque import kt_and_torque
from .thermal import continuous_current
from .inertia import rotor_inertia, total_inertia


def evaluate_design(design: MotorDesign) -> dict:
    """Evaluate one design point. Returns the full result-variable dict."""
    geo = build_coil(design)

    # Resistance: single-stator winding scaled to the series machine.
    r20 = phase_resistance(design, geo, temp_c=None) * design.n_stators

    tq = kt_and_torque(design, geo)
    kt = tq["kt_nm_per_a"]                      # already scaled by n_stators

    therm = continuous_current(design, r20)
    i_cont = therm["i_cont_a"]
    r_hot = therm["r_phase_hot"]

    tau_cont = kt * i_cont
    rotor = design.rotor()
    j_rotor = rotor_inertia(rotor)
    j_total = total_inertia(rotor, design.load_inertia_kgm2)
    a_cont = tau_cont / j_total if j_total > 0 else 0.0

    # Copper mass: all phases, all stators. Tapered windings carry the exact
    # integrated volume (cross-section varies with radius); conductor_area_m2
    # is then the minimum section, which would undercount the mass.
    total_len = geo.length_per_phase_m * design.n_phases * design.n_stators
    if geo.copper_volume_m3 is not None:
        cu_mass = geo.copper_volume_m3 * design.n_stators * C.RHO_CU_DENSITY
    else:
        cu_mass = total_len * geo.conductor_area_m2 * C.RHO_CU_DENSITY

    area_mm2 = geo.conductor_area_m2 * 1e6
    cur_density = i_cont / area_mm2 if area_mm2 > 0 else 0.0
    v_cont = i_cont * r_hot
    torque_density = tau_cont / cu_mass if cu_mass > 0 else 0.0

    # Airgap tangential shear at continuous torque (coreless sanity: 0.5-10 kPa).
    r_mean = 0.5 * (design.r_inner_m + design.r_outer_m)
    annulus = math.pi * (design.r_outer_m**2 - design.r_inner_m**2)
    shear = tau_cont / (r_mean * annulus) if r_mean > 0 and annulus > 0 else 0.0

    # Drive-side parasitics: air-core phase L, PWM ripple on the reference
    # bus/switching frequency, and eddy loss at the reference speed.
    from .parasitics import eddy_loss, phase_inductance, pwm_ripple

    l_phase = phase_inductance(design, geo)
    ripple = pwm_ripple(design, l_phase, v_cont, i_cont)
    p_eddy = eddy_loss(design, geo, tq["b_gap_mean_t"])

    # Per-plate magnetic pull when stator back iron is fitted (bearing spec).
    from .iron import plate_axial_force

    f_plate = plate_axial_force(design) if design.back_iron else 0.0

    return {
        "accel_cont_rad_s2": a_cont,            # OBJECTIVE
        "tau_cont_mNm": tau_cont * 1e3,
        "kt_mNm_per_A": kt * 1e3,
        "i_cont_A": i_cont,
        "j_rotor_kgm2": j_rotor,
        "j_total_kgm2": j_total,
        "r_phase_20c_ohm": r20,
        "r_phase_hot_ohm": r_hot,
        "b_gap_mean_T": tq["b_gap_mean_t"],
        "b_gap_peak_T": tq["b_gap_peak_t"],
        "copper_loss_W": therm["p_dissipation_w"],
        "n_turns": geo.n_turns,
        "conductor_length_m": total_len,
        "conductor_area_mm2": area_mm2,
        "copper_mass_g": cu_mass * 1e3,
        "current_density_A_mm2": cur_density,
        "v_drive_cont_V": v_cont,
        "shear_stress_kPa": shear / 1e3,
        "torque_density_Nm_kg": torque_density,
        "end_turn_fraction": geo.end_turn_fraction,
        "l_phase_uH": l_phase * 1e6,
        "pwm_ripple_A_pp": ripple["pwm_ripple_a_pp"],
        "l_ext_uH": ripple["l_ext_h"] * 1e6,
        "eddy_loss_W_ref": p_eddy,
        "plate_pull_N": f_plate,
        "torque_ripple": tq["torque_ripple"],
        "winding_factor": tq.get("winding_factor", float("nan")),
        "winding_utilisation": tq.get("winding_utilisation", float("nan")),
        "warnings": therm.get("warnings", []),
    }
