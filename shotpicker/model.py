"""Project data: sections of shots, saved as JSON (machine-readable) plus a Markdown view.

Saved file (`*.shotpicker.json`):
  {
    "format": "shot-picker/1",
    "title": "...",
    "footage_dir": "E:\\...",          # read-only source folder
    "extra_files": ["C:\\...\\x.mp4"],   # files added one by one (absolute paths)
    "clip_index": {"DJI_..MP4": 3},      # stable clip numbers (#idx)
    "rotate": {"VID_..mp4": 90},          # extra clockwise turn per clip, on top of the file's own rotation tag
    "sections": [
      {"name": "Day 1", "shots": [
        {"n": 9, "idx": 11, "file": "DJI_..MP4", "in": 36.5, "out": 40.0, "note": "...",
         "speed": 1, "dur": 3.5, "tl_start": 37.437, "extra": {...}}]}]
  }
`in`/`out` are source seconds inside the clip. `n`, `idx`, `speed`, `dur` (timeline seconds =
(out-in)/speed) and `tl_start` are written for convenience and ignored on load. `extra` holds
fields the app does not edit (speed, audio, cards, fades ...) and round-trips untouched, plus
`stab` (true, or a zoom % number), which S toggles: stabilize the shot on export (stabilize.py).
Game sound per shot lives in `extra` too: `vol_db` (dB, default 0) and `mute` (true/false).

Audio lanes (optional, absent in old projects):
  "voice": [{"file": "C:\\...\\take1.wav", "in": 12.1, "out": 15.0, "start": 10.6, "gain_db": 0,
             "fade_in": 0, "fade_out": 0, "note": "..."}],
  "music": [ same fields ]
`start` is where the clip begins on the cut timeline (seconds); `in`/`out` are seconds inside the file.
"""
from __future__ import annotations

import itertools
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = "shot-picker/1"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".webm", ".insv", ".lrv"}
DEFAULT_SECTION = "Shots"
_uid = itertools.count(1)


def tc(sec: float) -> str:
    """m:ss.s like the existing shot lists."""
    sec = max(sec, 0.0)
    out = f"{int(sec // 60)}:{sec % 60:04.1f}"  # same rounding as the pipeline's shot_list_md.py
    return out if not out.endswith(":60.0") else f"{int(sec // 60) + 1}:00.0"


def tc_ms(sec: float) -> str:
    sec = max(sec or 0.0, 0.0)
    h, r = divmod(sec, 3600)
    s = f"{int(r // 60)}:{r % 60:06.3f}"
    return f"{int(h)}:{s.zfill(9)}" if h >= 1 else s


def parse_tc(s: str) -> float:
    """'1:03.5', '0:01:03.5', '63.5' -> seconds."""
    parts = s.strip().split(":")
    v = 0.0
    for p in parts:
        v = v * 60 + float(p)
    return v


def clean_note(text: str) -> str:
    return "\n".join(ln.strip() for ln in (text or "").strip().splitlines())


@dataclass
class Shot:
    file: str            # basename inside footage_dir, or absolute path
    t_in: float
    t_out: float
    note: str = ""
    extra: dict = field(default_factory=dict)
    uid: int = field(default_factory=lambda: next(_uid))

    @property
    def speed(self) -> float:
        return float(self.extra.get("speed", 1) or 1)

    @property
    def src_len(self) -> float:
        return max(self.t_out - self.t_in, 0.0)

    @property
    def dur(self) -> float:
        return self.src_len / self.speed

    def __post_init__(self):
        self.note = clean_note(self.note)

    def clone(self) -> "Shot":
        return Shot(self.file, self.t_in, self.t_out, self.note, json.loads(json.dumps(self.extra)), self.uid)


@dataclass
class Section:
    name: str
    shots: list[Shot] = field(default_factory=list)


