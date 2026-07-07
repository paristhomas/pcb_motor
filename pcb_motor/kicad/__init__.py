"""KiCad export layer: coil artwork, production footprint, stator project.

- :mod:`pcb_motor.kicad.export` -- simple constant/stepped-width ``fp_line``
  exporter for a coil's raw polylines (quick artwork, no pads).
- :mod:`pcb_motor.kicad.footprint` -- the production two-sided filled-copper
  stator footprint: mirrored B.Cu, via stitch farm, net-bearing terminal pads,
  in-footprint series bridges, clearance-verified before writing.
- :mod:`pcb_motor.kicad.project` -- the KiCad project around that footprint:
  one stator symbol (pin number == footprint pad name), pre-wired WYE
  schematic, library tables and project file.

Every writer in this package emits CRLF line endings (KiCad saves CRLF;
mixed endings turn every in-KiCad save into a whole-file diff).
"""

from .export import coil_to_kicad_mod, write_coil_kicad_mod
from .footprint import (
    FootprintError,
    FootprintReport,
    build_footprint,
    stator_plan,
)
from .project import ProjectReport, build_kicad_project

__all__ = [
    "coil_to_kicad_mod",
    "write_coil_kicad_mod",
    "FootprintError",
    "FootprintReport",
    "build_footprint",
    "stator_plan",
    "ProjectReport",
    "build_kicad_project",
]
