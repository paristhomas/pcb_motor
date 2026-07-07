"""pcb_motor -- Biot-Savart coreless axial-flux PCB-motor simulator + optimiser.

For a given stator/rotor configuration, evaluates the PCB coil design directly
from copper geometry: Biot-Savart air-gap field -> Kt/torque -> thermal
continuous rating -> rotor inertia -> KiCad export. Every intermediate metric
(Kt, torque, drive voltage, current density, shear) is reported so it can drive
general motor-design work, not just the continuous-acceleration objective.

The model is analytical (~+/-30% absolute); treat every number as
feasibility-grade, not as a bench measurement.
"""

from __future__ import annotations

from .design import (
    CurrentSource,
    CoilGeometry,
    RotorConfig,
    MotorDesign,
    WindingTopology,
)

__all__ = [
    "CurrentSource",
    "CoilGeometry",
    "RotorConfig",
    "MotorDesign",
    "WindingTopology",
]
