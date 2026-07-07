"""Stator back iron via the method of images.

``back_iron=True`` means: a flat high-permeability plate behind EACH stator
board (stator-side return path). For a mu->infinity plane, the image of a
current element is the geometrically mirrored element carrying the *same*
current -- mirroring flips the z-component of every segment vector while
keeping the in-plane components, which is exactly the boundary condition
(tangential H cancels, normal B doubles at the plate face).

Two plates make an infinite image series (reflections of reflections); each
double reflection pushes the image a full plate-to-plate spacing further away,
so contributions decay like a dipole (~1/d^3) and the series is truncated at
``order`` reflections per chain (default 3, <<1% residual for this geometry).

Modelled: rotor field amplification (B_gap, Kt, torque) and winding
inductance amplification (parasitics adds coil images with the same
machinery). NOT modelled: saturation of the plate (keep plate thickness
sensible -- ~1 mm mild steel carries ~0.5 T return flux over these pole
pitches), eddy/hysteresis drag in the plate (negligible at gimbal speeds,
material choice matters above a few hundred rpm), and the axial attraction's
negative stiffness (the *nominal per-plate pull* is reported via the Maxwell
stress integral so the mechanical design can size spacers/bearings).
"""

from __future__ import annotations

import numpy as np

from .design import CurrentSource, RotorConfig


def iron_plane_z(rotor: RotorConfig) -> float:
    """Axial position of the iron plate's inner face (+z side; -z mirrors).

    The plate sits flush on the back of the stator board:
    ``t_magnet/2 + air_gap + board_thickness + standoff``.
    """
    return (rotor.magnet_thickness_m / 2.0 + rotor.air_gap_m
            + rotor.board_thickness_m + rotor.iron_standoff_m)


def _mirror(source: CurrentSource, z_plane: float) -> CurrentSource:
    verts = source.vertices.copy()
    verts[:, 2] = 2.0 * z_plane - verts[:, 2]
    return CurrentSource(verts, source.currents.copy())


def iron_images(source: CurrentSource, z_top: float, z_bot: float,
                order: int = 3) -> list[CurrentSource]:
    """Image sources for two mu->infinity planes at ``z_top`` / ``z_bot``.

    Two chains of alternating reflections (first-in-top and first-in-bottom),
    each ``order`` deep -> ``2*order`` images.
    """
    images: list[CurrentSource] = []
    for first_top in (True, False):
        cur = source
        plane_top = first_top
        for _ in range(order):
            cur = _mirror(cur, z_top if plane_top else z_bot)
            images.append(cur)
            plane_top = not plane_top
    return images


def with_iron_images(source: CurrentSource, rotor: RotorConfig,
                     order: int = 3) -> CurrentSource:
    """The source plus its back-iron images (identity when ``back_iron`` off)."""
    if not rotor.back_iron:
        return source
    z_p = iron_plane_z(rotor)
    return CurrentSource.concat([source]
                                + iron_images(source, z_p, -z_p, order))


def plate_axial_force(design, n_r: int = 24, n_phi: int = 60) -> float:
    """Nominal magnetic pull [N] on ONE iron plate (Maxwell stress, Bz^2/2mu0
    over the plate face). Equal and opposite on the rotor per side; nominally
    balanced for a centred rotor but each spacer stack carries this load, and
    the axial stiffness is negative -- size the mechanics for it."""
    from .field import b_field_at_points
    from .magnets import magnet_segments

    rotor = design.rotor()
    if not rotor.back_iron:
        return 0.0
    z_p = iron_plane_z(rotor)
    r = np.linspace(max(1e-3, design.r_inner_m * 0.5),
                    design.r_outer_m * 1.05, n_r)
    phi = np.linspace(0.0, 2.0 * np.pi, n_phi, endpoint=False)
    R, PHI = np.meshgrid(r, phi, indexing="ij")
    pts = np.column_stack([(R * np.cos(PHI)).ravel(),
                           (R * np.sin(PHI)).ravel(),
                           np.full(R.size, z_p)])
    src = with_iron_images(magnet_segments(rotor), rotor)
    B = b_field_at_points(src, pts, design.coil_resolution_m)
    bz2 = (B[:, 2] ** 2).reshape(n_r, n_phi)
    mu0 = 4e-7 * np.pi
    # Polar area integral of the Maxwell normal stress.
    integrand = bz2.mean(axis=1) * 2.0 * np.pi * r / (2.0 * mu0)
    return float(np.trapezoid(integrand, r))
