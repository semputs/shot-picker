"""Markdown view of a project, and parsing edits back into it.

Format (strict, one block per shot):

    # Title
    *21:39.9 · 296 shots*                       <- totals, regenerated

    ## Day 0 - KL to Seoul                      <- section heading (editable)
    *0:03.0 · 0:34.4 · 7 shots*                 <- totals, regenerated

    ### 2 · 0:03.0                              <- shot number + timeline time, regenerated
    Check-in hall — Mum poses with suitcase     <- note (editable, any number of lines)
    `#3 · DJI_20230425200712_0003_D · 0:20.0–0:26.0 · 6.0s`   <- clip, in–out (editable)
    > [DAY 0 · Tue · 25 April] [Sound up]       <- kept settings, read-only

Editing rules: section headings, notes and in–out times sync back; moving whole blocks
reorders shots; deleting a block deletes the shot; a new block with a valid clip line adds one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Project, Section, Shot, clean_note, parse_tc, tc

TC = r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?"
META = re.compile(rf"^`?\s*(?:#(\d+)\s*·\s*)?(.+?)\s*·\s*({TC})\s*[–—-]\s*({TC})(?:\s*·\s*[\d.]+s)?\s*`?\s*$")
SHOT_H = re.compile(r"^###(?:\s|$)")
SEC_H = re.compile(r"^##\s+(.*?)\s*$")
TITLE_H = re.compile(r"^#\s+(.*?)\s*$")
TOTALS = re.compile(r"^\*[^*].*\*\s*$")
TOL = 0.06  # display shows 0.1 s, so anything within rounding keeps the exact stored time


def tags(s: Shot) -> list[str]:
    e, out = s.extra, []
    if e.get("main"):
        out.append("Main title")
    if e.get("day"):
        out.append(" · ".join(e["day"]))
    if e.get("place"):
        out.append("📍 " + " · ".join(e["place"]))
    if e.get("end"):
        out.append("End card")
    sp = s.speed
    if sp < 1:
        out.append(f"{1 / sp:g}× slow-mo")
    elif sp > 1:
        out.append(f"{sp:g}× speed")
    if e.get("audio") == "nat":
        out.append("Sound up")
    elif e.get("audio") == "mute":
        out.append("Muted")
    if e.get("fade"):
        out.append(f"fade {e['fade']}")
    if e.get("stab"):
        out.append("Steady" if e["stab"] is True else f"Steady {e['stab']}%")
    if e.get("mute"):
        out.append("🔇 game sound off")
    elif e.get("vol_db"):
        out.append(f"🔊 {float(e['vol_db']):+g} dB")
    return out


def _plural(n):
    return f"{n} shot" + ("" if n == 1 else "s")


def render(p: Project) -> tuple[str, dict[int, tuple[int, int]]]:
    """Return markdown text and {shot uid: (first_line, last_line)}."""
    L = [f"# {p.title}", f"*{tc(p.total())} · {_plural(len(p.all_shots()))}*"]
    spans = {}
    n, t = 0, 0.0
    for sec in p.sections:
        secdur = sum(s.dur for s in sec.shots)
        L += ["", f"## {sec.name}", f"*{tc(t)} · {tc(secdur)} · {_plural(len(sec.shots))}*"]
        for s in sec.shots:
            n += 1
            L.append("")
            a = len(L)
            L.append(f"### {n} · {tc(t)}")
            if s.note:
                L += s.note.splitlines()
            idx = p.idx(s.file)
            stem = s.file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            L.append(f"`{'#%d · ' % idx if idx else ''}{stem} · {tc(s.t_in)}–{tc(s.t_out)} · {s.dur:.1f}s`")
            tg = tags(s)
            if tg:
                L.append("> " + " ".join(f"[{x}]" for x in tg))
            spans[s.uid] = (a, len(L) - 1)
            t += s.dur
    return "\n".join(L) + "\n", spans


@dataclass
class PShot:
    key: str
    t_in: float
    t_out: float
    note_lines: list[str] = field(default_factory=list)
    line: int = 0
    end: int = 0
    tag_lines: list[str] = field(default_factory=list)  # "> ..." lines; kept as note unless unchanged


def parse(text: str, p: Project):
    """-> (title, [(section name, [PShot])], errors)"""
    title, secs, errors = None, [], []
    cur: PShot | None = None
    pending: list[str] = []   # lines of a shot block before its clip line
    pending_tags: list[str] = []
    block_start = None

    def close_block(i):
        nonlocal block_start, pending, pending_tags
        if block_start is not None and cur is None:
            errors.append(f"line {block_start + 1}: shot has no clip line (`#idx · file · in–out`)")
        block_start, pending, pending_tags = None, [], []

    lines = text.splitlines()
    for i, raw in enumerate(lines):
        ln = raw.rstrip()
        if SHOT_H.match(ln):
            close_block(i)
            cur = None
            block_start = i
            if not secs:
                secs.append((None, []))
            continue
        m = SEC_H.match(ln)
        if m and not ln.startswith("###"):
            close_block(i)
            cur = None
            secs.append((m[1], []))
            continue
        m = TITLE_H.match(ln)
        if m and not ln.startswith("##"):
            close_block(i)
            cur = None
            title = m[1]
            continue
        if block_start is None:
            continue  # totals / blank / stray text outside shot blocks
        if ln.lstrip().startswith(">"):
            body = ln.lstrip()[1:].strip()
            if body:
                (cur.tag_lines if cur is not None else pending_tags).append(body)
            if cur is not None:
                cur.end = i
            continue
        if not ln.strip():
            continue
        if cur is None:
            mm = META.match(ln.strip())
            if mm:
                key = p.key_for_stem(mm[2])
                if key is None:
                    errors.append(f"line {i + 1}: unknown clip '{mm[2]}'")
                    block_start = None
                    continue
                a, b = parse_tc(mm[3]), parse_tc(mm[4])
                if b <= a:
                    errors.append(f"line {i + 1}: out ({mm[4]}) must be after in ({mm[3]})")
                cur = PShot(key, min(a, b), max(a, b), list(pending), block_start, i, list(pending_tags))
                secs[-1][1].append(cur)
            else:
                pending.append(ln.strip())
        else:
            cur.note_lines.append(ln.strip())  # text after the clip line still counts as note
            cur.end = i
    close_block(len(lines))
    return title, secs, errors


def apply(p: Project, title, secs) -> dict:
    """Rebuild p.sections from parsed markdown, keeping each matched shot's extra fields and uid.
    Returns stats {'added', 'removed', 'changed'} and fills 'lines' {uid: (first, last)}."""
    old = p.all_shots()
    used: set[int] = set()
    flat = [ps for _, shots in secs for ps in shots]
    match: dict[int, Shot] = {}
    # pass 1: same clip and same in/out
    for k, ps in enumerate(flat):
        for s in old:
            if s.uid not in used and s.file == ps.key and abs(s.t_in - ps.t_in) < TOL and abs(s.t_out - ps.t_out) < TOL:
                match[k] = s
                used.add(s.uid)
                break
    # pass 2: same clip and same note (times edited)
    for k, ps in enumerate(flat):
        if k in match:
            continue
        note = clean_note("\n".join(ps.note_lines))
        for s in old:
            if s.uid not in used and s.file == ps.key and s.note == note:
                match[k] = s
                used.add(s.uid)
                break
    # pass 3: same clip, in order
    for k, ps in enumerate(flat):
        if k in match:
            continue
        for s in old:
            if s.uid not in used and s.file == ps.key:
                match[k] = s
                used.add(s.uid)
                break
    stats = {"added": 0, "removed": len(old) - len(used), "changed": 0, "tag_notes": 0, "lines": {}}
    new_secs, k = [], 0
    for name, shots in secs:
        if name is None:
            name = p.sections[0].name if p.sections else "Shots"
        sec = Section(name)
        for ps in shots:
            s = match.get(k)
            # a "> [chips]" line identical to the shot's own settings line is display only;
            # any other "> ..." text the user typed is kept as part of the note
            own = " ".join(f"[{x}]" for x in tags(s)) if s else None
            extra_lines = [t for t in ps.tag_lines if t != own]
            stats["tag_notes"] += len(extra_lines)
            note = clean_note("\n".join(ps.note_lines + extra_lines))
            if s is None:
                s = Shot(ps.key, ps.t_in, ps.t_out, note)
                stats["added"] += 1
            else:
                s = s.clone()
                if abs(s.t_in - ps.t_in) >= TOL or abs(s.t_out - ps.t_out) >= TOL or s.note != note:
                    stats["changed"] += 1
                if abs(s.t_in - ps.t_in) >= TOL:
                    s.t_in = ps.t_in
                if abs(s.t_out - ps.t_out) >= TOL:
                    s.t_out = ps.t_out
                s.note = note
            stats["lines"][s.uid] = (ps.line, ps.end)
            sec.shots.append(s)
            k += 1
        new_secs.append(sec)
    if title:
        p.title = title
    p.sections = new_secs
    return stats
