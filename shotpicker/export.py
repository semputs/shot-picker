"""Export a straight cut: every shot in list order, cropped to 16:9 (1920x1080) or 9:16 (1080x1920).

Each shot is rendered to its own file with ffmpeg (same crop as the frame guide: 16:9 uses the shot's
'y' position, default middle; 9:16 is centred), then the files are joined without re-encoding.
Speed (slow-mo / fast) from imported edits is honoured, and so is each clip's rotation (applied after
ffmpeg's own handling of the file's rotation tag, like the player), and shots marked for stabilizing
(S in the app, see stabilize.py) are steadied. Each shot's game sound gets its level or mute (Audio menu), and the
voice + music lanes (audio.py) are mixed under the joined shots. Notes, cards and fades are not applied.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import audio, stabilize
from .model import Project, Shot, shot_gain

FORMATS = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
ROTATE = stabilize.ROTATE
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe(path: str) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate",
                        "-of", "json", path], capture_output=True, text=True, creationflags=NO_WINDOW)
    streams = json.loads(r.stdout or "{}").get("streams", [])
    fps = 0.0
    for s in streams:
        if s.get("codec_type") == "video":
            a, b = (s.get("r_frame_rate") or "0/1").split("/")
            fps = float(a) / float(b or 1)
            break
    return {"audio": any(s.get("codec_type") == "audio" for s in streams), "fps": fps}


def auto_fps(infos: list[dict]) -> str:
    """29.97-family footage -> 30000/1001, otherwise 30."""
    ntsc = sum(1 for i in infos if abs(i["fps"] - 30000 / 1001) < 0.01 or abs(i["fps"] - 60000 / 1001) < 0.01)
    return "30000/1001" if ntsc * 2 >= len(infos) else "30"


def _atempo(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def shot_cmd(src: str, s: Shot, fmt: str, fps: str, has_audio: bool, dst: Path, hwdec=True, rotate=0,
             stab: str = "") -> list[str]:
    """stab: stabilize.filter_for(...) chain; run the command with cwd = the motion file's folder."""
    W, H = FORMATS[fmt]
    speed = s.speed
    y = float(s.extra.get("y", 0.5)) if fmt == "16:9" else 0.5
    a = "16/9" if fmt == "16:9" else "9/16"
    crop = (f"crop=w='trunc(min(iw,ih*{a})/2)*2':h='trunc(min(ih,iw/({a}))/2)*2':"
            f"x='(iw-ow)/2':y='(ih-oh)*{y:.4f}'")
    out_len = s.src_len / speed
    v = f"[0:v]{ROTATE[rotate % 360]}{stab}{crop},scale={W}:{H}:flags=lanczos,setsar=1,setpts=(PTS-STARTPTS)/{speed},fps={fps},format=yuv420p[v]"
    g = shot_gain(s)
    if has_audio and g is not None:
        vol = f",volume={g:.2f}dB" if g else ""
        af = f"[0:a]asetpts=PTS-STARTPTS,{_atempo(speed)},aresample=48000,aformat=channel_layouts=stereo{vol},apad[a]"
    else:
        af = "anullsrc=r=48000:cl=stereo[a]"
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if hwdec:
        cmd += ["-hwaccel", "d3d11va"]
    cmd += ["-ss", f"{s.t_in:.3f}", "-t", f"{s.src_len:.3f}", "-i", src,
            "-filter_complex", f"{v};{af}", "-map", "[v]", "-map", "[a]", "-t", f"{out_len:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", fps,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dst)]
    return cmd


def export(p: Project, fmt: str, out: Path, fps: str = "auto", progress=None, cancelled=lambda: False):
    """Render the cut. progress(done, total, message). Returns the output path; raises on failure."""
    shots = p.all_shots()
    if not shots:
        raise RuntimeError("No shots to export.")
    out = Path(out)
    work = out.with_name(out.stem + "_parts")
    work.mkdir(parents=True, exist_ok=True)
    try:
        return _export(p, shots, fmt, out, work, fps, progress, cancelled)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)  # cancelled or failed: leave nothing half-made behind
        if out.exists() and out.stat().st_size == 0:
            out.unlink()
        raise


def _export(p, shots, fmt, out, work, fps, progress, cancelled):
    infos = {}
    files = sorted({s.file for s in shots})
    for i, k in enumerate(files):
        if cancelled():
            raise RuntimeError("Cancelled")
        if progress and i % 10 == 0:
            progress(0, len(shots), f"Checking clips… {i}/{len(files)}")
        path = p.abspath(k)
        if not Path(path).exists():
            raise RuntimeError(f"Missing clip: {path}")
        infos[k] = probe(path)
    if fps == "auto":
        fps = auto_fps(list(infos.values()))
    parts = []
    stab_dir = stabilize.cache_dir_for(p.path) if p.path else work / "stab"
    for n, s in enumerate(shots, 1):
        if cancelled():
            raise RuntimeError("Cancelled")
        if progress:
            progress(n - 1, len(shots), f"Shot {n}/{len(shots)} · {Path(s.file).name}")
        dst = (work / f"{n:04d}.mp4").resolve()
        src = str(Path(p.abspath(s.file)).resolve())
        rot = p.rotation(s.file)
        stab, cwd = "", None
        if stabilize.wanted(s.extra):
            if progress:
                progress(n - 1, len(shots), f"Shot {n}/{len(shots)} · steadying {Path(s.file).name}")
            trf = stabilize.analyze(src, s.t_in, s.t_out, rot, stab_dir)
            stab, cwd = stabilize.filter_for(trf, stabilize.zoom_of(s.extra)), trf.parent
        r = subprocess.run(shot_cmd(src, s, fmt, fps, infos[s.file]["audio"], dst, rotate=rot, stab=stab),
                           cwd=cwd, capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode:  # retry with software decoding
            r = subprocess.run(shot_cmd(src, s, fmt, fps, infos[s.file]["audio"], dst, hwdec=False, rotate=rot,
                                        stab=stab), cwd=cwd, capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode:
            raise RuntimeError(f"Shot {n} ({Path(s.file).name}) failed:\n{r.stderr[-600:]}")
        parts.append(dst)
    if progress:
        progress(len(shots), len(shots), "Joining shots…")
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{x.resolve().as_posix()}'\n" for x in parts), encoding="utf-8")
    has_lanes = bool(p.voice or p.music)
    joined = work / "joined.mp4" if has_lanes else out
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-movflags", "+faststart", str(joined)],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError(f"Joining failed:\n{r.stderr[-600:]}")
    if has_lanes:
        if cancelled():
            raise RuntimeError("Cancelled")
        if progress:
            progress(len(shots), len(shots), "Mixing voice and music…")
        mix = audio.build_mix(p, work / "lanes.wav", total=p.total())
        for amix in ("amix=inputs=2:duration=first:dropout_transition=0:normalize=0",
                     "amix=inputs=2:duration=first:dropout_transition=0,volume=2"):  # older ffmpeg
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(joined), "-i", str(mix), "-filter_complex",
                                f"[0:a][1:a]{amix}[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
                               capture_output=True, text=True, creationflags=NO_WINDOW)
            if r.returncode == 0:
                break
        if r.returncode:
            raise RuntimeError(f"Mixing the audio lanes failed:\n{r.stderr[-600:]}")
    shutil.rmtree(work, ignore_errors=True)
    return out
