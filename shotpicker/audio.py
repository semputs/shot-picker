"""Audio lanes: probing, the voice + music mix (for playback and export), and game-sound levels.

The mix is one WAV of the whole cut (voice and music lanes only, game sound stays in the shots).
The app plays it in a second, audio-only player next to the picture during shot-list playback, and
the video export lays it under the joined shots.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .model import AudioClip, Project, Shot, shot_gain

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff", ".mp4", ".mov"}
_dur_cache: dict[str, float] = {}


def duration(path: str) -> float:
    if path not in _dur_cache:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        try:
            _dur_cache[path] = float(r.stdout.strip())
        except ValueError:
            _dur_cache[path] = 0.0
    return _dur_cache[path]


def lin(db: float | None) -> float:
    return 0.0 if db is None else 10 ** (db / 20)


def mpv_volume(s: Shot) -> float:
    """mpv volume (0-100+) for a shot's game sound."""
    return round(100 * lin(shot_gain(s)), 2)


def mix_key(p: Project) -> str:
    data = json.dumps([[c.to_json() for c in p.voice], [c.to_json() for c in p.music], round(p.total(), 3)])
    return hashlib.sha1(data.encode()).hexdigest()[:16]


def clip_filter(c: AudioClip, label: str, i: int) -> str:
    f = [f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS"]
    if c.gain_db:
        f.append(f"volume={c.gain_db:.2f}dB")
    if c.fade_in > 0:
        f.append(f"afade=t=in:st=0:d={c.fade_in:.3f}")
    if c.fade_out > 0:
        f.append(f"afade=t=out:st={max(c.dur - c.fade_out, 0):.3f}:d={c.fade_out:.3f}")
    ms = int(round(c.start * 1000))
    f.append(f"adelay={ms}|{ms}")
    return ",".join(f) + f"[{label}]"


def build_mix(p: Project, out: Path, total: float | None = None) -> Path | None:
    """Render voice + music to a stereo WAV as long as the cut. None when both lanes are empty."""
    clips = [c for _, c in p.audio_clips() if c.dur > 0.01]
    if not clips:
        return None
    total = total if total is not None else max(p.total(), max(c.end for c in clips))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for c in clips:
        cmd += ["-ss", f"{c.t_in:.3f}", "-t", f"{c.dur:.3f}", "-i", c.file]
    parts = [clip_filter(c, f"c{i}", i) for i, c in enumerate(clips)]
    ins = "".join(f"[c{i}]" for i in range(len(clips)))
    tmp = out.with_name(out.stem + ".tmp.wav")
    for amix in (f"amix=inputs={len(clips)}:duration=longest:dropout_transition=0:normalize=0",
                 f"amix=inputs={len(clips)}:duration=longest:dropout_transition=0,volume={len(clips)}"):
        graph = ";".join(parts) + f";{ins}{amix},apad,atrim=0:{total:.3f}[m]"
        r = subprocess.run(cmd + ["-filter_complex", graph, "-map", "[m]", "-ar", "48000", "-ac", "2",
                                  "-c:a", "pcm_s16le", str(tmp)],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode == 0:
            tmp.replace(out)
            return out
    raise RuntimeError(f"Audio mix failed:\n{r.stderr[-600:]}")
