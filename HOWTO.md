# Shot Picker — how to

**Start it:** double-click **Shot Picker** on the desktop (or `ShotPicker.bat` in this folder).
The first time, it opens the Korea 2023 edit (v3, 296 shots). After that it reopens whatever project you used last.

## The window

| Left | Middle | Right |
|---|---|---|
| Clips in the footage folder, sorted by name. `#27` is the clip number; `●3` means it has 3 shots. Type in *Filter clips…* to search. | The player (mpv, hardware decode). The bar under the picture shows every shot in this clip as a coloured block with its shot number. | The shot list as Markdown. You can type in it directly. |

You can drag the panel edges. **View ▸ Shot list on the left / right** swaps its side.

## Marking shots
1. Pick a clip, play it, press **I** at the start of the shot you want and **O** at the end. That adds the shot. Do it as many times as you like in one clip.
2. A new shot goes next to the clip's other shots, or otherwise into clip-name order, under the section it lands in.
3. To fix a shot: click its block on the bar, move to the right frame and press **Shift+I** or **Shift+O**. You can also drag the edge of the block.

## Shot list panel (right)
Each shot looks like this:
```
### 47 · 7:12.3                                   ← number + time in the video (filled in for you)
Son on the swing — keep his laugh, slow-mo end     ← your note: type anything
`#112 · DJI_20230427130102_0112_D · 0:12.0–0:16.5 · 4.5s`   ← clip and in–out
> [Sound up] [fade in]                             ← settings from the Korea pipeline (read-only)
```
- **To add a note**, type plain text on a new line anywhere inside the shot, above or below the clip line. For example: `Title card here: Gangchon Rail Park`. Lines starting with `>` are fine too; they're kept as notes. The coloured `> [Sound up]` line is the one exception: it shows settings and can't be changed, so a changed copy of it is kept as a note instead.
- **Edit notes and headings** (`## Day 2 - Nami Island`) as you like. Changes save about a second after you stop typing, and the numbers refresh when you click away.
- You can also edit the in–out times on the clip line, cut and paste a whole block to move a shot, delete a block to delete the shot, or type `## New heading` to start a new section.
- If something can't be read (such as a broken clip line), a red message under the panel says what's wrong, and nothing changes until you fix it.
- Click a shot in the panel to jump the player to it.
- To reorder without typing, use **Alt+↑ / Alt+↓** or the ↑ ↓ buttons. A shot moves across a heading into the next section.

## Rotating a clip
Phones lying flat on a table often save the wrong orientation (sideways or upside down). Press **R** to turn the clip on screen 90° clockwise, or **Shift+R** to turn it anticlockwise. Edit ▸ Rotate also has 180° and Reset.
- The rotation belongs to the **whole clip**, so all its shots turn together. The file list shows `↻90°` next to a rotated clip, and the clip label shows it too.
- It's saved in the project file, is undoable (Ctrl+Z), and is used by shot-list playback, the frame guide, Export video and Claude's renders.
- The footage files themselves are never changed.

## Stabilizing shots
For handheld phone footage with no OIS. Press **S** on a selected shot to mark it **Steady**, and press S again to unmark it. **Edit ▸ Stabilize all shots / no shots** does the whole list at once.
- The shot list shows `[Steady]` under a marked shot. The mark is saved in the project file and is undoable.
- It's applied when you export (and in Claude's renders), **not** in the player. To see it, press **Shift+S** (Edit ▸ Preview stabilized shot). After a few seconds a side-by-side clip (original | stabilized) opens in your video player.
- How it works: ffmpeg's vid.stab tracks the camera shake over just that shot, then steadies it inside a fixed 4% zoom-in that hides the moving edges. Deliberate pans stay, but the jitter goes.
- If a really shaky shot still shows edges, give it more room: change `"stab": true` to `"stab": 8` (8% zoom) in that shot's `extra` in the JSON.
- Don't mark screen recordings or tripod shots: there's nothing to steady, and you'd only lose 4% of the picture.
- The motion data is cached in a `<project>.stab` folder next to the project file. It's safe to delete; it gets remade.

## Frame guide (what gets cropped)
Use the **Frame guide** dropdown next to the player buttons, or press **G** to cycle through the options.
- **16:9 landscape (1080p):** your 4:3 clips lose a strip at the top and bottom, and the guide greys those strips out. For Korea shots that already have a crop position set, the guide shows that exact position.
- **9:16 vertical (Shorts / TikTok):** keeps a tall strip in the middle and greys out both sides.
- **Both:** the dark grey is cut from both formats; the light grey is cut only from the vertical one.
- A 1920×1080 clip already fills 16:9, so it's labelled *(whole picture)*.

## Checking the cut
**F5** (or *▶▶ Play shot list*) plays every shot back to back in list order, from the selected shot. **Shift+F5** starts from the beginning. The bar then shows the whole video, with day markers; click it to jump. **[ / ]** skips a shot and **Esc** stops. Slow-mo and fast shots play at their set speed.

