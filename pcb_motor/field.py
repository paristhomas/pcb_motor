"""Biot-Savart field evaluation (vectorised numpy kernel, SI in/out).

The kernel evaluates all points against all current segments at once
(broadcast), chunked over points to bound memory -- fast enough to sit inside
an optimisation loop. It is validated to <1% against the closed-form on-axis
field of a circular current loop (``tests/test_field.py``).

Internally the arithmetic runs in the historical cm/gauss unit system (where
``mu_0/4pi`` is exactly 0.1 for lengths in cm, current in A, B in gauss),
inherited from the reference kernel this implementation was cross-validated
against. This module is the *only* place unit conversion happens: callers pass
metres / amperes and get tesla back.

This is an independent NumPy reimplementation; it does not reuse any of that
kernel's code. Acknowledgement to the "Biot-Savart Magnetic Field Calculator"
by Mingde Yin and Ryan Zazo, the reference against which this module's output
was cross-validated (and the source of the cm/gauss convention above).
"""

from __future__ import annotations

import numpy as np

from .design import CurrentSource

# Conversions
_M_TO_CM = 100.0        # metres -> centimetres
_GAUSS_TO_TESLA = 1e-4  # gauss -> tesla
_FACTOR = 0.1           # mu_0/4pi when lengths are in cm, current in A, B in gauss
_POINT_CHUNK = 256      # evaluate this many points per broadcast block (memory cap)


def _subdivide(starts_cm: np.ndarray, ends_cm: np.ndarray, currents: np.ndarray,
               resolution_cm: float):
    """Split each straight segment into pieces no longer than ``resolution_cm``.

    The midpoint rule's error grows like ``(L/d)^2`` for a segment of length L
    seen from distance d, so coarse polylines (e.g. magnet arcs) must be
    subdivided to stay accurate near the conductor. Returns subdivided segment
    midpoints, segment vectors (dl) and currents.
    """
    seg = ends_cm - starts_cm                       # (M, 3)
    seglen = np.linalg.norm(seg, axis=1)            # (M,)
    k = np.maximum(1, np.ceil(seglen / resolution_cm).astype(int))  # pieces per seg

    mids, dls, curs = [], [], []
    for s, e, I, kk in zip(starts_cm, ends_cm, currents, k):
        # kk equal pieces along s->e
        t = np.linspace(0.0, 1.0, kk + 1)           # (kk+1,)
        pts = s[None, :] + t[:, None] * (e - s)[None, :]  # (kk+1, 3)
        sub_s = pts[:-1]
        sub_e = pts[1:]
        mids.append((sub_s + sub_e) / 2.0)
        dls.append(sub_e - sub_s)
        curs.append(np.full(kk, I))
    return (np.vstack(mids), np.vstack(dls), np.concatenate(curs))


def b_field_at_points(
    source: CurrentSource,
    points_m: np.ndarray,
    resolution_m: float = 0.5e-3,
) -> np.ndarray:
    """B in TESLA at each point.

    Parameters
    ----------
    source:
        The current-carrying polyline (SI: vertices in metres, currents in
        amperes; ``currents[i]`` flows in the segment ``i -> i+1``). Segments
        with zero current (e.g. the breaks ``CurrentSource.concat`` inserts
        between loops) contribute nothing.
    points_m:
        Field evaluation points in metres. Shape ``(3,)`` for a single point or
        ``(P, 3)`` for many.
    resolution_m:
        Segment resampling length in metres; finer improves accuracy near the
        conductor at the cost of more work.

    Returns
    -------
    np.ndarray
        ``(P, 3)`` array of B in tesla.
    """
    pts = np.asarray(points_m, dtype=float).reshape(-1, 3)

    verts_cm = source.vertices * _M_TO_CM            # (N, 3)
    currents = np.asarray(source.currents, dtype=float)
    if verts_cm.shape[0] < 2:
        return np.zeros((pts.shape[0], 3))

    # Build straight segments from consecutive vertices; drop zero-current ones.
    starts = verts_cm[:-1]
    ends = verts_cm[1:]
    seg_cur = currents[:-1]
    nz = seg_cur != 0.0
    if not np.any(nz):
        return np.zeros((pts.shape[0], 3))
    mids, dls, curs = _subdivide(starts[nz], ends[nz], seg_cur[nz],
                                 resolution_m * _M_TO_CM)

    pts_cm = pts * _M_TO_CM
    out = np.empty((pts_cm.shape[0], 3), dtype=float)
    # Chunk over points so the (chunk, segs, 3) intermediate stays bounded.
    Idl = curs[:, None] * dls                         # (S, 3) current-weighted dl
    for lo in range(0, pts_cm.shape[0], _POINT_CHUNK):
        hi = min(lo + _POINT_CHUNK, pts_cm.shape[0])
        r = pts_cm[lo:hi, None, :] - mids[None, :, :]      # (q, S, 3)
        rmag = np.linalg.norm(r, axis=2)                   # (q, S)
        rmag3 = rmag ** 3
        # dB = I dl x r / |r|^3 ; cross over last axis
        cross = np.cross(Idl[None, :, :], r)               # (q, S, 3)
        with np.errstate(divide="ignore", invalid="ignore"):
            contrib = cross / rmag3[:, :, None]
        contrib[~np.isfinite(contrib)] = 0.0               # skip r==0 singularities
        out[lo:hi] = _FACTOR * contrib.sum(axis=1)

    return out * _GAUSS_TO_TESLA
