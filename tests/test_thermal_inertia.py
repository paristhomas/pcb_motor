"""Lock the pcb_motor thermal / inertia physics with regression + property tests.

Two standalone physics functions are exercised here:
  - ``pcb_motor.thermal.continuous_current``
  - ``pcb_motor.inertia.rotor_inertia``

The golden values below were captured from the engine on the default design
point (originally cross-validated against the analytical model these were ported
from). They pin the numbers so a physics regression is caught.
"""

from __future__ import annotations

import math

import pytest

from pcb_motor.design import MotorDesign, RotorConfig
from pcb_motor.thermal import continuous_current
from pcb_motor.inertia import rotor_inertia, total_inertia


def test_continuous_current_regression():
    """Continuous-current model on the default design (fixed r_phase) is stable."""
    design = MotorDesign()  # default hobby design point
    out = continuous_current(design, r_phase_20c=10.0)

    assert out["dT"] == pytest.approx(75.0, rel=1e-9)
    assert out["a_surface_m2"] == pytest.approx(0.005026548245743669, rel=1e-9)
    assert out["p_dissipation_w"] == pytest.approx(5.654866776461628, rel=1e-9)
    assert out["r_phase_hot"] == pytest.approx(13.120000000000001, rel=1e-9)
    assert out["i_cont_a"] == pytest.approx(0.536041781474981, rel=1e-9)


def test_continuous_current_zero_when_no_dT():
    """No temperature headroom -> no continuous current."""
    design = MotorDesign(temp_limit_c=25.0, ambient_c=25.0)
    out = continuous_current(design, r_phase_20c=10.0)
    assert out["dT"] == 0.0
    assert out["p_dissipation_w"] == 0.0
    assert out["i_cont_a"] == 0.0


def test_continuous_current_fr4_warning():
    """A limit at/above FR4 Tg raises a derate warning."""
    design = MotorDesign(temp_limit_c=150.0)
    out = continuous_current(design, r_phase_20c=10.0)
    assert any("FR4" in w for w in out["warnings"])


def test_inertia_positive_and_scales():
    rotor = RotorConfig()
    j = rotor_inertia(rotor)
    assert j > 0
    assert j == pytest.approx(2.639974554591111e-05, rel=1e-9)

    # Doubling the magnet outer radius increases J (more area, larger radius,
    # bigger carrier disc).
    bigger = RotorConfig(magnet_r_outer_m=2.0 * rotor.magnet_r_outer_m)
    assert rotor_inertia(bigger) > j

    # total_inertia adds the load exactly.
    load = 1.234e-4
    assert total_inertia(rotor, load) == pytest.approx(j + load, rel=0, abs=0)
    assert total_inertia(rotor, 0.0) == pytest.approx(j)


def test_round_carrier_does_not_shrink_when_a_ring_is_removed():
    """Depopulating a magnet ring removes magnet mass but NOT the carrier disc.

    The carrier is one physical part sized to the designed rotor envelope, so the
    round / round_outer / round_inner variants must share an identical carrier
    inertia. (Regression: an earlier model sized the carrier to the outermost
    *populated* ring, letting round_inner cheat a tiny carrier and report an
    unphysically high acceleration.)
    """
    geom = dict(outer_ring_r_m=37.2e-3, outer_disc_d_m=15e-3,
                inner_ring_r_m=20.6e-3, inner_disc_d_m=8e-3)
    both = rotor_inertia(RotorConfig(magnet_topology="round", **geom))
    outer = rotor_inertia(RotorConfig(magnet_topology="round_outer", **geom))
    inner = rotor_inertia(RotorConfig(magnet_topology="round_inner", **geom))

    # Removing a ring strictly lowers J (less magnet), but never below the shared
    # carrier, and removing the heavier outer ring drops more than the inner one.
    assert both > outer > inner
    assert inner > 0

    # The carrier term is the SAME physical disc in every case: sized to the
    # outer ring's outer edge, independent of which rings carry magnets.
    from pcb_motor.constants import NDFEB_DENSITY, PLA_DENSITY
    t = RotorConfig().magnet_thickness_m
    tc = RotorConfig().carrier_thickness_m
    r_ext = geom["outer_ring_r_m"] + geom["outer_disc_d_m"] / 2.0
    j_carrier = 0.5 * (PLA_DENSITY * math.pi * r_ext**2 * tc) * r_ext**2

    # inner-only J is exactly that fixed carrier plus only the inner discs' mass.
    a = geom["inner_disc_d_m"] / 2.0
    R = geom["inner_ring_r_m"]
    j_mag_inner = 14 * (NDFEB_DENSITY * math.pi * a**2 * t) * (0.5 * a**2 + R**2)
    assert inner == pytest.approx(j_carrier + j_mag_inner, rel=1e-9)
