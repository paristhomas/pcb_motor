"""Physical constants and material data for the PCB-motor design engine.

All values SI unless noted. Sources are standard engineering references; numbers
that materially affect the result carry roughly +/-20% uncertainty and should be
bench/FEMM validated before committing to a build.
"""

from __future__ import annotations

# --- Fundamental ---
MU0 = 4.0e-7 * 3.141592653589793  # vacuum permeability [H/m]
G = 9.80665                       # gravitational acceleration [m/s^2]

# --- Copper (electrical) ---
RHO_CU_20 = 1.68e-8        # resistivity at 20 C [ohm*m]
ALPHA_CU = 0.0039          # temperature coefficient of resistivity [1/K]
RHO_CU_DENSITY = 8960.0    # mass density [kg/m^3]

# Copper foil thickness by weight. 1 oz/ft^2 = 35 um is the JLCPCB default.
COPPER_THICKNESS = {
    0.5: 17.5e-6,
    1.0: 35e-6,
    2.0: 70e-6,
}

# --- FR4 substrate (thermal) ---
FR4_TG = 130.0             # glass-transition temperature [C] (standard Tg-130 FR4)
FR4_DENSITY = 1850.0       # [kg/m^3]

# --- NdFeB magnet grades: remanence Br [T] (typical room-temp values) ---
# Recoil relative permeability mu_r ~ 1.05 for all sintered NdFeB.
NDFEB_BR = {
    "N35": 1.20,
    "N42": 1.32,
    "N45": 1.35,
    "N48": 1.40,
    "N52": 1.43,
}
NDFEB_MU_R = 1.05
NDFEB_DENSITY = 7500.0     # [kg/m^3]

# --- 3D-print / structural materials ---
PLA_DENSITY = 1240.0       # [kg/m^3] (typical FDM PLA)

# --- Steel back-iron / flux-return sheets ---
STEEL_DENSITY = 7870.0     # [kg/m^3] (low-carbon / electrical steel)
STEEL_B_SAT = 1.5          # working flux ceiling used to size the yoke [T]


def copper_resistivity(temp_c: float) -> float:
    """Copper resistivity [ohm*m] at temperature ``temp_c`` [C]."""
    return RHO_CU_20 * (1.0 + ALPHA_CU * (temp_c - 20.0))


def copper_thickness(weight_oz: float) -> float:
    """Copper foil thickness [m] for a given weight in oz/ft^2."""
    if weight_oz in COPPER_THICKNESS:
        return COPPER_THICKNESS[weight_oz]
    # Linear in weight: 1 oz = 35 um.
    return weight_oz * 35e-6


def magnet_br(grade: str) -> float:
    """Remanence Br [T] for an NdFeB grade string (e.g. 'N42')."""
    key = grade.upper()
    if key not in NDFEB_BR:
        raise ValueError(f"Unknown magnet grade {grade!r}; known: {sorted(NDFEB_BR)}")
    return NDFEB_BR[key]
