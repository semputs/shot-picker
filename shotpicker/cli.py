"""Command line, for scripts and other agents (no window, no libmpv needed).

  python -m shotpicker info   <project.shotpicker.json>              check clips exist, print the shot list
  python -m shotpicker md     <project.shotpicker.json>              (re)write the readable .md next to it
  python -m shotpicker export <project.shotpicker.json> [--format 16:9|9:16] [--fps auto|30|60] [--out x.mp4]
  python -m shotpicker edl    <project.shotpicker.json> [--out base] write base.xml / .fcpxml / .otio for editors

Exit code 0 = OK, 1 = problem (message on stderr).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .model import Project, tc

COMMANDS = ("info", "md", "export", "edl")


def _load(path) -> Project:
    p = Project.open(path)
    p.files()
    return p


def info(args) -> int:
    p = _load(args.project)
    missing = sorted({s.file for s in p.all_shots() if not Path(p.abspath(s.file)).exists()})
    print(f"{p.title}: {len(p.all_shots())} shots, {tc(p.total())}, footage: {p.footage_dir or '(none)'}")
    n, t = 0, 0.0
    for sec in p.sections:
        print(f"\n## {sec.name}  ({tc(sum(s.dur for s in sec.shots))}, {len(sec.shots)} shots)")
        for s in sec.shots:
            n += 1
            note = s.note.replace("\n", " / ")
            print(f"  {n:>3} {tc(t):>7}  {Path(s.file).name}  {tc(s.t_in)}-{tc(s.t_out)}  {s.dur:.1f}s  {note}")
            t += s.dur
    if missing:
        print("\nMISSING CLIPS:\n  " + "\n  ".join(p.abspath(m) for m in missing), file=sys.stderr)
        return 1
    return 0


def md(args) -> int:
    p = _load(args.project)
    p.save()
    print(f"wrote {p.md_path()}")
    return 0


def export(args) -> int:
    from . import export as exporter
    p = _load(args.project)
    tag = "16x9" if args.format == "16:9" else "9x16"
    out = Path(args.out) if args.out else Path(args.project).with_name(
        f"{Path(args.project).name.replace('.shotpicker.json', '')} - {tag} - {time.strftime('%Y%m%d-%H%M')}.mp4")
    if p.footage_dir and out.resolve().is_relative_to(Path(p.footage_dir).resolve()):
        print("refusing to write into the footage folder", file=sys.stderr)
        return 1

    def progress(done, total, msg):
        print(f"[{done}/{total}] {msg}", flush=True)
    out = exporter.export(p, args.format, out, args.fps, progress=progress)
    print(f"wrote {out}")
    return 0


def edl(args) -> int:
    from . import interchange
    p = _load(args.project)
    base = Path(args.out) if args.out else Path(args.project).with_name(
        Path(args.project).name.replace(".shotpicker.json", ""))
    for f in interchange.export_all(p, base):
        print(f"wrote {f}")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="python -m shotpicker", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        sp = sub.add_parser(name)
        sp.add_argument("project")
        if name == "export":
            sp.add_argument("--format", choices=["16:9", "9:16"], default="16:9")
            sp.add_argument("--fps", default="auto", help="auto, 30, 60, or e.g. 30000/1001")
            sp.add_argument("--out")
        if name == "edl":
            sp.add_argument("--out", help="output path without extension")
    args = ap.parse_args(argv)
    try:
        return {"info": info, "md": md, "export": export, "edl": edl}[args.cmd](args)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