@dataclass
class AudioClip:
    """A piece of an audio file placed on the cut timeline (voice or music lane)."""
    file: str            # absolute path
    t_in: float
    t_out: float
    start: float         # cut-timeline seconds
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    note: str = ""
    raw: str = ""        # the original take when `file` is an enhanced copy of it ("" = file is the original)
    unmute_db: float | None = None   # level to go back to when a muted clip (gain -96 dB) is unmuted
    uid: int = field(default_factory=lambda: next(_uid))

    @property
    def dur(self) -> float:
        return max(self.t_out - self.t_in, 0.0)

    @property
    def end(self) -> float:
        return self.start + self.dur

    def to_json(self) -> dict:
        d = {"file": self.file, "in": round(self.t_in, 3), "out": round(self.t_out, 3),
                "start": round(self.start, 3), "gain_db": round(self.gain_db, 2),
                "fade_in": round(self.fade_in, 3), "fade_out": round(self.fade_out, 3), "note": self.note}
        if self.raw:
            d["raw"] = self.raw
        if self.unmute_db is not None:
            d["unmute_db"] = self.unmute_db
        return d

    @classmethod
    def from_json(cls, d: dict) -> "AudioClip":
        return cls(d["file"], float(d["in"]), float(d["out"]), float(d.get("start", 0)),
                   float(d.get("gain_db", 0) or 0), float(d.get("fade_in", 0) or 0),
                   float(d.get("fade_out", 0) or 0), d.get("note", ""), d.get("raw", ""),
                   d.get("unmute_db"))


LANES = ("voice", "music")


def shot_gain(s: "Shot") -> float | None:
    """Game-sound level of a shot in dB, or None when muted."""
    if s.extra.get("mute"):
        return None
    return float(s.extra.get("vol_db", 0) or 0)


