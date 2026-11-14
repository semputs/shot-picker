"""Import an existing edit list (edl_refined.json style) into a shot-picker project.

Each EDL entry has idx, file, in, dur (timeline seconds), speed, section and pipeline-only fields.
Source out = in + dur*speed. Everything except file/in/dur/section/tl_start goes into `extra`
(plus `_dur0`, the original timeline length, so a reader can stretch cards if a shot changes length).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from .model import Project, Section, Shot

KOREA = {
    "edl": r"C:\Sandbox\workspace\korea-trip-2023\analysis\edl_refined.json",
    "footage": r"E:\_Gopro\230429 Korea trip 2023",
    "out": r"C:\Sandbox\workspace\korea-trip-2023\analysis\shot_picker.shotpicker.json",
    "title": "Korea 2023",
}
SKIP = {"file", "in", "dur", "section", "tl_start", "idx"}


def _describer(edl_path: Path):
    """Use describe()+notes() from ../scripts/shot_list_md.py and clip_notes.md when the project has them."""
    analysis = edl_path.parent
    script = analysis.parent / "scripts" / "shot_list_md.py"
    notes_md = analysis / "clip_notes.md"
    if not (script.exists() and notes_md.exists()):
        return None
    spec = importlib.util.spec_from_file_location("_shot_list_md", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    desc = mod.notes(notes_md)
    return lambda s, t_in, t_out: mod.describe(desc.get(s["idx"], ""), t_in, t_out)


def import_edl(edl_path, footage_dir, out_path, title=None) -> Project:
    edl_path = Path(edl_path)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    describe = _describer(edl_path)
    p = Project(out_path)
    p.title = title or edl_path.parent.parent.name
    p.footage_dir = str(footage_dir)
    inv = edl_path.parent / "inventory.json"
    if inv.exists():
        p.clip_index = {c["file"]: c["idx"] for c in json.loads(inv.read_text(encoding="utf-8"))}
    for s in edl:
        p.clip_index.setdefault(s["file"], s["idx"])
        speed = float(s.get("speed", 1) or 1)
        t_out = round(s["in"] + s["dur"] * speed, 3)
        note = describe(s, s["in"], t_out) if describe else ""
        extra = {k: v for k, v in s.items() if k not in SKIP}
        extra["_dur0"] = s["dur"]
        name = s.get("section", "Shots")
        if not p.sections or p.sections[-1].name != name:
            p.sections.append(Section(name))
        p.sections[-1].shots.append(Shot(s["file"], float(s["in"]), t_out, note, extra))
    p.meta = {"imported_from": str(edl_path)}
    return p


def import_korea() -> Project:
    p = import_edl(KOREA["edl"], KOREA["footage"], KOREA["out"], KOREA["title"])
    p.save()
    return p
