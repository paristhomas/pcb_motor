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

# Neck ("hot spot") current-density nudge threshold [A/mm^2]. Reported density
# is at the winding's narrowest cross-section, so exceeding this means a local
# hot spot even when the lumped thermal balance is satisfied.
NECK_DENSITY_LIMIT_A_MM2 = 80.0


def evaluate_design(design: MotorDesign) -> dict:
    """Evaluate one design point. Returns the full result-variable dict."""
    # Fail fast on invalid topology combos (magnet_segments enforces the same
    # rules; checking here avoids paying for the coil build first).
    from .magnets import validate_rotor_sides

    validate_rotor_sides(design.rotor())

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

    warnings = list(therm.get("warnings", []))

    # PWM-ripple gate (design guide Stage 5): a coreless winding's air-core L
    # is tiny, so worst-case ripple can dwarf the continuous current. Flag it
    # loudly instead of returning a silently un-drivable design.
    ripple_pp = ripple["pwm_ripple_a_pp"]
    ripple_budget = design.drive_ripple_frac * i_cont
    if math.isfinite(ripple_pp) and ripple_budget > 0 and ripple_pp > ripple_budget:
        warnings.append(
            f"PWM ripple {ripple_pp:.2f} A pp exceeds the {ripple_budget:.2f} A "
            f"budget ({ripple_pp / ripple_budget:.0f}x) at "
            f"{design.drive_v_bus:g} V bus / {design.drive_f_pwm_hz / 1e3:g} kHz "
            f"/ {design.drive_ripple_frac:.0%} of I_cont: not drivable without "
            f"~{ripple['l_ext_h'] * 1e6:.0f} uH/phase external inductance -- "
            "see design guide Stage 5."
        )

    # Hot-neck nudge: current density is reported at the narrowest section
    # (the neck at r_inner for tapered traces); IPC/JLC guidance puts sustained
    # PCB current density around 20-35 A/mm^2, so ~80 is well into local-hot-
    # spot territory even when the lumped thermal balance says OK.
    if cur_density > NECK_DENSITY_LIMIT_A_MM2:
        warnings.append(
            f"current density {cur_density:.0f} A/mm^2 at the winding's "
            f"narrowest section exceeds ~{NECK_DENSITY_LIMIT_A_MM2:.0f} A/mm^2: "
            "expect a hot neck at r_inner -- widen trace_width_m, add copper "
            "weight, or move r_inner_m outward."
        )

    if design.rotor_sides == 2:
        warnings.append(
            "dual-rotor sandwich: the rotor-rotor axial attraction is NOT "
            "modelled (plate_pull_N covers stator back iron only). The two "
            "magnet plates pull toward each other through the board -- size "
            "the rotor spacer/hub and assembly jig for that force."
        )

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
        "rotor_sides": design.rotor_sides,
        "torque_ripple": tq["torque_ripple"],
        "winding_factor": tq.get("winding_factor", float("nan")),
        "winding_utilisation": tq.get("winding_utilisation", float("nan")),
        "warnings": warnings,
    }