class Project:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.title = "Untitled"
        self.footage_dir = ""
        self.extra_files: list[str] = []
        self.clip_index: dict[str, int] = {}
        self.rotate: dict[str, int] = {}
        self.sections: list[Section] = []
        self.meta: dict = {}
        self.voice: list[AudioClip] = []
        self.music: list[AudioClip] = []
        self._files_cache: list[str] | None = None

    # ---- audio lanes -------------------------------------------------------
    def lane(self, name: str) -> list[AudioClip]:
        return self.voice if name == "voice" else self.music

    def audio_clips(self) -> list[tuple[str, AudioClip]]:
        return [("voice", c) for c in self.voice] + [("music", c) for c in self.music]

    def find_audio(self, uid) -> tuple[str, AudioClip] | None:
        for lane, c in self.audio_clips():
            if c.uid == uid:
                return lane, c
        return None

    def audio_in(self, path: str) -> list[AudioClip]:
        return [c for _, c in self.audio_clips() if c.file == path]

    def sort_lanes(self):
        self.voice.sort(key=lambda c: c.start)
        self.music.sort(key=lambda c: c.start)

    def shot_span(self, uid) -> tuple[float, float] | None:
        """(start, end) of a shot on the cut timeline."""
        for s, t in self.timeline():
            if s.uid == uid:
                return t, t + s.dur
        return None

    def shot_at(self, t: float) -> Shot | None:
        last = None
        for s, a in self.timeline():
            last = s
            if a <= t < a + s.dur:
                return s
        return last

    def ripple(self, after: float, delta: float, skip_uid=None):
        """Slide every voice/music clip that starts at or after `after` by `delta` seconds."""
        if not delta:
            return
        for _, c in self.audio_clips():
            if c.uid != skip_uid and c.start >= after - 1e-6:
                c.start = max(0.0, round(c.start + delta, 3))

    def trim_audio(self, uid, which: str, new_t: float) -> float:
        """Move a voice/music clip's in or out point (seconds in its file).
        In: the clip keeps its place in time, so it grows or shrinks to the left (nothing else moves).
        Out: everything after it slides by the change, voice, music and the shot under the clip's end
        (that shot gets longer or shorter), so the picture stays in sync. Returns the applied change."""
        f = self.find_audio(uid)
        if not f:
            return 0.0
        _, c = f
        if which == "in":
            new_t = min(max(new_t, 0.0, c.t_in - c.start), c.t_out - 0.05)
            d = new_t - c.t_in
            c.t_in = round(new_t, 3)
            c.start = round(c.start + d, 3)
            return d
        new_t = max(new_t, c.t_in + 0.05)
        d = new_t - c.t_out
        if abs(d) < 1e-4:
            return 0.0
        old_end = c.end
        shot = self.shot_at(old_end - 1e-3)
        if shot is not None:
            if d < 0:
                d = max(d, -(shot.dur - 0.3))
            shot.t_out = round(shot.t_out + d * shot.speed, 3)
        c.t_out = round(c.t_out + d, 3)
        self.ripple(old_end, d, skip_uid=c.uid)
        return d

    # ---- files -------------------------------------------------------------
    def files(self, refresh=False) -> list[str]:
        """File keys sorted by name: basenames from footage_dir, then absolute extra files."""
        if self._files_cache is None or refresh:
            keys = []
            d = Path(self.footage_dir) if self.footage_dir else None
            if d and d.is_dir():
                keys = [p.name for p in d.iterdir() if p.suffix.lower() in VIDEO_EXT and p.is_file()
                        and not p.name.startswith("._")]
            keys.sort(key=str.lower)
            extra = sorted((f for f in self.extra_files if f not in keys), key=lambda f: Path(f).name.lower())
            self._files_cache = keys + extra
            self.assign_indices(self._files_cache)
        return self._files_cache

    def assign_indices(self, keys):
        """Clip numbers stay stable once given; new files get the next free numbers in name order."""
        nxt = max(self.clip_index.values(), default=0) + 1
        for k in keys:
            if k not in self.clip_index:
                self.clip_index[k] = nxt
                nxt += 1

    def abspath(self, key: str) -> str:
        return key if os.path.isabs(key) else os.path.join(self.footage_dir, key)

    def idx(self, key: str) -> int | None:
        return self.clip_index.get(key)

    def rotation(self, key: str) -> int:
        """Extra clockwise rotation for a clip (0/90/180/270), applied after the file's own rotation tag."""
        return self.rotate.get(key, 0) % 360

    def turn(self, key: str, degrees: int) -> int:
        r = (self.rotation(key) + degrees) % 360
        if r:
            self.rotate[key] = r
        else:
            self.rotate.pop(key, None)
        return r

    def key_for_stem(self, stem: str) -> str | None:
        s = stem.strip().strip("`").lower()
        cands = list(self.files()) + [sh.file for sh in self.all_shots()]
        for k in cands:
            name = Path(k).name.lower()
            if s in (name, Path(name).stem):
                return k
        return None

    # ---- shots -------------------------------------------------------------
    def all_shots(self) -> list[Shot]:
        return [s for sec in self.sections for s in sec.shots]

    def flat(self) -> list[tuple[Shot, Section]]:
        return [(s, sec) for sec in self.sections for s in sec.shots]

    def find(self, uid) -> tuple[Section, int] | None:
        for sec in self.sections:
            for i, s in enumerate(sec.shots):
                if s.uid == uid:
                    return sec, i
        return None

    def number(self, uid) -> int | None:
        for n, s in enumerate(self.all_shots(), 1):
            if s.uid == uid:
                return n
        return None

    def timeline(self) -> list[tuple[Shot, float]]:
        out, t = [], 0.0
        for s in self.all_shots():
            out.append((s, round(t, 3)))
            t += s.dur
        return out

    def total(self) -> float:
        return sum(s.dur for s in self.all_shots())

    def shots_in(self, key: str) -> list[Shot]:
        return [s for s in self.all_shots() if s.file == key]

    def add_shot(self, shot: Shot) -> Shot:
        """Insert in clip-name order: among the clip's own shots by in point if it has any,
        else after the last shot that starts at or before it."""
        same = [(sec, i, s) for sec in self.sections for i, s in enumerate(sec.shots) if s.file == shot.file]
        if same:
            later = [(sec, i) for sec, i, s in same if s.t_in > shot.t_in]
            sec, i = later[0] if later else (same[-1][0], same[-1][1] + 1)
            sec.shots.insert(i, shot)
            return shot
        k = (Path(shot.file).name.lower(), shot.t_in)
        best = None
        for sec in self.sections:
            for i, s in enumerate(sec.shots):
                if (Path(s.file).name.lower(), s.t_in) <= k:
                    best = (sec, i + 1)
        if best is None:
            if not self.sections:
                self.sections.append(Section(DEFAULT_SECTION))
            sec = next((x for x in self.sections if x.shots), self.sections[0])
            best = (sec, 0)
        best[0].shots.insert(best[1], shot)
        return shot

    def remove(self, uid):
        f = self.find(uid)
        if f:
            f[0].shots.pop(f[1])

    def move(self, uid, delta: int) -> bool:
        """Move one step; crossing a section edge moves the shot into the neighbouring section."""
        f = self.find(uid)
        if not f:
            return False
        sec, i = f
        si = self.sections.index(sec)
        j = i + delta
        if 0 <= j < len(sec.shots):
            sec.shots[i], sec.shots[j] = sec.shots[j], sec.shots[i]
            return True
        ns = si + (1 if delta > 0 else -1)
        if not 0 <= ns < len(self.sections):
            return False
        shot = sec.shots.pop(i)
        if delta > 0:
            self.sections[ns].shots.insert(0, shot)
        else:
            self.sections[ns].shots.append(shot)
        return True

    def sort_section(self, sec: Section):
        sec.shots.sort(key=lambda s: (Path(s.file).name.lower(), s.t_in))

    # ---- io ----------------------------------------------------------------
    def to_json(self) -> dict:
        secs, n, t = [], 0, 0.0
        for sec in self.sections:
            shots = []
            for s in sec.shots:
                n += 1
                shots.append({"n": n, "idx": self.idx(s.file), "file": s.file, "in": round(s.t_in, 3),
                              "out": round(s.t_out, 3), "note": s.note, "speed": s.speed,
                              "dur": round(s.dur, 3), "tl_start": round(t, 3), "extra": s.extra})
                t += s.dur
            secs.append({"name": sec.name, "shots": shots})
        return {"format": FORMAT, "title": self.title, "footage_dir": self.footage_dir,
                "extra_files": self.extra_files, "clip_index": self.clip_index, "rotate": self.rotate,
                "total": round(t, 3), "shot_count": n, "meta": self.meta, "sections": secs,
                "voice": [c.to_json() for c in self.voice], "music": [c.to_json() for c in self.music]}

    def load_json(self, d: dict):
        self.title = d.get("title", "Untitled")
        self.footage_dir = d.get("footage_dir", "")
        self.extra_files = list(d.get("extra_files", []))
        self.clip_index = dict(d.get("clip_index", {}))
        self.rotate = {k: int(v) % 360 for k, v in d.get("rotate", {}).items() if int(v) % 360}
        self.meta = dict(d.get("meta", {}))
        self.sections = [Section(s["name"], [Shot(x["file"], float(x["in"]), float(x["out"]), x.get("note", ""),
                                                  dict(x.get("extra", {}))) for x in s["shots"]])
                         for s in d.get("sections", [])]
        self.voice = [AudioClip.from_json(x) for x in d.get("voice", [])]
        self.music = [AudioClip.from_json(x) for x in d.get("music", [])]
        self.sort_lanes()
        self._files_cache = None

    @classmethod
    def open(cls, path) -> "Project":
        p = cls(path)
        p.load_json(json.loads(Path(path).read_text(encoding="utf-8")))
        return p

    def snapshot(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False)

    def restore(self, snap: str):
        uids = [s.uid for s in self.all_shots()]
        auids = {lane: [c.uid for c in self.lane(lane)] for lane in LANES}
        self.load_json(json.loads(snap))
        for s, u in zip(self.all_shots(), uids):  # keep selection stable where possible
            s.uid = u
        for lane in LANES:
            for c, u in zip(self.lane(lane), auids[lane]):
                c.uid = u

    def md_path(self) -> Path:
        return self.path.with_name(self.path.name.replace(".shotpicker.json", "").replace(".json", "") + ".md")

    def save(self):
        from .mdsync import render
        if not self.path:
            return
        _atomic_write(self.path, json.dumps(self.to_json(), indent=1, ensure_ascii=False))
        _atomic_write(self.md_path(), render(self)[0])


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


SAFE_NAME = re.compile(r"[^\w\- ]+")
