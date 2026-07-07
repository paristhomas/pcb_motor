"""pcb-motor command-line interface.

    pcb-motor new          --session NAME [--set field=value ...]
    pcb-motor point        [--session NAME] [--set field=value ...]
    pcb-motor report       [--session NAME | --set ...]
    pcb-motor datasheet    [--session NAME | --set ...]
    pcb-motor compare      NAME1 NAME2 ...
    pcb-motor sweep        --inputs trace_width_mm,trace_space_mm [--serve]
    pcb-motor optimize     [--inputs ...] [--trials 100]

(Equivalently ``python -m pcb_motor <subcommand>``.)

``new``, ``point``, ``report``, ``datasheet`` and ``compare`` run the pure
simulator (no bencher needed). ``sweep`` / ``optimize`` need holobench
(``pip install "pcb-motor[sweep]"``).
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from .design import MotorDesign
from .evaluate import evaluate_design


def _apply_overrides(design: MotorDesign, sets: list[str]) -> MotorDesign:
    """Apply ``field=value`` overrides (SI field names on MotorDesign)."""
    valid = {f.name: f.type for f in dataclasses.fields(MotorDesign)}
    for item in sets:
        if "=" not in item:
            raise SystemExit(f"--set expects field=value, got {item!r}")
        key, val = item.split("=", 1)
        key = key.strip()
        if key not in valid:
            raise SystemExit(f"unknown field {key!r}; choose from {sorted(valid)}")
        cur = getattr(design, key)
        if isinstance(cur, bool):
            newv = val.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(cur, int):
            newv = int(round(float(val)))
        elif isinstance(cur, float):
            newv = float(val)
        else:
            newv = val.strip()
        setattr(design, key, newv)
    return design


def _print_point(design: MotorDesign) -> None:
    r = evaluate_design(design)
    print(f"pcb-motor point  [{design.winding_topology}, {design.pole_pairs}pp, "
          f"{design.magnet_grade}, {design.n_stators} stator(s)]")
    print("-" * 56)
    rows = [
        ("Continuous acceleration", "accel_cont_rad_s2", "rad/s^2"),
        ("Continuous torque", "tau_cont_mNm", "mNm"),
        ("Kt (torque constant)", "kt_mNm_per_A", "mNm/A"),
        ("Continuous current", "i_cont_A", "A"),
        ("Mean airgap |Bz|", "b_gap_mean_T", "T"),
        ("Peak airgap |Bz|", "b_gap_peak_T", "T"),
        ("Phase resistance (20C)", "r_phase_20c_ohm", "ohm"),
        ("Rotor inertia J", "j_rotor_kgm2", "kg*m^2"),
        ("Total inertia J", "j_total_kgm2", "kg*m^2"),
        ("Turns / phase / layer-set", "n_turns", ""),
        ("Copper mass", "copper_mass_g", "g"),
        ("Current density", "current_density_A_mm2", "A/mm^2"),
        ("Drive voltage (cont)", "v_drive_cont_V", "V"),
        ("Airgap shear", "shear_stress_kPa", "kPa"),
        ("Phase inductance (air-core)", "l_phase_uH", "uH"),
        ("PWM ripple @bus/fsw", "pwm_ripple_A_pp", "A pp"),
        ("Ext. L for ripple budget", "l_ext_uH", "uH"),
        ("Eddy loss @ref speed", "eddy_loss_W_ref", "W"),
        ("Iron plate pull (per side)", "plate_pull_N", "N"),
        ("Torque density", "torque_density_Nm_kg", "Nm/kg"),
        ("End-turn fraction", "end_turn_fraction", ""),
        ("Winding factor kw1", "winding_factor", ""),
        ("Winding utilisation", "winding_utilisation", ""),
    ]
    for label, key, unit in rows:
        print(f"  {label:28s} {r[key]:12.4g} {unit}")
    if r["warnings"]:
        print("\nwarnings:")
        for w in r["warnings"]:
            print(f"  - {w}")


def _design_for(args):
    """Build a ``MotorDesign`` from ``--session`` (if given) plus ``--set`` overrides.

    A session loads the saved motor as the base; ``--set`` overrides still apply on
    top, so you can tweak a saved design without re-saving it. Without a session it
    starts from ``MotorDesign()`` defaults (the original behaviour).
    """
    base = MotorDesign()
    if getattr(args, "session", None):
        from . import session as sessions
        s = sessions.Session(args.session, root=args.root)
        if not s.exists():
            raise SystemExit(
                f"session {args.session!r} has no saved motor under {args.root}/"
            )
        base = s.load_motor()
    return _apply_overrides(base, args.sets)


def _resolve_design(args):
    """Source a (design, session) pair for report/datasheet.

    A ``--session`` loads the saved motor; otherwise the design is
    ``MotorDesign()`` with ``--set`` overrides and there is no session.
    """
    from . import session as sessions
    session = None
    if getattr(args, "session", None):
        session = sessions.Session(args.session, root=args.root)
        if not session.exists():
            raise SystemExit(
                f"session {args.session!r} has no saved motor; "
                f"run 'pcb-motor new --session {args.session}' first"
            )
        design = session.load_motor()
    else:
        design = _apply_overrides(MotorDesign(), args.sets)
    return design, session


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="pcb-motor", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("point", help="evaluate one design and print results")
    pp.add_argument("--set", action="append", default=[], dest="sets",
                    help="override a MotorDesign field, e.g. --set trace_width_m=2e-4")
    pp.add_argument("--session", help="base the design on designs/<name>/")
    pp.add_argument("--root", default="designs")

    pc = sub.add_parser("config", help="save a setup figure (winding + rotor + stack)")
    pc.add_argument("--set", action="append", default=[], dest="sets")
    pc.add_argument("--out", default="setup.png", help="output PNG path")
    pc.add_argument("--session", help="base the design on designs/<name>/")
    pc.add_argument("--root", default="designs")

    pe = sub.add_parser("export", help="export coil geometry as a KiCad footprint (.kicad_mod)")
    pe.add_argument("--set", action="append", default=[], dest="sets",
                    help="override a MotorDesign field, e.g. --set corner_radius_m=2e-4")
    pe.add_argument("--out", default="coil.kicad_mod", help="output .kicad_mod path")
    pe.add_argument("--layer", default="F.Cu", help="KiCad copper layer name")
    pe.add_argument("--single-coil", action="store_true",
                    help="export one tooth/coil (first angular sector) instead of the whole layer")
    pe.add_argument("--session", help="base the design on designs/<name>/")
    pe.add_argument("--root", default="designs")

    pw = sub.add_parser("sweep", help="bencher sweep dashboard (needs holobench)")
    pw.add_argument("--inputs", required=True, help="1-3 sweep axes, comma-separated")
    pw.add_argument("--results", help="result vars (comma-separated; default headline set)")
    pw.add_argument("--out", default="dashboard", help="output directory")
    pw.add_argument("--set", action="append", default=[], dest="sets",
                    help="hold a sweep input fixed, e.g. --set r_outer_mm=45 (display units)")
    pw.add_argument("--serve", action="store_true", help="serve live instead of saving")
    pw.add_argument("--port", type=int, default=9001)
    pw.add_argument("--no-cache", action="store_true")

    po = sub.add_parser("optimize", help="maximise continuous acceleration (needs holobench)")
    po.add_argument("--inputs", help="design axes to optimise (comma-separated)")
    po.add_argument("--trials", type=int, default=100)

    pf = sub.add_parser("new",
                        help="seed a new design session from defaults + --set overrides")
    pf.add_argument("--session", required=True,
                    help="save the motor into designs/<name>/")
    pf.add_argument("--set", action="append", default=[], dest="sets",
                    help="set a MotorDesign field, e.g. --set pole_pairs=7 --set n_slots=12")
    pf.add_argument("--root", default="designs")

    pr = sub.add_parser("report", help="write a combined HTML design report")
    pr.add_argument("--set", action="append", default=[], dest="sets")
    pr.add_argument("--session", help="load the design from designs/<name>/")
    pr.add_argument("--out", help="output HTML path (default: session dir or design_report.html)")
    pr.add_argument("--name", help="report title")
    pr.add_argument("--rich", action="store_true",
                    help="also embed the Biot-Savart rotating-field & torque figures (slow, ~1 min)")
    pr.add_argument("--root", default="designs")

    pds = sub.add_parser("datasheet", help="write a Markdown key-parameters datasheet")
    pds.add_argument("--set", action="append", default=[], dest="sets")
    pds.add_argument("--session", help="load the design from designs/<name>/")
    pds.add_argument("--out", help="output Markdown path (default: session dir or datasheet.md)")
    pds.add_argument("--name", help="datasheet title")
    pds.add_argument("--root", default="designs")

    pcmp = sub.add_parser("compare", help="compare saved design sessions side by side")
    pcmp.add_argument("names", nargs="+", help="session names under designs/")
    pcmp.add_argument("--out", help="write the comparison table to this Markdown file")
    pcmp.add_argument("--root", default="designs")

    args = p.parse_args(argv)

    if args.cmd == "point":
        design = _design_for(args)
        _print_point(design)
        return 0

    if args.cmd == "config":
        from . import viz
        design = _design_for(args)
        fig = viz.plot_setup(design)
        fig.savefig(args.out, dpi=120, bbox_inches="tight")
        print(f"setup figure written to {args.out}")
        return 0

    if args.cmd == "export":
        from .kicad import export as K
        from .coils import build_coil
        design = _design_for(args)
        geo = build_coil(design)
        polylines = K._layer0_polylines(geo.polylines)
        if args.single_coil:
            polylines = K._first_sector_polylines(polylines, design.n_slots)
        if not polylines:
            print("no polylines to export")
            return 2
        width_fn = None
        width_note = f"width {design.trace_width_m*1e3:.3f} mm"
        if getattr(design, "tapered_traces", False):
            from .coil_spiral import trace_width_at
            width_fn = lambda r: trace_width_at(design, r)
            w_out = float(trace_width_at(design, design.r_outer_m))
            width_note = (f"tapered width {design.trace_width_m*1e3:.3f}"
                          f"-{w_out*1e3:.3f} mm")
        n_lines = K.write_coil_kicad_mod(
            args.out, polylines, design.trace_width_m, layer=args.layer,
            name=f"pcb_motor_{design.winding_topology}", width_fn=width_fn,
        )
        scope = "single coil" if args.single_coil else "front copper layer"
        print(f"KiCad footprint written to {args.out} "
              f"({scope}: {len(polylines)} traces, {n_lines} fp_line segments, "
              f"{width_note} on {args.layer})")
        return 0

    if args.cmd in ("sweep", "optimize"):
        try:
            from . import sweep as S
        except ImportError as exc:
            print(exc)
            return 2
        inputs = [s.strip() for s in (args.inputs or "").split(",") if s.strip()]
        if args.cmd == "sweep":
            results = ([s.strip() for s in args.results.split(",") if s.strip()]
                       if args.results else None)
            const = {}
            for item in args.sets:
                k, _, v = item.partition("=")
                v = v.strip()
                if v.lower() in ("true", "false", "yes", "no", "on", "off"):
                    const[k.strip()] = v.lower() in ("true", "yes", "on")
                    continue
                try:
                    const[k.strip()] = float(v)
                except ValueError:
                    const[k.strip()] = v
            path = S.build_dashboard(
                input_vars=inputs, result_vars=results, out_dir=args.out,
                serve=args.serve, port=args.port, cache=not args.no_cache, const=const,
            )
            if path:
                print(f"dashboard written to {path}")
            return 0
        else:
            res = S.optimize(input_vars=inputs or None, n_trials=args.trials)
            print("optimization complete:", res)
            return 0

    if args.cmd == "new":
        from . import session as sessions
        design = _apply_overrides(MotorDesign(), args.sets)
        s = sessions.Session(args.session, root=args.root)
        s.save_motor(design)
        print(f"session saved to {s.dir}")
        _print_point(design)
        return 0

    if args.cmd == "report":
        from . import report as R
        design, session = _resolve_design(args)
        out = args.out or (str(session.report_html) if session else "design_report.html")
        R.build_design_report(design, out, name=args.name, rich=args.rich)
        print(f"design report written to {out}")
        return 0

    if args.cmd == "datasheet":
        from . import session as sessions
        design, session = _resolve_design(args)
        out = args.out or (str(session.datasheet_md) if session else "datasheet.md")
        text = sessions.datasheet(design, name=args.name)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"datasheet written to {out}")
        return 0

    if args.cmd == "compare":
        from . import session as sessions
        sessions_list = []
        for nm in args.names:
            s = sessions.Session(nm, root=args.root)
            if not s.exists():
                raise SystemExit(f"session {nm!r} has no saved motor under {args.root}/")
            sessions_list.append(s)
        table = sessions.compare(sessions_list)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(table)
            print(f"comparison written to {args.out}")
        else:
            print(table)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