## Hotkeys
| Key | Does |
|---|---|
| Space | Play / pause |
| I | Mark in |
| O | Mark out → adds the shot |
| Shift+I / Shift+O | Move the selected shot's in / out to here |
| `,` / `.`  (or Ctrl+← / Ctrl+→) | One frame back / forward |
| ← / → | 1 s back / forward (Shift: 5 s) |
| Home / End | Start / end of clip |
| [ / ] | Previous / next shot (in list order) |
| PgUp / PgDn | Previous / next clip |
| P | Play the selected shot once |
| F5 / Shift+F5 | Play shot list from selected / from start |
| Esc | Stop list playback · cancel a pending In · leave the text panel |
| Alt+↑ / Alt+↓ | Move selected shot up / down |
| Delete | Delete selected shot |
| F2 or Enter | Type a note for the selected shot |
| Ctrl+Z / Ctrl+Y | Undo / redo (while the text panel has its own undo) |
| M | Mute |
| G | Frame guide: off → 16:9 → 9:16 → both |
| R / Shift+R | Rotate the clip 90° clockwise / anticlockwise |
| S / Shift+S | Stabilize the shot on / off · preview it stabilized |
| Ctrl+E | Export video |
| F1 | This list |

## Audio lanes (under the player)
Three rows on one time scale for the whole cut:
- **Game**: one block per shot, its own sound. Grey = muted, otherwise its level in dB. Right-click a block (or **Audio** menu) to set the volume or mute it. **Audio ▸ Game sound: mute all / unmute all** for the lot.
- **Voice**: the voice-over lines. Drag the **right edge** to lengthen or shorten a line: the shot under its end grows or shrinks to match and everything after slides along. Drag the **left edge** to let in more or less of the take before the line. Drag the middle to move a line. **Ctrl+K** splits the selected clip at the playhead, **Delete** removes it.
- **Music**: backing tracks. **Audio ▸ Add music…**, then right-click ▸ **Level / fades…** for volume, fade in and fade out (drawn as ramps).

Wheel scrolls, **Ctrl+wheel** zooms, double-click opens a clip in the player, clicking empty space plays the shot list from there. **F5** plays the cut with voice and music (they are mixed in the background after each change; "mixing audio…" shows next to the save indicator).

Deleting or trimming a shot slides the voice and music after it too. Edits typed in the Markdown panel don't slide them.

## Enhance voice (ClearerVoice)
One-time: run **setup_voice.bat** (needs internet; about 3 GB for PyTorch, the models come from HuggingFace on first use). Then **Audio ▸ Enhance voice…**:
- **Clean + brighten** = MossFormer2_SE_48K then MossFormer2_SR_48K (noise and room echo out, phone top end rebuilt).
- **Clean only** = MossFormer2_SE_48K.

Each take gets a cleaned copy next to it (`<take>.clear-sesr.wav`), and the voice clips switch to it with the same cut points. **Audio ▸ Voice: back to the original takes** switches back. Ctrl+Z works too.

## Export for FilmCraft / other editors
**File ▸ Export timeline for FilmCraft (XML, FCPXML, OTIO)…** (Ctrl+Shift+E) writes three files side by side: `.xml` (FCP 7), `.fcpxml` (1.9) and `.otio`. Shots on V1, their game sound on A1 at its level (muted shots left out), voice and music on their own tracks with fades, one marker per section. Try each in FilmCraft to see which survives a save and reload.

## Export video (when you're done picking)
The exported video now has each shot's game-sound level or mute, with the voice and music lanes mixed in.
**File ▸ Export video…** (Ctrl+E) or the **⇩ Export video** button.
- **Format:** 16:9 landscape 1920×1080 (YouTube) or 9:16 vertical 1080×1920 (Shorts / TikTok). The crop matches the frame guide.
- **Frame rate:** *Auto* matches your footage (29.97 for the DJI clips, 30 for screen recordings), or choose 30 / 60.
- **Save as:** defaults to the project's folder; Korea goes to `korea-trip-2023\output`. A new file is made each time, named with the date.
- It's a straight cut: every shot in list order, with sound. Slow-mo and sped-up shots from the Korea edit keep their speed, rotated clips stay rotated, and Steady shots are stabilized.
- It does **not** add your notes, title cards, music or fades. For a finished video with those, ask Claude to render from the project file.
- A progress bar shows each shot. **Cancel** stops it and deletes the half-made files. Roughly: a 20-minute cut of 4K DJI footage takes several minutes.

## Saving
Everything **autosaves** within half a second. Undo history is kept while the app is open.
- Korea: `korea-trip-2023\analysis\shot_picker.shotpicker.json`, plus a readable `shot_picker.md` next to it.
- New projects (**File ▸ New project from footage folder…**, for example the UK trip) are saved to `shot-picker\projects\` by default. Nothing is ever written into the footage folder.

## For the Korea render pipeline (Claude)
```
py -3.12 scripts\from_shot_picker.py analysis
set EDL=edl_picker.json
py -3.12 scripts\render.py <footage> analysis build output\<name>.mp4
```
`from_shot_picker.py` writes `analysis/edl_picker.json` in the same shape as `edl_refined.json`. Imported shots keep speed, audio, cards, fades and so on. New shots get the defaults plus `exact: true`, and each shot carries the user's `note`. With no edits, its output matches edl_refined.json exactly, so already-rendered shots are reused from the cache.

**File ▸ Re-import Korea v3 edit** starts over from `edl_refined.json` and keeps a `.bak` of the old file.

## Setup notes
- On a new PC, run `setup.bat` once. It creates `.venv`, installs the packages and downloads `lib\libmpv-2.dll` (the shinchiro mpv build from 12 Oct 2026, linked from mpv.io).
- Command line for scripts and agents: `python -m shotpicker info | md | export | edl <project>`. See README.md.
