"""Thermal continuous-current limit for the PCB stator stack.

Standalone continuous-current physics. For an FR4 coreless stator the
continuous torque is almost always set by I^2 R self-heating, not by magnetics:
a lumped steady-state convection balance equates copper loss to
``h * A_surface * dT``, with resistance taken at the hot operating temperature
(copper resistivity rises ~0.39%/K).
"""

from __future__ import annotations

import math

from .constants import ALPHA_CU, FR4_TG
from .design import MotorDesign


def continuous_current(design: MotorDesign, r_phase_20c: float) -> dict:
    """Thermally-limited continuous phase current and the lumped thermal state.

    Continuous-thermal model:

        dT        = max(temp_limit_c - ambient_c, 0)
        annulus   = pi * (r_outer_m^2 - r_inner_m^2)
        a_surface = max(cooled_faces, 1) * annulus
        p_max     = h_conv * a_surface * dT
        r_hot     = r_phase_20c * (1 + ALPHA_CU * (temp_limit_c - 20))
        i_cont    = sqrt(p_max / (loss_phase_factor * r_hot))   if denom & p_max > 0
                    else 0

    The cooled annulus uses the stator coil radii (``r_outer_m`` / ``r_inner_m``)
    -- the active copper annulus that sheds heat.

    Parameters
    ----------
    design : MotorDesign
        Provides r_outer_m, r_inner_m, cooled_faces, h_conv, temp_limit_c,
        ambient_c, loss_phase_factor.
    r_phase_20c : float
        Phase resistance at 20 C [ohm] (from the coil/resistance model).

    Returns
    -------
    dict with keys:
        i_cont_a          continuous phase current [A]
        r_phase_hot       phase resistance at temp_limit_c [ohm]
        p_dissipation_w   max steady copper loss removable [W]
        a_surface_m2      convective surface area [m^2]
        dT                temperature rise above ambient [K]
        warnings          list[str] (FR4-Tg derate warning, optional)
    """
    warnings: list[str] = []

    # FR4 softens at/above its glass-transition temperature.
    if design.temp_limit_c >= FR4_TG:
        warnings.append(
            f"Continuous temp limit {design.temp_limit_c:.0f} C is at/above FR4 "
            f"Tg ({FR4_TG:.0f} C): board will soften/delaminate. Derate."
        )

    dT = max(design.temp_limit_c - design.ambient_c, 0.0)

    # Convective area: each outer face thermally coupled to a stator sheds heat
    # at one annulus of active copper. At least one annulus is always assumed
    # (a buried stator still couples across its gap, optimistically).
    annulus = math.pi * (design.r_outer_m**2 - design.r_inner_m**2)
    a_surface = max(design.cooled_faces, 1) * annulus
    p_max = design.h_conv * a_surface * dT

    # Resistance at the hot operating point.
    r_hot = r_phase_20c * (1.0 + ALPHA_CU * (design.temp_limit_c - 20.0))

    # p_max = loss_phase_factor * I^2 * r_hot  ->  I_cont
    denom = design.loss_phase_factor * r_hot
    i_cont = math.sqrt(p_max / denom) if denom > 0 and p_max > 0 else 0.0

    return {
        "i_cont_a": i_cont,
        "r_phase_hot": r_hot,
        "p_dissipation_w": p_max,
        "a_surface_m2": a_surface,
        "dT": dT,
        "warnings": warnings,
    }
