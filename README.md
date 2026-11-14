# Shot Picker

A small Windows video editor for travel vlogs and channel videos. You pick shots out of a folder of raw footage, put them in order, write notes, and export a straight-cut draft. It's built with PySide6 (Qt), libmpv for frame-accurate, hardware-decoded playback, and ffmpeg for export.

- **Left:** the clips in the footage folder. **Middle:** the player, with every marked shot shown on the playbar. **Right:** a live, editable Markdown shot list.
- Mark shots with **I / O**, reorder them, write notes, and play the whole list back with **F5**.
- There's a frame guide for 16:9 and 9:16 crops, rotation, stabilization, voice and music lanes, and voice enhancement.
- Export a video (16:9 1080p or 9:16 vertical), or an edit for Premiere, Final Cut Pro or FilmCraft (`.xml` / `.fcpxml` / `.otio`).

User guide and hotkeys: **[HOWTO.md](HOWTO.md)**.

## Setup (Windows 10/11)
Needs Python 3.12 (via the `py` launcher) and **ffmpeg / ffprobe on PATH**.
```bat
setup.bat          :: creates .venv, installs requirements, downloads libmpv into lib\
ShotPicker.bat     :: starts the app (or: .venv\Scripts\pythonw.exe -m shotpicker [project.shotpicker.json])
setup_voice.bat    :: optional, ~3 GB: PyTorch + ClearerVoice for Audio > Enhance voice
```
`lib\libmpv-2.dll` (~116 MB) is too big for GitHub, so `setup.bat` downloads it.

**Clone to a short path**, such as `C:\Sandbox\workspace\shot-picker`. PySide6 has very long internal file paths, so setup fails in deep folders like `%TEMP%\...` unless Windows long-path support is turned on.

## Command line (no window, no libmpv needed)
```bat
.venv\Scripts\python -m shotpicker info   project.shotpicker.json                 :: check clips exist + print the shot list (exit 1 if clips missing)
.venv\Scripts\python -m shotpicker md     project.shotpicker.json                 :: rewrite the readable .md next to it
.venv\Scripts\python -m shotpicker export project.shotpicker.json --format 16:9   :: or 9:16; --fps auto|30|60; --out draft.mp4
.venv\Scripts\python -m shotpicker edl    project.shotpicker.json                 :: .xml + .fcpxml + .otio for Premiere / FCP / FilmCraft
```

## For agents: making a draft for the user
The user hands footage to a Claude session or another agent and wants back a **draft they can open in the app, review and fix**. The workflow:

1. **Look at the footage, read-only.** Never write, rename or delete anything in the footage folder. `ffprobe` gives you durations. Contact sheets or transcripts are fine, but keep them in your own working folder.
2. **Write a project file** at `projects\<name>.shotpicker.json`. Use a new name; never overwrite one of the user's existing project files unless they ask. A minimal valid file:
   ```json
   {
     "format": "shot-picker/1",
     "title": "UK trip 2026",
     "footage_dir": "E:\\UK 2026",
     "sections": [
       {"name": "Day 1 - London", "shots": [
         {"file": "DJI_0001.MP4", "in": 12.0, "out": 16.5, "note": "Son waving at Big Ben"},
         {"file": "DJI_0004.MP4", "in": 3.2,  "out": 7.0,  "note": ""}
       ]}
     ]
   }
   ```
   - `file` is a filename inside `footage_dir`, or an absolute path. `in` and `out` are seconds inside the clip. Shots play top to bottom.
   - `note` is free text for the user or a later renderer. The app's own export ignores it.
   - Optional per-shot `extra` fields: `"mute": true`, `"vol_db": -6`, `"speed": 0.5` (slow-mo), `"y": 0.3` (16:9 crop height for 4:3 footage; 0 = top), `"stab": true`. Any unknown keys you add round-trip untouched.
   - Optional top-level fields: `"rotate": {"file.mp4": 90}`, plus `"voice"` / `"music"` lanes. The full format is in the [`shotpicker/model.py`](shotpicker/model.py) docstring.
3. **Check it:** `python -m shotpicker info <file>` must exit 0.
4. **Render a preview:** `python -m shotpicker export <file> --format 16:9 --out <your folder>\draft.mp4`. That's a straight cut, with no titles, notes or music.
5. **Tell the user** the project path. They open it with `ShotPicker.bat <file>` (or File ▸ Open project) and carry on editing. Every edit autosaves to the same JSON.

When the user has edited a project, the JSON is the source of truth. Re-read it before rendering; never re-type their choices from memory. A project's own render scripts (outside this repo) may give notes extra meaning, such as `caption:` lines, so check that project's docs before assuming.

## Code map
| File | What it does |
|---|---|
| `shotpicker/app.py` | Main window: files, player, playbar, Markdown panel, menus, hotkeys |
| `shotpicker/model.py` | Project / Section / Shot data, and JSON load/save (the format is documented here) |
| `shotpicker/mdsync.py` | Markdown view ⇄ project (strict, parseable format) |
| `shotpicker/export.py` | ffmpeg export: per-shot render (crop, speed, stabilize, audio), then join |
| `shotpicker/interchange.py` | FCP7 XML / FCPXML / OTIO export |
| `shotpicker/audio.py`, `lanes.py` | Voice and music lanes: mixing and the lane widget |
| `shotpicker/stabilize.py` | vid.stab analysis and caching |
| `shotpicker/enhance.py`, `voice_enhance.py` | ClearerVoice voice enhancement (runs in `.venv-voice`) |
| `shotpicker/guide.py` | 16:9 / 9:16 frame-guide overlay |
| `shotpicker/importers.py` | Import an `edl_refined.json`-style edit list (Korea 2023 pipeline) |
| `shotpicker/cli.py` | The command line above |

Personal projects (`projects/`), renders, the venvs and libmpv are git-ignored and stay on the user's PC.
