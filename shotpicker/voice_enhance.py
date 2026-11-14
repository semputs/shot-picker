"""Clean up a voice recording with ClearerVoice (ClearVoice). Run by the voice venv, not by Shot Picker.

    python voice_enhance.py --model se|sesr INPUT OUTPUT.wav

se    MossFormer2_SE_48K: removes noise and room echo
sesr  SE, then MossFormer2_SR_48K: also rebuilds the high frequencies a phone mic cuts off

The output is 48 kHz mono, exactly as long as the input, so cut points in the take stay valid.
Models download from HuggingFace into ./checkpoints (the working directory) on first use.
Prints "STEP n/m text" lines for the app's progress bar.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf


def step(n, m, text):
    print(f"STEP {n}/{m} {text}", flush=True)


def run_model(task, name, src, dst):
    from clearvoice import ClearVoice
    cv = ClearVoice(task=task, model_names=[name])
    out = cv(input_path=src, online_write=False)
    cv.write(out, output_path=dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=("se", "sesr"), default="sesr")
    ap.add_argument("input")
    ap.add_argument("output")
    a = ap.parse_args()
    m = 3 if a.model == "sesr" else 2
    work = tempfile.mkdtemp(prefix="sp_voice_")
    try:
        step(1, m + 1, "Preparing audio")
        wav48 = os.path.join(work, "in48.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.input, "-ac", "1", "-ar", "48000",
                        "-c:a", "pcm_s16le", wav48], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        n_in = sf.info(wav48).frames
        step(2, m + 1, "Removing noise and echo (MossFormer2_SE_48K)")
        se = os.path.join(work, "se.wav")
        run_model("speech_enhancement", "MossFormer2_SE_48K", wav48, se)
        last = se
        if a.model == "sesr":
            step(3, m + 1, "Restoring high frequencies (MossFormer2_SR_48K)")
            sr = os.path.join(work, "sr.wav")
            run_model("speech_super_resolution", "MossFormer2_SR_48K", se, sr)
            last = sr
        step(m + 1, m + 1, "Saving")
        y, rate = sf.read(last, dtype="float32", always_2d=True)
        y = y.mean(axis=1)
        if rate != 48000:
            raise SystemExit(f"unexpected sample rate {rate}")
        y = np.pad(y, (0, max(0, n_in - len(y))))[:n_in]
        peak = float(np.abs(y).max() or 1.0)
        if peak > 0.99:
            y = y * (0.99 / peak)
        tmp = a.output + ".part.wav"
        sf.write(tmp, y, 48000, subtype="PCM_16")
        os.replace(tmp, a.output)
        print("DONE", a.output, flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
