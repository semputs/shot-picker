"""Shot stabilization (ffmpeg vid.stab, two passes), for handheld footage without OIS/EIS.

A shot is stabilized when its `extra` has `"stab": true` (S in the app). Pass 1 (`analyze`) runs
vidstabdetect over exactly the shot's source range, after the clip's rotation, and caches the motion
file in `cache_dir` keyed by file/in/out/rotation. Pass 2 is `filter_for`, a filter chain to put
after the rotation and before the crop: it steadies the camera path inside a small fixed zoom-in (4%, or
the shot's own number) that hides the moving borders. Both passes must read the frames the same way
(same -ss/-t and rotation), so callers use the same input options as the export.

Only stdlib, so other render scripts can import it: sys.path.insert(0, r"...\\shot-picker").
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ROTATE = {0: "", 90: "transpose=clock,", 180: "hflip,vflip,", 270: "transpose=cclock,"}
SHAKINESS = 8      # 1-10: how shaky the footage is (phone, no OIS: high)
ZOOM = 4           # % zoom-in budget: room the camera path may move in without showing borders
VERSION = 1        # bump when the analysis settings change, so cached motion files are redone


def wanted(extra: dict) -> bool:
    return bool(extra.get("stab"))


def zoom_of(extra: dict) -> float:
    """`"stab": true` uses ZOOM; a number (e.g. 8) gives a shakier shot a bigger budget."""
    v = extra.get("stab")
    return float(v) if not isinstance(v, bool) and isinstance(v, (int, float)) and v > 0 else ZOOM


def trf_name(file: str, t_in: float, t_out: float, rotate: int) -> str:
    key = f"{Path(file).name}|{t_in:.3f}|{t_out:.3f}|{rotate % 360}|{SHAKINESS}|{VERSION}"
    return f"{Path(file).stem}_{hashlib.sha1(key.encode()).hexdigest()[:10]}.trf"


def analyze(src: str, t_in: float, t_out: float, rotate: int, cache_dir: Path) -> Path:
    """Pass 1: write (or reuse) the motion file for this shot. Returns its path; raises on failure."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    trf = cache_dir / trf_name(src, t_in, t_out, rotate)
    if trf.exists() and trf.stat().st_size:
        return trf
    tmp = trf.with_suffix(".part")
    # run inside cache_dir with a bare file name: Windows drive colons would need escaping in filter args
    vf = f"{ROTATE[rotate % 360]}vidstabdetect=shakiness={SHAKINESS}:accuracy=15:stepsize=6:result={tmp.name}"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t_in:.3f}", "-t", f"{t_out - t_in:.3f}",
                        "-i", str(Path(src).resolve()), "-vf", vf, "-an", "-f", "null", "-"],
                       cwd=cache_dir, capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Stabilize analysis failed for {Path(src).name}:\n{r.stderr[-600:]}")
    tmp.replace(trf)
    return trf


def filter_for(trf: Path, zoom: float = ZOOM) -> str:
    """Pass 2 filter chain (ends with a comma, like export.ROTATE). The ffmpeg run that uses it must
    have cwd=trf.parent, since the motion file is named without its folder.
    vid.stab 1.4's L1 path optimizer gives the steadiest camera the zoom budget allows (it uses all of
    it, so the budget is fixed rather than optzoom, which zoomed 15-20% on these shots)."""
    return (f"vidstabtransform=input={Path(trf).name}:optalgo=opt:optzoom=0:zoom={zoom:g}:interpol=bicubic,"
            "unsharp=5:5:0.5:3:3:0.25,")


def cache_dir_for(project_path) -> Path:
    """Motion files live next to the project: <name>.stab/"""
    p = Path(project_path)
    return p.with_name(p.name.replace(".shotpicker.json", "") + ".stab")
