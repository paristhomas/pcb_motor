"""Fab-ready Gerber + drill export for a KiCad ``.kicad_pcb``, via ``kicad-cli``.

The rest of this package hand-writes ``.kicad_pcb`` s-expression text; turning
that board into Gerbers is delegated to KiCad's own ``kicad-cli`` so the output
is exactly what a board house expects (apertures, attributes, drill format),
not our approximation of it. This module shells out to ``kicad-cli``, exports
the standard JLCPCB 2-layer set plus an Excellon drill file, and zips them.

``kicad-cli`` (KiCad >= 7; the emitted boards are KiCad 8 format) must be on
``PATH`` or passed explicitly. If it is absent, :func:`export_gerbers` raises
:class:`GerberError` with an actionable message rather than a bare
``FileNotFoundError`` -- callers (CLI, tests) can catch it and degrade
gracefully, leaving the ``.kicad_pcb`` on disk for the user to plot by hand.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import zipfile


class GerberError(RuntimeError):
    """Raised when Gerber export cannot run or ``kicad-cli`` reports failure."""


# Standard JLCPCB 2-layer fabrication layer set, in kicad-cli canonical names.
JLC_2LAYER = (
    "F.Cu",
    "B.Cu",
    "F.Paste",
    "B.Paste",
    "F.Silkscreen",
    "B.Silkscreen",
    "F.Mask",
    "B.Mask",
    "Edge.Cuts",
)

_INSTALL_HINT = (
    "kicad-cli not found. Install KiCad >= 7 (it ships kicad-cli), or pass "
    "kicad_cli=<path>. Debian/Ubuntu: `sudo apt install kicad`; for KiCad 8/9 "
    "use the kicad PPA (e.g. ppa:kicad/kicad-9.0-releases). On Windows the "
    "binary is typically C:\\Program Files\\KiCad\\<ver>\\bin\\kicad-cli.exe."
)


@dataclasses.dataclass
class GerberReport:
    """Outcome of a Gerber/drill export run."""

    available: bool          # was kicad-cli found and runnable
    kicad_version: str       # `kicad-cli version` output, or ""
    pcb_path: str
    out_dir: str
    files: list[str]         # basenames written into out_dir
    zip_path: str | None
    ok: bool                 # both exports succeeded and expected files exist
    stderr_tail: str = ""    # last stderr, for diagnostics

    def __str__(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        head = f"SUMMARY: {status}  ({len(self.files)} files"
        if self.zip_path:
            head += f", zip {os.path.basename(self.zip_path)}"
        head += f")  kicad-cli {self.kicad_version or 'unknown'}"
        lines = [head] + [f"  {f}" for f in sorted(self.files)]
        if not self.ok and self.stderr_tail:
            lines.append(f"  stderr: {self.stderr_tail.strip()[-500:]}")
        return "\n".join(lines)


def _resolve_cli(kicad_cli: str | None) -> str:
    exe = kicad_cli or shutil.which("kicad-cli")
    if not exe:
        raise GerberError(_INSTALL_HINT)
    if kicad_cli and not shutil.which(kicad_cli) and not os.path.exists(kicad_cli):
        raise GerberError(f"kicad-cli path not found: {kicad_cli}\n{_INSTALL_HINT}")
    return exe


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:                      # exe vanished mid-run
        raise GerberError(f"{cmd[0]}: {e}\n{_INSTALL_HINT}") from e


def _version(exe: str) -> str:
    cp = _run([exe, "version"])
    return (cp.stdout or cp.stderr or "").strip().splitlines()[0] if cp.returncode == 0 else ""


def export_gerbers(
    pcb_path: str,
    out_dir: str | None = None,
    *,
    zip_path: str | None = None,
    kicad_cli: str | None = None,
    layers: "tuple[str, ...] | list[str]" = JLC_2LAYER,
) -> GerberReport:
    """Export ``pcb_path`` to a Gerber + Excellon-drill set and zip it.

    ``out_dir`` defaults to ``<pcb_dir>/gerbers``; ``zip_path`` defaults to
    ``<pcb_dir>/<stem>_gerbers.zip``. Runs two ``kicad-cli`` calls (gerbers,
    then drill) with the drill origin matched to the gerber origin so the sets
    register. Returns a :class:`GerberReport`; raises :class:`GerberError` if
    ``kicad-cli`` is missing or either call fails.
    """
    if not os.path.exists(pcb_path):
        raise GerberError(f"board not found: {pcb_path}")
    exe = _resolve_cli(kicad_cli)
    version = _version(exe)

    pcb_dir = os.path.dirname(os.path.abspath(pcb_path))
    stem = os.path.splitext(os.path.basename(pcb_path))[0]
    out_dir = out_dir or os.path.join(pcb_dir, "gerbers")
    os.makedirs(out_dir, exist_ok=True)

    gerber_cmd = [
        exe, "pcb", "export", "gerbers",
        "--output", out_dir,
        "--layers", ",".join(layers),
        "--use-drill-file-origin",
        pcb_path,
    ]
    drill_cmd = [
        exe, "pcb", "export", "drill",
        "--output", out_dir + os.sep,
        "--format", "excellon",
        "--drill-origin", "absolute",
        "--excellon-units", "mm",
        "--generate-map-file",
        "--map-format", "gerberx2",
        pcb_path,
    ]

    stderr_tail = ""
    for cmd in (gerber_cmd, drill_cmd):
        cp = _run(cmd)
        if cp.returncode != 0:
            stderr_tail = cp.stderr or cp.stdout or ""
            raise GerberError(
                f"kicad-cli failed ({' '.join(cmd[1:4])}, rc={cp.returncode}):\n"
                f"{stderr_tail.strip()[-800:]}"
            )

    files = sorted(
        f for f in os.listdir(out_dir)
        if not f.endswith(".zip")
    )
    zip_path = zip_path or os.path.join(pcb_dir, f"{stem}_gerbers.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(os.path.join(out_dir, f), arcname=f)

    ok = bool(files) and os.path.exists(zip_path)
    return GerberReport(
        available=True,
        kicad_version=version,
        pcb_path=pcb_path,
        out_dir=out_dir,
        files=files,
        zip_path=zip_path,
        ok=ok,
        stderr_tail=stderr_tail,
    )
