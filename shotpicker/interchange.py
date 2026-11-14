"""Export the cut as editor timelines: FCP 7 XML (.xml), FCPXML 1.9 (.fcpxml) and OpenTimelineIO (.otio).

Tracks: V1 the shots in list order; A1 each shot's own sound at its level (muted shots left out);
then the voice lane and the music lane (a lane whose clips overlap is spread over extra tracks).
Gain and fade in/out carry over; section headings become markers. Time is 30 fps. Crops, rotation,
stabilizing and speed changes are not written (the editor keeps the clip as it is on disk).
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape as E, quoteattr as Q

from . import audio
from .model import AudioClip, Project, shot_gain

FPS = 30


def fr(sec: float) -> int:
    return int(round(sec * FPS))


def url(path: str, localhost=False) -> str:
    p = path.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return ("file://localhost" if localhost else "file://") + urllib.parse.quote(p, safe="/:")


def pack(clips: list[AudioClip]) -> list[list[AudioClip]]:
    """Spread clips over as few tracks as possible so none overlap within a track."""
    tracks: list[list[AudioClip]] = []
    for c in sorted(clips, key=lambda c: c.start):
        for t in tracks:
            if fr(t[-1].end) <= fr(c.start):
                t.append(c)
                break
        else:
            tracks.append([c])
    return tracks


class Cut:
    """Everything the writers need, with source durations probed once."""

    def __init__(self, p: Project):
        self.p = p
        self.shots = []          # (n, shot, abspath, start, dur, section_name_or_None)
        last = None
        for n, ((s, t), (_, sec)) in enumerate(zip(p.timeline(), p.flat()), 1):
            self.shots.append((n, s, p.abspath(s.file), t, s.dur, sec.name if sec.name != last else None))
            last = sec.name
        self.total = p.total()
        self.voice = pack(p.voice)
        self.music = pack(p.music)
        self.end = max([self.total] + [c.end for _, c in p.audio_clips()])
        self.durs = {}
        for path in {x[2] for x in self.shots} | {c.file for _, c in p.audio_clips()}:
            self.durs[path] = audio.duration(path) or 3600.0

    def markers(self):
        return [(name, start) for _, _, _, start, _, name in self.shots if name]


# ---- FCP 7 XML -----------------------------------------------------------------------------------
def _rate():
    return f"<rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>"


def _levels(db: float, fade_in=0.0, fade_out=0.0, dur=0.0) -> str:
    v = audio.lin(db)
    keys = ""
    if fade_in > 0 or fade_out > 0:
        pts = []
        if fade_in > 0:
            pts += [(0, 0.0), (fr(fade_in), v)]
        else:
            pts += [(0, v)]
        if fade_out > 0:
            pts += [(max(fr(dur - fade_out), 1), v), (fr(dur), 0.0)]
        keys = "".join(f"<keyframe><when>{w}</when><value>{x:.5f}</value></keyframe>" for w, x in pts)
    return ("<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid>"
            "<effectcategory>audiolevels</effectcategory><effecttype>audiolevel</effecttype>"
            "<mediatype>audio</mediatype><parameter><parameterid>level</parameterid><name>Level</name>"
            f"<valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{v:.5f}</value>{keys}</parameter>"
            "</effect></filter>")


def write_xmeml(cut: Cut, out: Path, title: str):
    files, seen, cid = {}, set(), [0]

    def filexml(path, video):
        fid = files.setdefault(path, f"file-{len(files) + 1}")
        if fid in seen:
            return f'<file id="{fid}"/>'
        seen.add(fid)
        med = ""
        if video:
            med += (f"<video><samplecharacteristics>{_rate()}<width>1920</width><height>1080</height>"
                    "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>")
        med += ("<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>"
                "</samplecharacteristics><channelcount>2</channelcount></audio>")
        return (f'<file id="{fid}"><name>{E(Path(path).name)}</name><pathurl>{url(path, True)}</pathurl>{_rate()}'
                f"<duration>{fr(cut.durs[path])}</duration><media>{med}</media></file>")

    def item(path, name, start, dur, tin, kind, video, level=""):
        cid[0] += 1
        st, en = fr(start), fr(start + dur)
        i0 = fr(tin)
        x = (f'<clipitem id="clipitem-{cid[0]}"><name>{E(name)}</name><enabled>TRUE</enabled>'
             f"<duration>{fr(cut.durs[path])}</duration>{_rate()}<start>{st}</start><end>{en}</end>"
             f"<in>{i0}</in><out>{i0 + en - st}</out>{filexml(path, video)}")
        if kind == "audio":
            x += "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>"
        return x + level + "</clipitem>"

    def track(items):
        return f"<track>{''.join(items)}<enabled>TRUE</enabled><locked>FALSE</locked></track>"

    video_exts = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
    V, A1 = [], []
    for n, s, path, start, dur, _ in cut.shots:
        name = f"{n:02d} {s.note.splitlines()[0] if s.note else Path(path).stem}"
        V.append(item(path, name, start, dur, s.t_in, "video", True))
        g = shot_gain(s)
        if g is not None:
            A1.append(item(path, name, start, dur, s.t_in, "audio", True, _levels(g)))
    atracks = [track(A1)]
    for lane, tracks in (("Voice", cut.voice), ("Music", cut.music)):
        for t in tracks:
            atracks.append(track([item(c.file, f"{lane} {c.note or Path(c.file).stem}", c.start, c.dur, c.t_in,
                                       "audio", Path(c.file).suffix.lower() in video_exts,
                                       _levels(c.gain_db, c.fade_in, c.fade_out, c.dur)) for c in t]))
    mk = "".join(f"<marker><name>{E(n)}</name><comment></comment><in>{fr(a)}</in><out>-1</out></marker>"
                 for n, a in cut.markers())
    fmt = (f"<samplecharacteristics>{_rate()}<width>1920</width><height>1080</height>"
           "<pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance></samplecharacteristics>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
           f'<sequence id="sequence-1"><name>{E(title)}</name><duration>{fr(cut.end)}</duration>{_rate()}'
           f"<timecode>{_rate()}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
           f"<media><video><format>{fmt}</format>{track(V)}</video>"
           "<audio><numOutputChannels>2</numOutputChannels><format><samplecharacteristics><depth>16</depth>"
           f"<samplerate>48000</samplerate></samplecharacteristics></format>{''.join(atracks)}</audio></media>"
           f"{mk}</sequence></xmeml>\n")
    Path(out).write_text(xml, encoding="utf-8")


# ---- FCPXML 1.9 ----------------------------------------------------------------------------------
def _t(sec_or_frames, frames=False) -> str:
    f = sec_or_frames if frames else fr(sec_or_frames)
    return f"{f}/{FPS}s" if f else "0s"


def _vol(db: float | None, fade_in=0.0, fade_out=0.0) -> str:
    amount = "-96dB" if db is None else f"{db:+.2f}dB".replace("+", "")
    fades = ""
    if fade_in > 0:
        fades += f'<fadeIn type="easeIn" duration="{_t(fade_in)}"/>'
    if fade_out > 0:
        fades += f'<fadeOut type="easeOut" duration="{_t(fade_out)}"/>'
    if fades:
        return f'<adjust-volume amount="{amount}"><param name="amount">{fades}</param></adjust-volume>'
    return f'<adjust-volume amount="{amount}"/>'


def write_fcpxml(cut: Cut, out: Path, title: str):
    assets, res = {}, []

    def asset(path, video):
        if path not in assets:
            aid = f"r{len(assets) + 2}"
            assets[path] = aid
            vid = ' hasVideo="1" format="r1"' if video else ""
            res.append(f'<asset id="{aid}" name={Q(Path(path).name)} start="0s" duration="{_t(cut.durs[path])}"'
                       f'{vid} hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">'
                       f'<media-rep kind="original-media" src={Q(url(path))}/></asset>')
        return assets[path]

    # connected audio clips hang off the spine shot under their start
    hang: dict[int, list[str]] = {}
    lanes = [(-1 - i, c, "Voice") for i, t in enumerate(cut.voice) for c in t]
    lanes += [(-1 - len(cut.voice) - i, c, "Music") for i, t in enumerate(cut.music) for c in t]
    for lane, c, kind in lanes:
        k = next((i for i, x in enumerate(cut.shots) if x[3] <= c.start < x[3] + x[4]), len(cut.shots) - 1)
        n, s, path, start, dur, _ = cut.shots[k]
        off = fr(s.t_in) + fr(c.start) - fr(start)       # in the parent clip's own time
        video = Path(c.file).suffix.lower() in {".mp4", ".mov", ".mkv", ".m4v"}
        aid = asset(c.file, video)
        role = "dialogue" if kind == "Voice" else "music"
        hang.setdefault(k, []).append(
            f'<asset-clip ref="{aid}" lane="{lane}" name={Q(f"{kind} {c.note or Path(c.file).stem}")} '
            f'offset="{_t(off, True)}" start="{_t(c.t_in)}" duration="{_t(c.dur)}" audioRole="{role}">'
            f"{_vol(c.gain_db, c.fade_in, c.fade_out)}</asset-clip>")
    spine = []
    for k, (n, s, path, start, dur, secname) in enumerate(cut.shots):
        aid = asset(path, True)
        name = f"{n:02d} {s.note.splitlines()[0] if s.note else Path(path).stem}"
        mk = f'<marker start="{_t(s.t_in)}" duration="1/{FPS}s" value={Q(secname)}/>' if secname else ""
        spine.append(f'<asset-clip ref="{aid}" name={Q(name)} offset="{_t(start)}" start="{_t(s.t_in)}" '
                     f'duration="{_t(dur)}" format="r1" tcFormat="NDF">{_vol(shot_gain(s))}{mk}'
                     f'{"".join(hang.get(k, []))}</asset-clip>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9"><resources>'
           f'<format id="r1" name="FFVideoFormat1080p30" frameDuration="1/{FPS}s" width="1920" height="1080"/>'
           f'{"".join(res)}</resources><library><event name={Q(title)}><project name={Q(title)}>'
           f'<sequence format="r1" duration="{_t(cut.total)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" '
           f'audioRate="48k"><spine>{"".join(spine)}</spine></sequence></project></event></library></fcpxml>\n')
    Path(out).write_text(xml, encoding="utf-8")


# ---- OpenTimelineIO ------------------------------------------------------------------------------
def _rt(sec, frames=None):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(FPS), "value": float(fr(sec) if frames is None else frames)}


def _tr(start, dur):
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start), "duration": _rt(dur)}


def _clip(name, path, tin, dur, cut, meta):
    return {"OTIO_SCHEMA": "Clip.1", "name": name, "source_range": _tr(tin, dur),
            "media_reference": {"OTIO_SCHEMA": "ExternalReference.1", "name": Path(path).name, "target_url": url(path),
                                "available_range": _tr(0, cut.durs[path]), "metadata": {}},
            "effects": [], "markers": [], "enabled": True, "metadata": {"shot_picker": meta}}


def _gap(dur):
    return {"OTIO_SCHEMA": "Gap.1", "name": "", "source_range": _tr(0, dur), "effects": [], "markers": [],
            "enabled": True, "metadata": {}}


def _track(name, kind, children, markers=()):
    return {"OTIO_SCHEMA": "Track.1", "name": name, "kind": kind, "children": children, "source_range": None,
            "effects": [], "markers": list(markers), "enabled": True, "metadata": {}}


def _lane_track(name, clips, cut, meta_fn):
    kids, t = [], 0
    for c in clips:
        gap = fr(c.start) - t
        if gap > 0:
            kids.append(_gap(gap / FPS))
        kids.append(_clip(f"{name} {c.note or Path(c.file).stem}", c.file, c.t_in, c.dur, cut, meta_fn(c)))
        t = fr(c.start) + fr(c.dur)
    return _track(name, "Audio", kids)


def write_otio(cut: Cut, out: Path, title: str):
    V, A1 = [], []
    for n, s, path, start, dur, _ in cut.shots:
        name = f"{n:02d} {s.note.splitlines()[0] if s.note else Path(path).stem}"
        V.append(_clip(name, path, s.t_in, dur, cut, {}))
        g = shot_gain(s)
        A1.append(_clip(name, path, s.t_in, dur, cut, {"gain_db": g}) if g is not None else _gap(dur))
    marks = [{"OTIO_SCHEMA": "Marker.2", "name": n, "marked_range": _tr(a, 0), "color": "RED", "comment": "",
              "metadata": {}} for n, a in cut.markers()]
    tracks = [_track("V1", "Video", V, marks), _track("A1 game sound", "Audio", A1)]
    meta = lambda c: {"gain_db": c.gain_db, "fade_in": c.fade_in, "fade_out": c.fade_out}  # noqa: E731
    for i, t in enumerate(cut.voice):
        tracks.append(_lane_track(f"Voice{'' if i == 0 else ' ' + str(i + 1)}", t, cut, meta))
    for i, t in enumerate(cut.music):
        tracks.append(_lane_track(f"Music{'' if i == 0 else ' ' + str(i + 1)}", t, cut, meta))
    doc = {"OTIO_SCHEMA": "Timeline.1", "name": title, "global_start_time": None, "metadata": {},
           "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "source_range": None,
                      "effects": [], "markers": [], "enabled": True, "metadata": {}}}
    Path(out).write_text(json.dumps(doc, indent=1), encoding="utf-8")


def export_all(p: Project, base: Path) -> list[Path]:
    """Write base.xml, base.fcpxml and base.otio. Returns the paths."""
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    cut = Cut(p)
    outs = [base.with_suffix(".xml"), base.with_suffix(".fcpxml"), base.with_suffix(".otio")]
    write_xmeml(cut, outs[0], p.title)
    write_fcpxml(cut, outs[1], p.title)
    write_otio(cut, outs[2], p.title)
    return outs
