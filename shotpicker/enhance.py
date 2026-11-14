"""Voice clean-up for the voice lane, using ClearerVoice in its own Python (see setup_voice.bat).

Shot Picker's venv stays small: the enhancement runs voice_enhance.py with the separate voice venv
(.venv-voice next to ShotPicker.bat), one take at a time. Each take gets a cleaned copy next to it,
<take>.clear-se.wav or <take>.clear-sesr.wav, and the voice clips are pointed at the copy; the clip
remembers the original in AudioClip.raw so it can be switched back.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # folder with ShotPicker.bat
VENV_PY = ROOT / ".venv-voice" / "Scripts" / "python.exe"
SCRIPT = Path(__file__).resolve().parent / "voice_enhance.py"
MODELS_DIR = ROOT / "voice-models"                     # ClearVoice puts checkpoints/ in here
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MODELS = {
    "sesr": "Clean + brighten (noise and echo out, phone top end rebuilt)",
    "se": "Clean only (noise and echo out)",
}


def ready() -> bool:
    return VENV_PY.exists()


def out_path(raw: str, model: str) -> str:
    p = Path(raw)
    return str(p.with_name(f"{p.stem}.clear-{model}.wav"))


def enhance(raw: str, model: str, on_line=None, cancelled=lambda: False) -> str:
    """Make (or reuse) the cleaned copy of one take. Returns its path; raises on failure."""
    dst = out_path(raw, model)
    if Path(dst).exists() and Path(dst).stat().st_mtime >= Path(raw).stat().st_mtime:
        return dst
    MODELS_DIR.mkdir(exist_ok=True)
    proc = subprocess.Popen([str(VENV_PY), "-u", str(SCRIPT), "--model", model, raw, dst], cwd=str(MODELS_DIR),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                            creationflags=NO_WINDOW)
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-25:]
        if on_line:
            on_line(line)
        if cancelled():
            proc.kill()
            raise RuntimeError("Cancelled")
    if proc.wait() != 0 or not Path(dst).exists():
        raise RuntimeError("Voice clean-up failed:\n" + "\n".join(tail))
    return dst
