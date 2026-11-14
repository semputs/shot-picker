"""Shot Picker main window: file list | mpv player + playbar + audio lanes | live Markdown shot list."""
from __future__ import annotations

import bisect
import locale
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["PATH"] = str(ROOT / "lib") + os.pathsep + os.environ["PATH"]

import mpv  # noqa: E402
from PySide6.QtCore import QEvent, QSettings, Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import (QAction, QColor, QFont, QKeySequence, QPalette, QSyntaxHighlighter,  # noqa: E402
                           QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,  # noqa: E402
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QProgressDialog,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from . import importers  # noqa: E402
from .mdsync import apply as md_apply, parse as md_parse, render as md_render  # noqa: E402
from .model import AudioClip, Project, Shot, shot_gain, tc, tc_ms  # noqa: E402
from .timeline import Timeline, colour  # noqa: E402
from .lanes import AudioLanes  # noqa: E402
from . import audio  # noqa: E402
from . import interchange  # noqa: E402
from . import enhance  # noqa: E402
from . import export as exporter  # noqa: E402
from . import stabilize  # noqa: E402
from . import guide  # noqa: E402

CHUNK = 15  # shots per EDL chunk when playing the shot list
PROJECTS_DIR = ROOT / "projects"

HOTKEYS = [
    ("Space", "Play / pause"),
    ("I", "Mark in"),
    ("O", "Mark out → adds the shot"),
    ("Shift+I / Shift+O", "Move the selected shot's in / out to the playhead"),
    (", / .  (or Ctrl+← / Ctrl+→)", "Step one frame back / forward"),
    ("← / →", "Back / forward 1 s   (Shift: 5 s)"),
    ("Home / End", "Start / end of clip"),
    ("[ / ]", "Previous / next shot (in list order)"),
    ("PgUp / PgDn", "Previous / next clip in the file list"),
    ("P", "Play the selected shot once"),
    ("F5", "Play the shot list from the selected shot"),
    ("Shift+F5", "Play the shot list from the start"),
    ("Esc", "Stop shot-list playback / cancel a pending in / leave the text panel"),
    ("Alt+↑ / Alt+↓", "Move the selected shot up / down (crosses section headings)"),
    ("Delete", "Delete the selected shot"),
    ("F2 or Enter", "Edit the selected shot's note"),
    ("Ctrl+Z / Ctrl+Y", "Undo / redo (outside the text panel)"),
    ("M", "Mute"),
    ("G", "Frame guide: off → 16:9 → 9:16 → both"),
    ("R / Shift+R", "Rotate the clip 90° clockwise / anticlockwise (whole clip, used by export)"),
    ("S", "Stabilize the selected shot on / off (applied when exporting)"),
    ("Shift+S", "Preview the selected shot stabilized, side by side with the original"),
    ("Ctrl+K", "Split the selected voice / music clip at the playhead"),
    ("Delete", "Deletes the selected voice / music clip when you last clicked one in the lanes"),
    ("Audio lanes", "Wheel scrolls, Ctrl+wheel zooms, drag a clip's right edge to lengthen it (everything "
                    "after slides), left edge to let in more of the take, middle to move it; "
                    "double-click opens it in the player, right-click for level / fades / mute"),
]


# ---- Markdown panel -----------------------------------------------------------------------------
class MdHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        mono = QFont("Consolas")

        def fmt(color, size=None, bold=False, family=None, bg=None):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if size:
                f.setFontPointSize(size)
            if bold:
                f.setFontWeight(QFont.Bold)
            if family:
                f.setFontFamilies([family])
            if bg:
                f.setBackground(QColor(bg))
            return f
        self.f_title = fmt("#ffffff", 16, True, "Georgia")
        self.f_sec = fmt("#f4efe6", 14, True, "Georgia")
        self.f_tot = fmt("#8d949c", 9, family=mono.family())
        self.f_num = fmt("#ff6f5e", 11, True)
        self.f_time = fmt("#8d949c", 9, family=mono.family())
        self.f_meta = fmt("#9aa1a9", 9, family=mono.family())
        self.f_chip = fmt("#ff9c88", 8.5, bg="#3a1f1c")
        self.f_sound = fmt("#c4f2df", 8.5, bg="#24564a")
        self.f_chip_other = fmt("#cfd3d8", 8.5, bg="#33373d")
        self.f_dim = fmt("#555b62", 8.5)

    def highlightBlock(self, text):
        if text.startswith("### "):
            num = text[4:].split("·")[0]
            self.setFormat(0, 4 + len(num), self.f_num)
            self.setFormat(4 + len(num), len(text), self.f_time)
        elif text.startswith("## "):
            self.setFormat(0, len(text), self.f_sec)
        elif text.startswith("# "):
            self.setFormat(0, len(text), self.f_title)
        elif text.startswith("*") and text.rstrip().endswith("*") and len(text) > 2:
            self.setFormat(0, len(text), self.f_tot)
        elif text.startswith("`"):
            self.setFormat(0, len(text), self.f_meta)
        elif text.startswith(">"):
            self.setFormat(0, len(text), self.f_dim)
            i = 0
            while (a := text.find("[", i)) >= 0 and (b := text.find("]", a)) >= 0:
                chip = text[a + 1:b]
                f = self.f_sound if chip.startswith(("Sound", "Muted")) else (
                    self.f_chip if (chip.startswith(("DAY", "📍", "Main", "End"))) else self.f_chip_other)
                self.setFormat(a, b - a + 1, f)
                i = b + 1


class MdEdit(QPlainTextEdit):
    def __init__(self, win):
        super().__init__()
        self.win = win

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.win.md_focus_out()

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton and not self.textCursor().hasSelection():
            self.win.md_clicked()


class VideoArea(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setStyleSheet("background:black")
        self.setMinimumSize(320, 200)


# ---- main window -------------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, project_path: str | None = None):
        super().__init__()
        self.settings = QSettings("ShotPicker", "ShotPicker")
        self.project: Project | None = None
        self.cur_key: str | None = None
        self.selected: int | None = None
        self.pending_in: float | None = None
        self.stop_at: float | None = None
        self.mode = "clip"
        self.list_shots: list[Shot] = []
        self.list_chunks: list[tuple[int, list[float]]] = []
        self.list_starts: list[float] = []
        self.list_cur = -1
        self.spans: dict[int, tuple[int, int]] = {}
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.md_dirty = False
        self.md_undo_pushed = False
        self.setting_text = False
        self.edge_snap = None
        self.last_seek = 0.0
        self.fps = 0.0
        self.sel_audio: int | None = None     # selected voice / music clip
        self.sel_kind = "shot"                # what was clicked last: 'shot' or 'audio'
        self.audio_snap = None                # undo snapshot while dragging in the lanes
        self.mix_path: Path | None = None     # voice + music mix for playback (None = none / not built)
        self.mix_key = ""
        self.mix_worker = None
        self.amp_loaded = ""
        self._build_ui()
        self._make_player()
        self._load_initial(project_path)

    # ---- UI ------------------------------------------------------------------------------------
    def _build_ui(self):
        self.setWindowTitle("Shot Picker")
        self.resize(1700, 950)
        # left: files
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 0, 6)
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color:#9aa1a9")
        self.filter = QLineEdit(placeholderText="Filter clips…")
        self.filter.textChanged.connect(self._apply_filter)
        self.files = QListWidget()
        self.files.setFont(QFont("Consolas", 9))
        self.files.currentItemChanged.connect(self._file_chosen)
        lv.addWidget(self.folder_label)
        lv.addWidget(self.filter)
        lv.addWidget(self.files)
        # middle: player
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(4, 6, 4, 6)
        self.clip_label = QLabel("No clip")
        self.clip_label.setStyleSheet("font-size:11pt;color:#e8e8e8")
        self.video = VideoArea(self)
        self.timeline = Timeline()
        self.timeline.setFocusPolicy(Qt.StrongFocus)
        self.timeline.seekRequested.connect(self._timeline_seek)
        self.timeline.shotClicked.connect(lambda uid: self.select(uid, jump=False))
        self.timeline.edgeDragged.connect(self._edge_drag)
        self.timeline.cutClicked.connect(lambda i, off: self.start_list(i, off))
        self.lanes = AudioLanes()
        self.lanes.setFocusPolicy(Qt.StrongFocus)
        self.lanes.seekRequested.connect(self.play_from_time)
        self.lanes.clipSelected.connect(self._lane_selected)
        self.lanes.clipOpened.connect(self._lane_opened)
        self.lanes.contextRequested.connect(self._lane_menu)
        self.lanes.clipMoved.connect(self._lane_moved)
        self.lanes.clipTrimmed.connect(self._lane_trimmed)
        self.time_label = QLabel()
        self.time_label.setFont(QFont("Consolas", 10))
        bar = QHBoxLayout()

        def btn(text, tip, fn):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            bar.addWidget(b)
            return b
        btn("⏮", "Previous shot  [", lambda: self.jump_shot(-1))
        btn("◀|", "Frame back  ,", self.step_back)
        self.play_btn = btn("▶", "Play / pause  Space", self.play_pause)
        btn("|▶", "Frame forward  .", self.step_fwd)
        btn("⏭", "Next shot  ]", lambda: self.jump_shot(1))
        btn("In  I", "Mark in", self.mark_in)
        btn("Out  O", "Mark out (adds the shot)", self.mark_out)
        btn("▶ Shot", "Play the selected shot  P", self.play_selected)
        btn("▶▶ Play shot list", "From the selected shot  F5  (Shift+F5 from the start)", lambda: self.play_list(False))
        btn("■", "Stop shot-list playback  Esc", self.escape)
        btn("⇩ Export video", "Render the shot list to an MP4  Ctrl+E", self.export_video)
        self.guide_box = QComboBox()
        for _, label in guide.MODES:
            self.guide_box.addItem(label)
        self.guide_box.setToolTip("Grey out what a 16:9 or vertical 9:16 export crops away  (G)")
        self.guide_box.setFocusPolicy(Qt.NoFocus)
        self.guide_box.setCurrentIndex(int(self.settings.value("guide", 0)))
        self.guide_box.currentIndexChanged.connect(self._guide_changed)
        bar.addWidget(self.guide_box)
        self._guide_key = None
        bar.addStretch(1)
        bar.addWidget(self.time_label)
        mv.addWidget(self.clip_label)
        mv.addWidget(self.video, 1)
        mv.addWidget(self.timeline)
        mv.addWidget(self.lanes)
        mv.addLayout(bar)
        # right: markdown
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 6, 6, 6)
        rbar = QHBoxLayout()
        lab = QLabel("Shot list")
        lab.setStyleSheet("font-weight:bold")
        rbar.addWidget(lab)
        self.total_label = QLabel()
        self.total_label.setToolTip("Total length of all shots = length of the finished video")
        self.total_label.setStyleSheet("color:#ffb38a;font-weight:bold;margin-left:8px")
        rbar.addWidget(self.total_label)
        rbar.addStretch(1)
        for text, tip, fn in (("↑", "Move shot up  Alt+↑", lambda: self.move_selected(-1)),
                              ("↓", "Move shot down  Alt+↓", lambda: self.move_selected(1)),
                              ("✕", "Delete shot  Del", self.delete_selected),
                              ("Sort section", "Sort the selected shot's section by clip name and in point",
                               self.sort_section)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            rbar.addWidget(b)
        self.md_status = QLabel()
        self.md_status.setWordWrap(True)
        self.md = MdEdit(self)
        self.md.setFont(QFont("Segoe UI", 10))
        self.md.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.md.setStyleSheet("QPlainTextEdit{background:#121416;color:#e8e8e8;border:1px solid #2a2d32;padding:6px}")
        self.md_hl = MdHighlighter(self.md.document())
        self.md.textChanged.connect(self._md_changed)
        rv.addLayout(rbar)
        rv.addWidget(self.md, 1)
        rv.addWidget(self.md_status)
        self.right = right
        self.split = QSplitter()
        for w in (left, mid, right):
            self.split.addWidget(w)
        left.setMinimumWidth(250)
        right.setMinimumWidth(420)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 0)
        self.split.setSizes([340, 1000, 560])
        self.setCentralWidget(self.split)
        if self.settings.value("md_side", "right") == "left":
            self.split.insertWidget(0, right)
        self.saved_label = QLabel()
        self.statusBar().addPermanentWidget(self.saved_label)
        self._build_menus()
        # timers
        self.poll = QTimer(self, interval=30, timeout=self._tick)
        self.save_timer = QTimer(self, singleShot=True, interval=500, timeout=self._save)
        self.md_timer = QTimer(self, singleShot=True, interval=700, timeout=self.apply_md)
        self.mix_timer = QTimer(self, singleShot=True, interval=600, timeout=self._rebuild_mix)

    def _build_menus(self):
        m = self.menuBar()

        def add(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a
        f = m.addMenu("&File")
        add(f, "New project from footage folder…", self.new_project, "Ctrl+N")
        add(f, "Open project…", self.open_project_dialog, "Ctrl+O")
        add(f, "Add video files…", self.add_files)
        add(f, "Change footage folder…", self.change_folder)
        f.addSeparator()
        add(f, "Import edit list (edl JSON)…", self.import_edl_dialog)
        add(f, "Re-import Korea v3 edit (replaces the Korea project)", self.reimport_korea)
        f.addSeparator()
        add(f, "Export video…", self.export_video, "Ctrl+E")
        add(f, "Export timeline for FilmCraft (XML, FCPXML, OTIO)…", self.export_timeline, "Ctrl+Shift+E")
        add(f, "Show project file in Explorer", self.reveal)
        add(f, "Exit", self.close)
        e = m.addMenu("&Edit")
        add(e, "Undo\tCtrl+Z", self.undo)
        add(e, "Redo\tCtrl+Y", self.redo)
        e.addSeparator()
        add(e, "Edit note\tF2", self.edit_note)
        add(e, "Move shot up\tAlt+Up", lambda: self.move_selected(-1))
        add(e, "Move shot down\tAlt+Down", lambda: self.move_selected(1))
        add(e, "Delete shot\tDel", self.delete_selected)
        add(e, "Sort section by clip name", self.sort_section)
        e.addSeparator()
        add(e, "Rotate clip 90° clockwise	R", lambda: self.rotate_clip(90))
        add(e, "Rotate clip 90° anticlockwise	Shift+R", lambda: self.rotate_clip(-90))
        add(e, "Rotate clip 180°", lambda: self.rotate_clip(180))
        add(e, "Reset clip rotation", lambda: self.rotate_clip(None))
        e.addSeparator()
        add(e, "Stabilize shot on / off	S", self.toggle_stab)
        add(e, "Stabilize all shots", lambda: self.stab_all(True))
        add(e, "Stabilize no shots", lambda: self.stab_all(False))
        add(e, "Preview stabilized shot	Shift+S", self.preview_stab)
        a = m.addMenu("&Audio")
        add(a, "Add voice clip…", lambda: self.add_audio("voice"))
        add(a, "Add music…", lambda: self.add_audio("music"))
        add(a, "Split selected clip at playhead\tCtrl+K", self.split_audio)
        add(a, "Level / fades of selected clip…", lambda: self._audio_dialog(self.sel_audio))
        add(a, "Delete selected voice / music clip", self.delete_audio)
        a.addSeparator()
        add(a, "Enhance voice (ClearerVoice)…", self.enhance_voice)
        add(a, "Voice: back to the original takes", self.voice_originals)
        a.addSeparator()
        add(a, "Game sound: volume of selected shot…", lambda: self.shot_volume(self.selected))
        add(a, "Game sound: mute / unmute selected shot", lambda: self.shot_mute(self.selected))
        add(a, "Game sound: mute all shots", lambda: self.shot_mute_all(True))
        add(a, "Game sound: unmute all shots", lambda: self.shot_mute_all(False))
        p = m.addMenu("&Play")
        add(p, "Play / pause\tSpace", self.play_pause)
        add(p, "Play selected shot\tP", self.play_selected)
        add(p, "Play shot list from selected\tF5", lambda: self.play_list(False))
        add(p, "Play shot list from start\tShift+F5", lambda: self.play_list(True))
        add(p, "Stop\tEsc", self.escape)
        v = m.addMenu("&View")
        add(v, "Shot list on the left / right", self.swap_side)
        h = m.addMenu("&Help")
        add(h, "Hotkeys", self.show_hotkeys, "F1")

    def _make_player(self):
        self.mpv = mpv.MPV(wid=str(int(self.video.winId())), vo="gpu", hwdec="d3d11va", keep_open="yes",
                           idle="yes", hr_seek="yes", hr_seek_framedrop="no", input_default_bindings=False,
                           input_vo_keyboard=False, input_cursor=False, osc=False, cursor_autohide="no",
                           prefetch_playlist="yes", osd_level=1, osd_font_size=30, osd_duration=1200,
                           demuxer_max_bytes="400MiB", demuxer_max_back_bytes="400MiB", ytdl=False,
                           audio_display="no", pause=True)
        # mpv draws into a native child window; disabling it for input keeps keyboard focus in Qt,
        # so hotkeys keep working after clicking the picture
        import ctypes
        ctypes.windll.user32.EnableWindow(int(self.video.winId()), False)
        # audio-only player for the voice + music mix, kept in step with the picture in shot-list playback
        self.amp = mpv.MPV(vid="no", idle="yes", keep_open="yes", pause=True, input_default_bindings=False,
                           audio_display="no", ytdl=False)
        self.poll.start()

    # ---- project -------------------------------------------------------------------------------
    def _load_initial(self, path):
        path = path or self.settings.value("last_project", "")
        if not path or not Path(path).exists():
            kp = Path(importers.KOREA["out"])
            if kp.exists():
                path = str(kp)
            elif Path(importers.KOREA["edl"]).exists():
                path = str(importers.import_korea().path)
        if path and Path(path).exists():
            self.open_project(path)
        else:
            self.project = Project()
            self._project_loaded()
            self.statusBar().showMessage("File ▸ New project from footage folder… to start.")

    def open_project(self, path):
        try:
            self.project = Project.open(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Shot Picker", f"Could not open {path}:\n{e}")
            return
        self.settings.setValue("last_project", str(path))
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._project_loaded()

    def _project_loaded(self):
        p = self.project
        self.setWindowTitle(f"Shot Picker — {p.title}" + (f"  ({p.path})" if p.path else ""))
        self.folder_label.setText(p.footage_dir or "(no footage folder)")
        self.files.blockSignals(True)
        self.files.clear()
        for k in p.files(refresh=True):
            it = QListWidgetItem()
            it.setData(Qt.UserRole, k)
            self.files.addItem(it)
        self.files.blockSignals(False)
        self._refresh_files()
        self.selected = None
        self.sel_audio = None
        self.lanes.project = p
        self.lanes.selected = None
        self.lanes.update()
        self.mix_timer.start()
        self._render_md()
        if p.footage_dir and not Path(p.footage_dir).is_dir():
            self.statusBar().showMessage(f"Footage folder not found: {p.footage_dir} (File ▸ Change footage folder…)")
        shots = p.all_shots()
        if shots:
            self.select(shots[0].uid, jump=True)
        elif self.files.count():
            self.files.setCurrentRow(0)

    def _refresh_files(self):
        counts = {}
        for s in self.project.all_shots():
            counts[s.file] = counts.get(s.file, 0) + 1
        for i in range(self.files.count()):
            it = self.files.item(i)
            k = it.data(Qt.UserRole)
            n = counts.get(k, 0)
            idx = self.project.idx(k)
            rot = self.project.rotation(k)
            it.setText(f"#{idx:<4}{Path(k).name}" + (f"  ●{n}" if n else "") + (f"  ↻{rot}°" if rot else ""))
            it.setForeground(QColor("#ffb38a") if n else QColor("#c9ced4"))
        self._apply_filter()
        n = len(self.project.all_shots())
        self.total_label.setText(f"{tc(self.project.total())} total · {n} shot{'' if n == 1 else 's'}")

    def _apply_filter(self):
        q = self.filter.text().lower()
        for i in range(self.files.count()):
            it = self.files.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def changed(self, from_md=False):
        """Model changed: autosave, refresh views."""
        self.save_timer.start()
        self._apply_rotation()
        self._refresh_files()
        self._refresh_timeline()
        self.lanes.update()
        if audio.mix_key(self.project) != self.mix_key:
            self.mix_timer.start()
        if not from_md:
            self._render_md()
        else:
            self._highlight_selected()
        self.saved_label.setText("● unsaved")

    def _save(self):
        if not self.project or not self.project.path:
            self.saved_label.setText("not saved — no project file")
            return
        try:
            self.project.save()
            self.saved_label.setText(f"Saved {time.strftime('%H:%M:%S')}  ·  {self.project.path.name}")
        except Exception as e:  # noqa: BLE001
            self.saved_label.setText(f"SAVE FAILED: {e}")

    def push_undo(self):
        self.undo_stack.append(self.project.snapshot())
        del self.undo_stack[:-300]
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return self.statusBar().showMessage("Nothing to undo", 2000)
        self.redo_stack.append(self.project.snapshot())
        self.project.restore(self.undo_stack.pop())
        self.changed()
        self.statusBar().showMessage("Undone", 1500)

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.project.snapshot())
        self.project.restore(self.redo_stack.pop())
        self.changed()

    def new_project(self):
        d = QFileDialog.getExistingDirectory(self, "Footage folder (read-only, nothing is written there)")
        if not d:
            return
        PROJECTS_DIR.mkdir(exist_ok=True)
        default = PROJECTS_DIR / f"{Path(d).name}.shotpicker.json"
        out, _ = QFileDialog.getSaveFileName(self, "Save project as", str(default), "Shot Picker (*.shotpicker.json)")
        if not out:
            return
        if Path(out).resolve().is_relative_to(Path(d).resolve()):
            QMessageBox.warning(self, "Shot Picker", "Please save the project outside the footage folder.")
            return
        if not out.endswith(".shotpicker.json"):
            out = out.removesuffix(".json") + ".shotpicker.json"
        p = Project(out)
        p.title = Path(d).name
        p.footage_dir = d
        p.files()
        p.save()
        self.open_project(out)

    def open_project_dialog(self):
        out, _ = QFileDialog.getOpenFileName(self, "Open project", str(PROJECTS_DIR), "Shot Picker (*.shotpicker.json)")
        if out:
            self.open_project(out)

    def add_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Add video files", self.project.footage_dir,
                                             "Video (*.mp4 *.mov *.mkv *.avi *.m4v *.mts *.webm);;All files (*)")
        if not fs:
            return
        for f in fs:
            f = os.path.normpath(f)
            if self.project.footage_dir and Path(f).parent == Path(self.project.footage_dir):
                continue
            if f not in self.project.extra_files:
                self.project.extra_files.append(f)
        self._project_loaded()
        self.save_timer.start()

    def change_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Footage folder", self.project.footage_dir)
        if d:
            self.project.footage_dir = os.path.normpath(d)
            self._project_loaded()
            self.save_timer.start()

    def import_edl_dialog(self):
        edl, _ = QFileDialog.getOpenFileName(self, "Edit list JSON (edl_refined.json style)", "", "JSON (*.json)")
        if not edl:
            return
        d = QFileDialog.getExistingDirectory(self, "Footage folder for this edit list")
        if not d:
            return
        out = str(Path(edl).with_name("shot_picker.shotpicker.json"))
        out, _ = QFileDialog.getSaveFileName(self, "Save project as", out, "Shot Picker (*.shotpicker.json)")
        if not out:
            return
        p = importers.import_edl(edl, os.path.normpath(d), out)
        p.save()
        self.open_project(out)

    def reimport_korea(self):
        if QMessageBox.question(self, "Shot Picker", "Replace the Korea shot-picker project with a fresh import of "
                                "analysis/edl_refined.json? Changes made in the app since the last import are lost "
                                "(a .bak copy is kept).") != QMessageBox.Yes:
            return
        kp = Path(importers.KOREA["out"])
        if kp.exists():
            kp.with_name(kp.name + ".bak").write_bytes(kp.read_bytes())
        self.open_project(str(importers.import_korea().path))

    def export_video(self):
        if not self.project or not self.project.all_shots():
            return QMessageBox.information(self, "Export video", "Mark some shots first.")
        if self.mode == "list":
            self._leave_list_mode()
        self.mpv.pause = True
        dlg = ExportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        fmt, fps, out = dlg.values()
        if self.project.footage_dir and Path(out).resolve().is_relative_to(Path(self.project.footage_dir).resolve()):
            return QMessageBox.warning(self, "Export video", "Please save outside the footage folder.")
        self.settings.setValue("export_fmt", fmt)
        self.settings.setValue("export_fps", fps)
        n = len(self.project.all_shots())
        prog = QProgressDialog("Starting…", "Cancel", 0, n + 1, self)
        prog.setWindowTitle(f"Exporting {fmt} · {tc(self.project.total())}")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setMinimumWidth(460)
        snap = Project()
        snap.load_json(self.project.to_json())  # export a frozen copy, so edits during export can't mix in
        work = ExportWorker(snap, fmt, fps, out)
        work.progress.connect(lambda d, t, msg: (prog.setValue(d), prog.setLabelText(msg)))
        prog.canceled.connect(work.cancel)

        def finished(ok, msg):
            prog.reset()
            work.wait()  # the signal arrives before run() returns; dropping a running QThread aborts the app
            self._export_worker = None
            if ok:
                b = QMessageBox(QMessageBox.Information, "Export video", f"Saved:\n{msg}", parent=self)
                show = b.addButton("Show in Explorer", QMessageBox.AcceptRole)
                b.addButton(QMessageBox.Close)
                b.exec()
                if b.clickedButton() is show:
                    os.system(f'explorer /select,"{msg}"')
            elif msg != "Cancelled":
                QMessageBox.warning(self, "Export video", msg)
            else:
                self.statusBar().showMessage("Export cancelled", 3000)
        work.done.connect(finished)
        self._export_worker = work
        work.start()

    def export_timeline(self):
        if not self.project or not self.project.all_shots():
            return QMessageBox.information(self, "Export timeline", "Mark some shots first.")
        base = self.project.path.parent if self.project.path else PROJECTS_DIR
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in self.project.title).strip() or "timeline"
        default = str(base / f"{safe} - {time.strftime('%Y%m%d-%H%M')}.xml")
        f, _ = QFileDialog.getSaveFileName(self, "Export timeline (also writes .fcpxml and .otio next to it)",
                                           default, "FCP 7 XML (*.xml)")
        if not f:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            outs = interchange.export_all(self.project, Path(f).with_suffix(""))
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            return QMessageBox.warning(self, "Export timeline", f"Export failed:\n{e}")
        QApplication.restoreOverrideCursor()
        b = QMessageBox(QMessageBox.Information, "Export timeline",
                        "Saved:\n" + "\n".join(str(o) for o in outs) +
                        "\n\nIn FilmCraft: File ▸ Import, pick one of these.", parent=self)
        show = b.addButton("Show in Explorer", QMessageBox.AcceptRole)
        b.addButton(QMessageBox.Close)
        b.exec()
        if b.clickedButton() is show:
            os.system(f'explorer /select,"{outs[0]}"')

    def reveal(self):
        if self.project.path:
            os.system(f'explorer /select,"{self.project.path}"')

    def swap_side(self):
        on_left = self.split.indexOf(self.right) == 0
        self.split.insertWidget(2 if on_left else 0, self.right)
        self.settings.setValue("md_side", "right" if on_left else "left")

    def show_hotkeys(self):
        rows = "".join(f"<tr><td><b>{k}</b>&nbsp;&nbsp;</td><td>{v}</td></tr>" for k, v in HOTKEYS)
        QMessageBox.information(self, "Hotkeys", f"<table>{rows}</table>")

    # ---- player --------------------------------------------------------------------------------
    def load_clip(self, key, t=0.0, play=None):
        if self.mode == "list":
            self._leave_list_mode()
        if key != self.cur_key or self.mpv.path is None:
            self.cur_key = key
            self.timeline.duration = 0
            path = self.project.abspath(key)
            if not Path(path).exists():
                self.statusBar().showMessage(f"Missing file: {path}")
            self._apply_rotation(key)
            self.mpv.loadfile(path, "replace", start=f"{max(t, 0):.3f}")
            self._select_file_row(key)
        else:
            self.seek(t, exact=True)
        if play is not None:
            self.mpv.pause = not play
        self._refresh_timeline()

    def _shown_key(self):
        """The clip on screen: the loaded clip, or the current shot's clip during shot-list playback."""
        if self.mode == "list" and 0 <= self.list_cur < len(self.list_shots):
            return self.list_shots[self.list_cur].file
        return self.cur_key

    def _apply_rotation(self, key=None):
        key = key or self._shown_key()
        rot = self.project.rotation(key) if key else 0
        try:
            if (self.mpv.video_rotate or 0) != rot:
                self.mpv.video_rotate = rot
        except Exception:  # noqa: BLE001
            pass

    def rotate_clip(self, degrees):
        """Turn the clip on screen (all its shots). None resets it."""
        key = self._shown_key()
        if not key:
            return self.statusBar().showMessage("Pick a clip first", 2000)
        self.push_undo()
        rot = self.project.turn(key, -self.project.rotation(key) if degrees is None else degrees)
        self.changed()
        self._update_clip_label()
        self.mpv.show_text(f"Rotation: {rot}°" if rot else "Rotation: as filmed", "1200")

    # ---- stabilization -----------------------------------------------------------------------------
    def toggle_stab(self):
        s = self._sel_shot()
        if not s:
            return self.statusBar().showMessage("Select a shot first", 2000)
        self.push_undo()
        if s.extra.get("stab"):
            s.extra.pop("stab")
        else:
            s.extra["stab"] = True
        self.changed()
        on = bool(s.extra.get("stab"))
        self.mpv.show_text("Stabilize: on (export; Shift+S to preview)" if on else "Stabilize: off", "1500")

    def stab_all(self, on):
        shots = self.project.all_shots() if self.project else []
        if not shots:
            return
        self.push_undo()
        for s in shots:
            if on and not s.extra.get("stab"):
                s.extra["stab"] = True
            elif not on:
                s.extra.pop("stab", None)
        self.changed()
        self.statusBar().showMessage(f"Stabilize {'on' if on else 'off'} for all {len(shots)} shots", 4000)

    def preview_stab(self):
        """Render the selected shot original | stabilized and open it in the system player."""
        s = self._sel_shot()
        if not s:
            return self.statusBar().showMessage("Select a shot first", 2000)
        if getattr(self, "_stab_worker", None):
            return self.statusBar().showMessage("A stabilize preview is already rendering…", 2000)
        n = self.project.number(s.uid)
        self.statusBar().showMessage(f"Rendering stabilize preview of shot {n}…")
        w = StabPreviewWorker(self.project.abspath(s.file), s.clone(), self.project.rotation(s.file),
                              stabilize.cache_dir_for(self.project.path) if self.project.path else
                              Path(tempfile.gettempdir()) / "shotpicker_stab", n)

        def finished(ok, msg):
            w.wait()
            self._stab_worker = None
            if ok:
                self.statusBar().showMessage(f"Preview of shot {n}: left original, right stabilized", 5000)
                os.startfile(msg)
            else:
                QMessageBox.warning(self, "Stabilize preview", msg)
        w.done.connect(finished)
        self._stab_worker = w
        w.start()

    def _select_file_row(self, key):
        self.files.blockSignals(True)
        for i in range(self.files.count()):
            if self.files.item(i).data(Qt.UserRole) == key:
                self.files.setCurrentRow(i)
                self.files.scrollToItem(self.files.item(i))
                break
        self.files.blockSignals(False)

    def _file_chosen(self, cur, _prev):
        if cur is not None:
            self.pending_in = None
            self.load_clip(cur.data(Qt.UserRole), 0.0)

    def pos(self) -> float:
        return self.mpv.time_pos or 0.0

    def seek(self, t, exact=True):
        if self.mpv.path is None:
            return
        try:
            self.mpv.seek(max(t, 0), "absolute", "exact" if exact else "keyframes")
        except SystemError:
            pass

    def _timeline_seek(self, t, exact):
        if self.mode != "clip":
            return
        now = time.monotonic()
        if exact or now - self.last_seek > 0.06:
            self.last_seek = now
            self.seek(t, exact)

    def play_pause(self):
        if self.mpv.path is None:
            return
        self.mpv.pause = not self.mpv.pause

    def step_fwd(self):
        self._ensure_clip_mode()
        self.mpv.frame_step()

    def step_back(self):
        self._ensure_clip_mode()
        self.mpv.frame_back_step()

    def nudge(self, d):
        if self.mode == "list":
            return
        self.seek(min(self.pos() + d, self.timeline.duration or 1e9), exact=True)

    def _tick(self):
        try:
            pos = self.mpv.time_pos
            dur = self.mpv.duration
            paused = self.mpv.pause
        except Exception:  # noqa: BLE001
            return
        self.play_btn.setText("▶" if paused else "❚❚")
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 4 == 0:
            self._update_guide()
        if self._ticks % 33 == 0 and self.mode == "clip":
            self._update_clip_label()  # decoder name settles a moment after loading
        if self.mode == "list":
            self._tick_list(pos)
            return
        if dur and abs(dur - self.timeline.duration) > 1e-3:
            self.timeline.duration = dur
            self._update_clip_label()
        if pos is None:
            return
        if self.stop_at is not None and pos >= self.stop_at - 0.005:
            self.mpv.pause = True
            self.stop_at = None
        self.timeline.pos = pos
        self.timeline.pending_in = self.pending_in
        self.timeline.update()
        ct = self._cut_time_of(pos)
        if ct is not None and abs(ct - self.lanes.pos) > 1e-3:
            self.lanes.pos = ct
            self.lanes.ensure_visible(ct)
            self.lanes.update()
        fps = self.mpv.container_fps or 0
        self.fps = fps
        self.time_label.setText(f"{tc_ms(pos)} / {tc_ms(dur or 0)}   frame {round(pos * fps) if fps else '-'}")

    def _guide_changed(self, i):
        self.settings.setValue("guide", i)
        self._update_guide()
        self.mpv.show_text(guide.MODES[i][1], "1000")

    def _guide_y(self) -> float:
        """Vertical crop position of the shot on screen (Korea shots carry 'y'; default middle)."""
        if self.mode == "list" and 0 <= self.list_cur < len(self.list_shots):
            s = self.list_shots[self.list_cur]
        else:
            s = self._sel_shot()
            if not s or s.file != self.cur_key:
                pos = self.mpv.time_pos or 0
                s = next((x for x in self.project.shots_in(self.cur_key or "") if x.t_in <= pos <= x.t_out), None)
        return float(s.extra.get("y", 0.5)) if s else 0.5

    def _update_guide(self):
        mode = guide.MODES[self.guide_box.currentIndex()][0]
        try:
            osd = self.mpv.osd_dimensions if mode != "off" else None
            vp = (self.mpv.video_out_params or self.mpv.video_params) if mode != "off" else None
        except Exception:  # noqa: BLE001
            return
        sw = (vp or {}).get("dw") or (vp or {}).get("w")
        sh = (vp or {}).get("dh") or (vp or {}).get("h")
        y = self._guide_y() if mode != "off" else 0.5
        key = (mode, tuple(sorted((osd or {}).items())), sw, sh, y)
        if key == self._guide_key:
            return
        self._guide_key = key
        data = guide.ass(mode, osd, sw, sh, y)
        try:
            if data:
                self.mpv.command("osd-overlay", id=1, format="ass-events", data=data,
                                 res_x=int(osd["w"]), res_y=int(osd["h"]), z=-1)
            else:
                self.mpv.command("osd-overlay", id=1, format="none", data="")
        except Exception:  # noqa: BLE001
            self._guide_key = None

    def _update_clip_label(self):
        if not self.cur_key:
            return
        try:
            w, h = self.mpv.width, self.mpv.height
            fps = self.mpv.container_fps or 0
            hw = self.mpv.hwdec_current or "software"
        except Exception:  # noqa: BLE001
            w = h = fps = 0
            hw = "?"
        idx = self.project.idx(self.cur_key)
        rot = self.project.rotation(self.cur_key)
        self.clip_label.setText(f"<b>#{idx}</b> &nbsp;{Path(self.cur_key).name} &nbsp; "
                                + (f"<b style='color:#ffb38a'>↻ {rot}°</b> &nbsp; " if rot else "") +
                                f"<span style='color:#8d949c'>{w}×{h} · {fps:.2f} fps · "
                                f"{tc(self.timeline.duration)} · decode: {hw}</span>")

    def _refresh_timeline(self):
        tl = self.timeline
        tl.selected = self.selected
        if self.mode == "clip":
            tl.mode = "clip"
            nums = {s.uid: n for n, s in enumerate(self.project.all_shots(), 1)}
            tl.shots = [(s.uid, nums[s.uid], s.t_in, s.t_out) for s in self.project.shots_in(self.cur_key)] \
                if self.cur_key else []
            if self.cur_key:
                for lane in ("voice", "music"):
                    for n, c in enumerate(self.project.lane(lane), 1):
                        if c.file == self.cur_key:
                            tl.shots.append((c.uid, n, c.t_in, c.t_out))
            if self.sel_kind == "audio":
                tl.selected = self.sel_audio
        tl.update()

    # ---- marking -------------------------------------------------------------------------------
    def _ensure_clip_mode(self):
        if self.mode == "list":
            self._leave_list_mode()

    def mark_in(self):
        self._ensure_clip_mode()
        if not self.cur_key:
            return
        self.pending_in = self.pos()
        self.mpv.show_text(f"IN  {tc_ms(self.pending_in)}", "1200")

    def mark_out(self):
        self._ensure_clip_mode()
        if not self.cur_key:
            return
        if self.pending_in is None:
            self.statusBar().showMessage("Press I first to set the in point.", 3000)
            self.mpv.show_text("Press I first", "1200")
            return
        a, b = sorted((self.pending_in, self.pos()))
        if b - a < 0.05:
            self.statusBar().showMessage("Shot too short — move the playhead past the in point.", 3000)
            return
        self.push_undo()
        shot = self.project.add_shot(Shot(self.cur_key, round(a, 3), round(b, 3)))
        self.pending_in = None
        self.selected = shot.uid
        self.changed()
        n = self.project.number(shot.uid)
        self.mpv.show_text(f"Shot {n} added  {tc(a)}–{tc(b)}", "1500")
        self.statusBar().showMessage(f"Shot {n} added: {Path(self.cur_key).name} {tc(a)}–{tc(b)}", 4000)

    def set_selected_edge(self, which):
        self._ensure_clip_mode()
        if self.sel_kind == "audio" and self.sel_audio is not None:
            f = self.project.find_audio(self.sel_audio)
            if f and f[1].file == self.cur_key:
                self.push_undo()
                self.project.trim_audio(self.sel_audio, which, round(self.pos(), 3))
                self.project.sort_lanes()
                self.changed()
                return
        s = self._sel_shot()
        if not s or s.file != self.cur_key:
            self.statusBar().showMessage("Select a shot in this clip first (click it on the playbar).", 3000)
            return
        t = round(self.pos(), 3)
        if (which == "in" and t >= s.t_out - 0.05) or (which == "out" and t <= s.t_in + 0.05):
            self.statusBar().showMessage("In must stay before out.", 3000)
            return
        self.push_undo()
        old_end, old_dur = self.project.shot_span(s.uid)[1], s.dur
        if which == "in":
            s.t_in = t
        else:
            s.t_out = t
        self.project.ripple(old_end, s.dur - old_dur)
        self.changed()
        self.mpv.show_text(f"Shot {self.project.number(s.uid)} {which.upper()} {tc_ms(t)}", "1200")

    def _edge_drag(self, uid, which, t, finished):
        if self.project.find_audio(uid):
            if self.edge_snap is None:
                self.edge_snap = self.project.snapshot()
            self.project.trim_audio(uid, which, round(t, 3))
            c = self.project.find_audio(uid)[1]
            self.sel_audio, self.sel_kind = uid, "audio"
            self.lanes.selected = uid
            self.seek(c.t_in if which == "in" else c.t_out, exact=finished)
            self._refresh_timeline()
            self.lanes.update()
            if finished:
                self.project.sort_lanes()
                self.undo_stack.append(self.edge_snap)
                self.redo_stack.clear()
                self.edge_snap = None
                self.changed()
            return
        f = self.project.find(uid)
        if not f:
            return
        s = f[0].shots[f[1]]
        if self.edge_snap is None:
            self.edge_snap = self.project.snapshot()
            self.edge_ref = (self.project.shot_span(uid)[1], s.dur)
        if which == "in":
            s.t_in = round(min(t, s.t_out - 0.05), 3)
        else:
            s.t_out = round(max(t, s.t_in + 0.05), 3)
        self.selected = uid
        self.seek(s.t_in if which == "in" else s.t_out, exact=finished)
        self._refresh_timeline()
        if finished:
            old_end, old_dur = self.edge_ref
            self.project.ripple(old_end, s.dur - old_dur)
            self.undo_stack.append(self.edge_snap)
            self.redo_stack.clear()
            self.edge_snap = None
            self.changed()

    def _sel_shot(self) -> Shot | None:
        if self.selected is None:
            return None
        f = self.project.find(self.selected)
        return f[0].shots[f[1]] if f else None

    def select(self, uid, jump=True, play=None):
        self.selected = uid
        self.sel_kind = "shot"
        self.lanes.selected = uid
        self.lanes.update()
        s = self._sel_shot()
        if s and jump:
            self.load_clip(s.file, s.t_in, play)
        self._refresh_timeline()
        self._highlight_selected(scroll=True)

    def jump_shot(self, d):
        shots = self.project.all_shots()
        if not shots:
            return
        if self.mode == "list":
            i = max(0, min(len(shots) - 1, self.list_cur + d))
            return self.start_list(i)
        cur = self._sel_shot()
        if cur:
            i = next(k for k, s in enumerate(shots) if s.uid == cur.uid) + d
        else:
            here = [k for k, s in enumerate(shots) if s.file == self.cur_key]
            i = here[0] if here else 0
        if 0 <= i < len(shots):
            self.pending_in = None
            self.select(shots[i].uid, jump=True)

    def next_clip(self, d):
        rows = [i for i in range(self.files.count()) if not self.files.item(i).isHidden()]
        if not rows:
            return
        cur = self.files.currentRow()
        later = [r for r in rows if (r > cur if d > 0 else r < cur)]
        if later:
            self.files.setCurrentRow(later[0] if d > 0 else later[-1])

    def play_selected(self):
        s = self._sel_shot()
        if not s:
            return
        self.load_clip(s.file, s.t_in, play=False)
        self.stop_at = s.t_out
        self.mpv.pause = False

    def delete_selected(self):
        if self.sel_kind == "audio" and self.sel_audio is not None and not self.md.hasFocus():
            return self.delete_audio()
        s = self._sel_shot()
        if not s:
            return
        n = self.project.number(s.uid)
        self.push_undo()
        old_end = self.project.shot_span(s.uid)[1]
        self.project.remove(s.uid)
        self.project.ripple(old_end, -s.dur)
        self.selected = None
        self.changed()
        self.statusBar().showMessage(f"Shot {n} deleted (Ctrl+Z to undo)", 4000)

    def move_selected(self, d):
        if self.md.hasFocus():
            if not self.apply_md():
                return
            uid = self._uid_at_cursor()
            if uid is not None:
                self.selected = uid
        s = self._sel_shot()
        if not s:
            return
        self.push_undo()
        if self.project.move(s.uid, d):
            self.changed()
            self._highlight_selected(scroll=True, move_cursor=self.md.hasFocus())
        else:
            self.undo_stack.pop()

    def sort_section(self):
        s = self._sel_shot()
        if not s:
            return self.statusBar().showMessage("Select a shot in the section to sort.", 3000)
        self.push_undo()
        self.project.sort_section(self.project.find(s.uid)[0])
        self.changed()

    def escape(self):
        if self.mode == "list":
            self._leave_list_mode()
        elif self.pending_in is not None:
            self.pending_in = None
        self.stop_at = None

    # ---- shot-list playback --------------------------------------------------------------------
    def play_list(self, from_start):
        shots = self.project.all_shots()
        if not shots:
            return
        i = 0
        if not from_start and self.selected is not None:
            i = next((k for k, s in enumerate(shots) if s.uid == self.selected), 0)
        self.start_list(i)

    def _edl(self, shots):
        parts = []
        for s in shots:
            p = self.project.abspath(s.file).replace("\\", "/")
            parts.append(f"%{len(p.encode('utf-8'))}%{p},{s.t_in:.3f},{s.src_len:.3f}")
        return "edl://" + ";".join(parts)

    def start_list(self, i, offset=0.0):
        """Play shots i.. back to back. Chunks of CHUNK shots are mpv EDL files in the playlist."""
        shots = self.project.all_shots()
        if not shots:
            return
        self.list_shots = shots
        self.list_starts = [t for _, t in self.project.timeline()]
        self.list_chunks = []
        for a in range(i, len(shots), CHUNK):
            offs, t = [], 0.0
            for s in shots[a:a + CHUNK]:
                offs.append(t)
                t += s.src_len
            self.list_chunks.append((a, offs))
        self.mode = "list"
        self.pending_in = None
        self.stop_at = None
        self.list_cur = -1
        first = shots[i]
        self.mpv.loadfile(self._edl(shots[i:i + CHUNK]), "replace", start=f"{offset * first.speed:.3f}")
        for a, _ in self.list_chunks[1:]:
            self.mpv.loadfile(self._edl(shots[a:a + CHUNK]), "append")
        self.mpv.volume = audio.mpv_volume(first)
        self.mpv.pause = False
        self._amp_start(self.list_starts[i] + offset)
        tl = self.timeline
        tl.mode = "cut"
        tl.duration = self.project.total()
        names, last = [], None
        for s, sec in self.project.flat():
            names.append(sec.name if sec.name != last else None)
            last = sec.name
        tl.cut = [(s.uid, n, t, s.dur, names[n - 1]) for n, (s, t) in enumerate(self.project.timeline(), 1)]
        self.cur_key = None
        self.statusBar().showMessage("Playing shot list — Esc to stop, [ / ] to skip, click the bar to jump")

    def _list_locate(self, pos):
        try:
            pl = self.mpv.playlist_pos
        except Exception:  # noqa: BLE001
            return None
        if pl is None or pl < 0 or pl >= len(self.list_chunks) or pos is None:
            return None
        a, offs = self.list_chunks[pl]
        j = max(bisect.bisect_right(offs, pos + 1e-4) - 1, 0)
        return a + j, pos - offs[j], pl

    def _tick_list(self, pos):
        loc = self._list_locate(pos)
        if loc is None:
            return
        idx, into, pl = loc
        s = self.list_shots[idx]
        if idx != self.list_cur:
            self.list_cur = idx
            self.mpv.speed = s.speed
            self.mpv.volume = audio.mpv_volume(s)
            self._apply_rotation(s.file)
            self.selected = s.uid
            self.timeline.selected = s.uid
            self._select_file_row(s.file)
            self._highlight_selected(scroll=True)
            sec = self.project.find(s.uid)[0].name
            self.clip_label.setText(f"<b>▶ Shot list</b> &nbsp; shot {idx + 1}/{len(self.list_shots)} · {sec} · "
                                    f"#{self.project.idx(s.file)} {Path(s.file).name}")
        cut_t = self.list_starts[idx] + into / s.speed
        self.timeline.pos = cut_t
        self.timeline.update()
        self.lanes.pos = cut_t
        self.lanes.ensure_visible(cut_t)
        self.lanes.update()
        self._amp_sync(cut_t)
        self.time_label.setText(f"{tc_ms(cut_t)} / {tc_ms(self.timeline.duration)}   shot {idx + 1}")
        if pl == len(self.list_chunks) - 1 and self.mpv.eof_reached:
            self._leave_list_mode()

    def _leave_list_mode(self):
        pos = self.mpv.time_pos
        loc = self._list_locate(pos)
        self.mode = "clip"
        self.mpv.speed = 1.0
        self.mpv.volume = 100
        self.amp.pause = True
        self.timeline.mode = "clip"
        if loc:
            s = self.list_shots[loc[0]]
            self.selected = s.uid
            self.cur_key = None
            self.load_clip(s.file, min(s.t_in + loc[1], s.t_out), play=False)
        self.mpv.pause = True
        self._refresh_timeline()
        self.statusBar().showMessage("Stopped shot-list playback", 2000)

    # ---- markdown sync -------------------------------------------------------------------------
    def _render_md(self):
        text, self.spans = md_render(self.project)
        if text == self.md.toPlainText():
            self._highlight_selected()
            return
        cur = self.md.textCursor().blockNumber()
        sb = self.md.verticalScrollBar().value()
        self.setting_text = True
        self.md.setPlainText(text)
        self.setting_text = False
        self.md_dirty = False
        c = self.md.textCursor()
        c.movePosition(QTextCursor.Start)
        c.movePosition(QTextCursor.Down, n=min(cur, self.md.document().blockCount() - 1))
        self.md.setTextCursor(c)
        self.md.verticalScrollBar().setValue(sb)
        self._set_md_status("")
        self._highlight_selected()

    def _md_changed(self):
        if self.setting_text:
            return
        self.md_dirty = True
        self.md_timer.start()

    def _set_md_status(self, msg, error=False):
        self.md_status.setText(msg)
        self.md_status.setStyleSheet(f"color:{'#ff7a6b' if error else '#8d949c'}")

    def apply_md(self) -> bool:
        """Parse the text panel into the model. Returns False (model untouched) on errors."""
        if not self.md_dirty:
            return True
        self.md_timer.stop()
        title, secs, errors = md_parse(self.md.toPlainText(), self.project)
        if errors:
            self._set_md_status("Not applied — " + "; ".join(errors[:3]), error=True)
            return False
        before = self.project.snapshot()
        stats = md_apply(self.project, title, secs)
        self.md_dirty = False
        self.spans = stats["lines"]  # line positions follow the text as typed, even if nothing else changed
        self._highlight_selected()
        if self.project.snapshot() == before:
            self._set_md_status("")
            self._refresh_derived_lines()
            return True
        if not self.md_undo_pushed:
            self.undo_stack.append(before)
            self.redo_stack.clear()
            self.md_undo_pushed = True
        self.spans = stats["lines"]
        bits = [f"{stats[k]} {k}" for k in ("added", "removed", "changed") if stats[k]]
        self._set_md_status("Synced" + (": " + ", ".join(bits) if bits else "")
                            + (" — Ctrl+Z outside the panel to undo" if stats["removed"] else "")
                            + (" · lines starting with > are saved as notes" if stats["tag_notes"] else ""))
        if self.selected is not None and not self.project.find(self.selected):
            self.selected = None
        self.changed(from_md=True)
        self._refresh_derived_lines()
        return True

    def _refresh_derived_lines(self):
        """Rewrite the generated lines (### number · time, *totals*) in place while the user is still in the
        panel, so numbering follows the new order without moving their cursor or other text."""
        gen = md_render(self.project)[0].splitlines()
        g_shots = [ln for ln in gen if ln.startswith("### ")]
        g_secs = [gen[i + 1] for i, ln in enumerate(gen[:-1]) if ln.startswith("## ")]
        g_title = next((gen[i + 1] for i, ln in enumerate(gen[:-1]) if ln.startswith("# ")), None)
        doc = self.md.document()
        lines = [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]
        todo = {}
        shot_lines = [i for i, ln in enumerate(lines) if ln.startswith("### ")]
        if len(shot_lines) == len(g_shots):
            todo.update(zip(shot_lines, g_shots))
        sec_tot = [i + 1 for i, ln in enumerate(lines[:-1]) if ln.startswith("## ") and not ln.startswith("###")
                   and lines[i + 1].startswith("*")]
        if len(sec_tot) == len(g_secs):
            todo.update(zip(sec_tot, g_secs))
        tt = next((i + 1 for i, ln in enumerate(lines[:-1]) if ln.startswith("# ") and lines[i + 1].startswith("*")),
                  None)
        if tt is not None and g_title:
            todo[tt] = g_title
        here = self.md.textCursor().blockNumber()
        todo = {i: t for i, t in todo.items() if lines[i] != t and i != here}
        if not todo:
            return
        self.setting_text = True
        c = QTextCursor(doc)
        c.beginEditBlock()
        for i, t in todo.items():
            c.setPosition(doc.findBlockByNumber(i).position())
            c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            c.insertText(t)
        c.endEditBlock()
        self.setting_text = False

    def md_focus_out(self):
        ok = self.apply_md()
        self.md_undo_pushed = False
        if ok:
            self._render_md()  # renumber

    def _uid_at_cursor(self):
        line = self.md.textCursor().blockNumber()
        for uid, (a, b) in self.spans.items():
            if a <= line <= b:
                return uid
        return None

    def md_clicked(self):
        if self.md_dirty and not self.apply_md():
            return
        uid = self._uid_at_cursor()
        if uid is not None and uid != self.selected:
            if self.mode == "list":
                self._leave_list_mode()
            self.select(uid, jump=True, play=False)

    def _highlight_selected(self, scroll=False, move_cursor=False):
        sels = []
        span = self.spans.get(self.selected) if self.selected is not None else None
        if span:
            doc = self.md.document()
            n = self.project.number(self.selected) or 1
            col = colour(n)
            col.setAlpha(40)
            for line in range(span[0], span[1] + 1):
                blk = doc.findBlockByNumber(line)
                if not blk.isValid():
                    continue
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(col)
                sel.format.setProperty(QTextFormat.FullWidthSelection, True)
                sel.cursor = QTextCursor(blk)
                sels.append(sel)
            if scroll and (move_cursor or not self.md.hasFocus()):
                c = QTextCursor(doc.findBlockByNumber(span[0]))
                self.md.setTextCursor(c)
                self.md.centerCursor()
        self.md.setExtraSelections(sels)

    def edit_note(self):
        s = self._sel_shot()
        if not s:
            return
        self._render_md()
        a, b = self.spans[s.uid]
        doc = self.md.document()
        c = QTextCursor(doc.findBlockByNumber(a + 1))
        if not s.note:  # open an empty note line under the heading
            c.insertText("\n")
            c.movePosition(QTextCursor.PreviousBlock)
            self.md_dirty = False
            self.md_timer.stop()
        else:
            c.movePosition(QTextCursor.NextBlock, n=len(s.note.splitlines()) - 1)
            c.movePosition(QTextCursor.EndOfBlock)
        self.md.setTextCursor(c)
        self.md.setFocus()

    # ---- audio lanes ---------------------------------------------------------------------------
    def _cut_time_of(self, pos):
        """Where the player position sits on the cut timeline (selected shot or audio clip), or None."""
        if pos is None or not self.cur_key:
            return None
        if self.sel_kind == "audio" and self.sel_audio is not None:
            f = self.project.find_audio(self.sel_audio)
            if f and f[1].file == self.cur_key:
                return f[1].start + (pos - f[1].t_in)
        s = self._sel_shot()
        if s and s.file == self.cur_key and s.t_in - 0.5 <= pos <= s.t_out + 0.5:
            return self.project.shot_span(s.uid)[0] + (pos - s.t_in) / s.speed
        return None

    def play_from_time(self, t):
        shots = self.project.all_shots()
        if not shots:
            return
        for i, (s, a) in enumerate(self.project.timeline()):
            if a <= t < a + s.dur or i == len(shots) - 1:
                return self.start_list(i, min(t - a, s.dur - 0.05) if t >= a else 0.0)

    def _amp_start(self, t):
        path = str(self.mix_path) if self.mix_path and self.mix_path.exists() else ""
        try:
            if not path:
                self.amp.pause = True
                return
            if self.amp_loaded != path:
                self.amp.loadfile(path, "replace", start=f"{max(t, 0):.3f}")
                self.amp_loaded = path
            else:
                self.amp.seek(max(t, 0), "absolute", "exact")
            self.amp.pause = False
        except Exception:  # noqa: BLE001
            pass

    def _amp_sync(self, cut_t):
        if not self.amp_loaded:
            return
        try:
            if self.amp.pause != self.mpv.pause:
                self.amp.pause = self.mpv.pause
            at = self.amp.time_pos
            if at is not None and abs(at - cut_t) > 0.12 and not self.mpv.pause:
                self.amp.seek(max(cut_t, 0), "absolute", "exact")
        except Exception:  # noqa: BLE001
            pass

    def _rebuild_mix(self):
        key = audio.mix_key(self.project)
        if key == self.mix_key and self.mix_path and self.mix_path.exists():
            return
        if self.mix_worker is not None and self.mix_worker.isRunning():
            self.mix_timer.start()  # try again when the running one is done
            return
        if not self.project.audio_clips():
            self.mix_key, self.mix_path = key, None
            return
        out = Path(tempfile.gettempdir()) / "shotpicker" / f"mix-{key}.wav"
        snap = Project()
        snap.load_json(self.project.to_json())
        w = MixWorker(snap, out, key)
        w.done.connect(self._mix_done)
        self.mix_worker = w
        self.saved_label.setText(self.saved_label.text().split("  ·  mixing")[0] + "  ·  mixing audio…")
        w.start()

    def _mix_done(self, key, path, err):
        if self.mix_worker is not None:
            self.mix_worker.wait()
        self.mix_worker = None
        self.saved_label.setText(self.saved_label.text().split("  ·  mixing")[0])
        if err:
            self.statusBar().showMessage(f"Voice/music mix failed: {err[:200]}", 8000)
            return
        old = self.mix_path
        self.mix_key, self.mix_path = key, Path(path) if path else None
        if old and old != self.mix_path:
            try:
                if self.amp_loaded == str(old):
                    self.amp.command("stop")
                    self.amp_loaded = ""
                old.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        if self.mode == "list":
            self._amp_start(self.lanes.pos)
        if audio.mix_key(self.project) != key:
            self.mix_timer.start()

    def _lane_selected(self, row, uid):
        if row == "game":
            if self.mode == "list":
                self._leave_list_mode()
            self.select(uid, jump=False)
        else:
            self.sel_audio, self.sel_kind = uid, "audio"
            self.lanes.selected = uid
            self._refresh_timeline()
        self.lanes.update()

    def _lane_opened(self, row, uid):
        if row == "game":
            return self.select(uid, jump=True, play=False)
        f = self.project.find_audio(uid)
        if not f:
            return
        self.sel_audio, self.sel_kind = uid, "audio"
        self.load_clip(f[1].file, f[1].t_in, play=False)
        self.statusBar().showMessage("Voice/music clip open: drag its edges on the playbar, Shift+I / Shift+O "
                                     "at the playhead, Ctrl+K to split, P plays it", 6000)

    def _lane_moved(self, uid, start, finished):
        f = self.project.find_audio(uid)
        if not f:
            return
        if self.audio_snap is None:
            self.audio_snap = self.project.snapshot()
        f[1].start = round(start, 3)
        self.lanes.update()
        if finished:
            self._lane_commit()

    def _lane_trimmed(self, uid, which, t, finished):
        if self.audio_snap is None:
            self.audio_snap = self.project.snapshot()
        self.project.trim_audio(uid, which, round(t, 3))
        self.lanes.update()
        if finished:
            self._lane_commit()

    def _lane_commit(self):
        self.project.sort_lanes()
        if self.audio_snap is not None:
            self.undo_stack.append(self.audio_snap)
            self.redo_stack.clear()
            self.audio_snap = None
        self.changed()

    def _lane_menu(self, row, uid, gpos):
        m = QMenu(self)
        if row == "game":
            s = self.project.find(uid)
            s = s[0].shots[s[1]] if s else None
            if s:
                n = self.project.number(uid)
                m.addAction(f"Shot {n}: volume…", lambda: self.shot_volume(uid))
                m.addAction("Unmute" if s.extra.get("mute") else "Mute", lambda: self.shot_mute(uid))
                m.addAction("Open shot in player", lambda: self.select(uid, jump=True, play=False))
                m.addSeparator()
            m.addAction("Mute all shots", lambda: self.shot_mute_all(True))
            m.addAction("Unmute all shots", lambda: self.shot_mute_all(False))
        else:
            name = "voice clip" if row == "voice" else "music"
            if uid != -1:
                self.sel_audio, self.sel_kind = uid, "audio"
                self.lanes.selected = uid
                m.addAction("Level and fades…", lambda: self._audio_dialog(uid))
                c = self.project.find_audio(uid)[1]
                m.addAction("Unmute" if c.gain_db <= -60 else "Mute", lambda: self._audio_mute(uid))
                m.addAction("Split at playhead", self.split_audio)
                m.addAction("Open in player", lambda: self._lane_opened(row, uid))
                m.addAction("Delete", self.delete_audio)
                m.addSeparator()
            at = max(0.0, self.lanes.t_of(self.lanes.mapFromGlobal(gpos).x()))
            m.addAction(f"Add {name} here…", lambda: self.add_audio(row, at))
        m.exec(gpos)
        self.lanes.update()

    def add_audio(self, lane, at=None):
        last = self.settings.value(f"audio_dir_{lane}", "")
        files, _ = QFileDialog.getOpenFileNames(self, f"Add {lane}", last,
                                                "Audio (" + " ".join("*" + e for e in sorted(audio.AUDIO_EXT)) + ")")
        if not files:
            return
        self.settings.setValue(f"audio_dir_{lane}", str(Path(files[0]).parent))
        t = self.lanes.pos if at is None else at
        self.push_undo()
        for f in files:
            d = audio.duration(f)
            if d <= 0:
                QMessageBox.warning(self, "Add audio", f"Can't read {f}")
                continue
            c = AudioClip(f, 0.0, round(d, 3), round(t, 3), note=Path(f).stem)
            self.project.lane(lane).append(c)
            t += d
            self.sel_audio, self.sel_kind = c.uid, "audio"
        self.project.sort_lanes()
        self.lanes.selected = self.sel_audio
        self.changed()

    def delete_audio(self):
        f = self.project.find_audio(self.sel_audio) if self.sel_audio is not None else None
        if not f:
            return
        self.push_undo()
        self.project.lane(f[0]).remove(f[1])
        self.sel_audio = None
        self.sel_kind = "shot"
        self.changed()
        self.statusBar().showMessage("Clip deleted (Ctrl+Z to undo)", 3000)

    def split_audio(self):
        f = self.project.find_audio(self.sel_audio) if self.sel_audio is not None else None
        if not f:
            return self.statusBar().showMessage("Select a voice or music clip first.", 3000)
        lane, c = f
        if self.mode == "clip" and self.cur_key == c.file:
            src = self.pos()                                   # playhead in the open take
        else:
            src = c.t_in + (self.lanes.pos - c.start)          # playhead on the cut timeline
        if not c.t_in + 0.05 < src < c.t_out - 0.05:
            return self.statusBar().showMessage("Put the playhead inside the clip to split it.", 3000)
        self.push_undo()
        b = AudioClip(c.file, round(src, 3), c.t_out, round(c.start + src - c.t_in, 3), c.gain_db, 0.0, c.fade_out,
                      c.note, c.raw, c.unmute_db)
        c.t_out, c.fade_out = round(src, 3), 0.0
        self.project.lane(lane).append(b)
        self.project.sort_lanes()
        self.changed()

    def _audio_mute(self, uid):
        f = self.project.find_audio(uid)
        if not f:
            return
        c = f[1]
        self.push_undo()
        if c.gain_db <= -60:
            c.gain_db, c.unmute_db = (c.unmute_db or 0.0), None
        else:
            c.unmute_db = c.gain_db
            c.gain_db = -96.0
        self.changed()

    def _audio_dialog(self, uid):
        f = self.project.find_audio(uid) if uid is not None else None
        if not f:
            return self.statusBar().showMessage("Select a voice or music clip first.", 3000)
        c = f[1]
        d = QDialog(self)
        d.setWindowTitle(f"{'Voice' if f[0] == 'voice' else 'Music'} clip · {Path(c.file).name}")
        form = QFormLayout(d)
        from PySide6.QtWidgets import QDoubleSpinBox

        def spin(v, lo, hi, step, suffix):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setDecimals(1)
            w.setSingleStep(step)
            w.setSuffix(suffix)
            w.setValue(v)
            return w
        gain = spin(max(c.gain_db, -60), -60, 24, 1, " dB")
        fin = spin(c.fade_in, 0, max(c.dur, 0.1), 0.5, " s")
        fout = spin(c.fade_out, 0, max(c.dur, 0.1), 0.5, " s")
        form.addRow("Level", gain)
        form.addRow("Fade in", fin)
        form.addRow("Fade out", fout)
        hint = QLabel("0 dB = as recorded. -60 dB = silent.")
        hint.setStyleSheet("color:#9aa1a9")
        form.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec() != QDialog.Accepted:
            return
        self.push_undo()
        c.gain_db = -96.0 if gain.value() <= -60 else round(gain.value(), 1)
        c.fade_in, c.fade_out = round(fin.value(), 2), round(fout.value(), 2)
        self.changed()

    # ---- voice clean-up (ClearerVoice, enhance.py) ------------------------------------------------
    def enhance_voice(self):
        if not self.project or not self.project.voice:
            return self.statusBar().showMessage("The voice lane is empty.", 3000)
        if not enhance.ready():
            return QMessageBox.information(
                self, "Enhance voice",
                "Voice clean-up needs a one-time setup (PyTorch + ClearerVoice, about 3 GB).\n\n"
                f"Run setup_voice.bat in\n{enhance.ROOT}\nthen try again.")
        names = list(enhance.MODELS)
        pick, ok = QInputDialog.getItem(self, "Enhance voice", "Every take on the voice lane gets a cleaned copy "
                                        "next to it; cut points stay the same.\nModel:",
                                        [enhance.MODELS[k] for k in names], 0, False)
        if not ok:
            return
        model = names[[enhance.MODELS[k] for k in names].index(pick)]
        raws = sorted({c.raw or c.file for c in self.project.voice})
        if self.mode == "list":
            self._leave_list_mode()
        prog = QProgressDialog("Starting…", "Cancel", 0, len(raws) * 10, self)
        prog.setWindowTitle("Enhance voice")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setMinimumWidth(520)
        work = EnhanceWorker(raws, model)
        work.progress.connect(lambda v, msg: (prog.setValue(v), prog.setLabelText(msg)))
        prog.canceled.connect(work.cancel)

        def finished(done, err):
            prog.reset()
            work.wait()
            self._enhance_worker = None
            if done:
                self.push_undo()
                for c in self.project.voice:
                    raw = c.raw or c.file
                    if raw in done:
                        c.raw, c.file = raw, done[raw]
                self.changed()
                self.statusBar().showMessage(f"Voice: {len(done)} take(s) cleaned ({model}). "
                                             "Audio > Voice: back to the original takes undoes it.", 6000)
            if err and err != "Cancelled":
                QMessageBox.warning(self, "Enhance voice", err[-1500:])
        work.done.connect(finished)
        self._enhance_worker = work
        work.start()

    def voice_originals(self):
        if not self.project or not any(c.raw for c in self.project.voice):
            return self.statusBar().showMessage("The voice lane already uses the original takes.", 3000)
        self.push_undo()
        for c in self.project.voice:
            if c.raw:
                c.file, c.raw = c.raw, ""
        self.changed()

    def shot_volume(self, uid):
        f = self.project.find(uid) if uid is not None else None
        if not f:
            return self.statusBar().showMessage("Select a shot first.", 3000)
        s = f[0].shots[f[1]]
        g = shot_gain(s)
        v, ok = QInputDialog.getDouble(self, "Game sound", f"Shot {self.project.number(uid)} level in dB "
                                       "(0 = as recorded, -60 = mute):", 0.0 if g is None else g, -60, 24, 1)
        if not ok:
            return
        self.push_undo()
        if v <= -60:
            s.extra["mute"] = True
        else:
            s.extra.pop("mute", None)
            if v:
                s.extra["vol_db"] = round(v, 1)
            else:
                s.extra.pop("vol_db", None)
        self.changed()

    def shot_mute(self, uid):
        f = self.project.find(uid) if uid is not None else None
        if not f:
            return
        s = f[0].shots[f[1]]
        self.push_undo()
        if s.extra.get("mute"):
            s.extra.pop("mute", None)
        else:
            s.extra["mute"] = True
        self.changed()

    def shot_mute_all(self, on):
        self.push_undo()
        for s in self.project.all_shots():
            if on:
                s.extra["mute"] = True
            else:
                s.extra.pop("mute", None)
        self.changed()

    # ---- keys ----------------------------------------------------------------------------------
    def handle_key(self, e) -> bool:
        k = e.key()
        mods = e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier)
        shift, ctrl, alt = mods == Qt.ShiftModifier, mods == Qt.ControlModifier, mods == Qt.AltModifier
        none = mods == Qt.NoModifier
        fw = QApplication.focusWidget()
        in_text = isinstance(fw, (QPlainTextEdit, QLineEdit))
        if k == Qt.Key_F5:
            self.play_list(from_start=shift)
            return True
        if alt and k in (Qt.Key_Up, Qt.Key_Down):
            self.move_selected(-1 if k == Qt.Key_Up else 1)
            return True
        if k == Qt.Key_Escape:
            if in_text:
                self.timeline.setFocus()
            else:
                self.escape()
            return True
        if in_text:
            return False
        acts = {
            (Qt.Key_Space, True): self.play_pause,
            (Qt.Key_I, True): self.mark_in,
            (Qt.Key_O, True): self.mark_out,
            (Qt.Key_P, True): self.play_selected,
            (Qt.Key_M, True): lambda: setattr(self.mpv, "mute", not self.mpv.mute),
            (Qt.Key_R, True): lambda: self.rotate_clip(90),
            (Qt.Key_S, True): self.toggle_stab,
            (Qt.Key_G, True): lambda: self.guide_box.setCurrentIndex((self.guide_box.currentIndex() + 1) % len(guide.MODES)),
            (Qt.Key_Comma, True): self.step_back,
            (Qt.Key_Period, True): self.step_fwd,
            (Qt.Key_BracketLeft, True): lambda: self.jump_shot(-1),
            (Qt.Key_BracketRight, True): lambda: self.jump_shot(1),
            (Qt.Key_PageUp, True): lambda: self.next_clip(-1),
            (Qt.Key_PageDown, True): lambda: self.next_clip(1),
            (Qt.Key_Home, True): lambda: self.seek(0),
            (Qt.Key_End, True): lambda: self.seek(max(self.timeline.duration - 0.05, 0)),
            (Qt.Key_Left, True): lambda: self.nudge(-1),
            (Qt.Key_Right, True): lambda: self.nudge(1),
            (Qt.Key_Delete, True): self.delete_selected,
            (Qt.Key_F2, True): self.edit_note,
            (Qt.Key_Return, True): self.edit_note,
            (Qt.Key_Enter, True): self.edit_note,
        }
        fn = acts.get((k, True)) if none else None
        if shift:
            fn = {Qt.Key_I: lambda: self.set_selected_edge("in"), Qt.Key_O: lambda: self.set_selected_edge("out"),
                  Qt.Key_R: lambda: self.rotate_clip(-90), Qt.Key_S: self.preview_stab,
                  Qt.Key_Left: lambda: self.nudge(-5), Qt.Key_Right: lambda: self.nudge(5)}.get(k)
        if ctrl:
            fn = {Qt.Key_Left: self.step_back, Qt.Key_Right: self.step_fwd, Qt.Key_Z: self.undo,
                  Qt.Key_Y: self.redo, Qt.Key_K: self.split_audio}.get(k)
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) and k == Qt.Key_Z:
            fn = self.redo
        if fn:
            fn()
            return True
        return False

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress and self.isActiveWindow():
            if self.handle_key(e):
                return True
        return super().eventFilter(obj, e)

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split", self.split.saveState())
        if self.md_dirty:
            self.apply_md()
        if self.save_timer.isActive() or self.saved_label.text().startswith("●"):
            self._save()
        self.poll.stop()
        for player in (self.mpv, getattr(self, "amp", None)):
            try:
                player.terminate()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(e)


class MixWorker(QThread):
    done = Signal(str, str, str)  # key, path ('' = no audio clips), error

    def __init__(self, project, out, key):
        super().__init__()
        self.project, self.out, self.key = project, out, key

    def run(self):
        try:
            path = audio.build_mix(self.project, self.out)
            self.done.emit(self.key, str(path) if path else "", "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(self.key, "", str(e))


class EnhanceWorker(QThread):
    progress = Signal(int, str)
    done = Signal(dict, str)  # {raw: cleaned}, error

    def __init__(self, raws, model):
        super().__init__()
        self.raws, self.model, self._cancel = raws, model, False

    def cancel(self):
        self._cancel = True

    def run(self):
        out, n = {}, len(self.raws)
        try:
            for i, raw in enumerate(self.raws):
                name = Path(raw).name

                def line(t, i=i, name=name):
                    if t.startswith("STEP "):
                        a, b = t[5:].split(" ", 1)[0].split("/")
                        self.progress.emit(i * 10 + int(9 * int(a) / int(b)),
                                           f"Take {i + 1}/{n} · {name}\n{t.split(' ', 2)[2]}")
                    elif "downloading" in t.lower():
                        self.progress.emit(i * 10, f"Take {i + 1}/{n} · {name}\nDownloading the model (first time)…")
                self.progress.emit(i * 10, f"Take {i + 1}/{n} · {name}")
                out[raw] = enhance.enhance(raw, self.model, line, lambda: self._cancel)
            self.progress.emit(n * 10, "Done")
            self.done.emit(out, "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(out, str(e))


class ExportWorker(QThread):
    progress = Signal(int, int, str)
    done = Signal(bool, str)

    def __init__(self, project, fmt, fps, out):
        super().__init__()
        self.project, self.fmt, self.fps, self.out = project, fmt, fps, out
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            out = exporter.export(self.project, self.fmt, Path(self.out), self.fps,
                                  progress=lambda d, t, m: self.progress.emit(d, t, m),
                                  cancelled=lambda: self._cancel)
            self.done.emit(True, str(out))
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))


class StabPreviewWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, src, shot, rotate, cache_dir, n):
        super().__init__()
        self.src, self.shot, self.rotate, self.cache_dir, self.n = src, shot, rotate, Path(cache_dir), n

    def run(self):
        try:
            s = self.shot
            trf = stabilize.analyze(self.src, s.t_in, s.t_out, self.rotate, self.cache_dir)
            out = self.cache_dir / f"preview_shot{self.n}.mp4"
            vf = (f"[0:v]{stabilize.ROTATE[self.rotate]}split[a][b];"
                  f"[b]{stabilize.filter_for(trf, stabilize.zoom_of(s.extra))}scale=-2:720[s];[a]scale=-2:720[o];"
                  "[o][s]hstack,format=yuv420p")
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s.t_in:.3f}", "-t", f"{s.src_len:.3f}",
                                "-i", str(Path(self.src).resolve()), "-filter_complex", vf, "-an",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out.name],
                               cwd=trf.parent, capture_output=True, text=True, creationflags=exporter.NO_WINDOW)
            if r.returncode:
                raise RuntimeError(r.stderr[-600:])
            self.done.emit(True, str(out))
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))


class ExportDialog(QDialog):
    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        p = win.project
        self.setWindowTitle("Export video")
        self.setMinimumWidth(560)
        form = QFormLayout(self)
        self.fmt = QComboBox()
        self.fmt.addItem("16:9 landscape · 1920×1080 (YouTube)", "16:9")
        self.fmt.addItem("9:16 vertical · 1080×1920 (Shorts / TikTok)", "9:16")
        want = win.settings.value("export_fmt", "")
        if not want:
            want = "9:16" if guide.MODES[win.guide_box.currentIndex()][0] == "9:16" else "16:9"
        self.fmt.setCurrentIndex(self.fmt.findData(want))
        self.fps = QComboBox()
        for label, v in (("Auto (match the footage)", "auto"), ("30 fps", "30"), ("60 fps", "60")):
            self.fps.addItem(label, v)
        self.fps.setCurrentIndex(max(0, self.fps.findData(win.settings.value("export_fps", "auto"))))
        self.path = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        form.addRow("Format", self.fmt)
        form.addRow("Frame rate", self.fps)
        form.addRow("Save as", row)
        info = QLabel(f"{len(p.all_shots())} shots · {tc(p.total())}, in list order. Straight cuts: the crop "
                      "matches the frame guide; slow-mo/fast shots keep their speed. Each shot's game sound uses its "
                      "level or mute, and the voice and music lanes are mixed in. Notes and title cards "
                      "are not added.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#9aa1a9")
        form.addRow(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.fmt.currentIndexChanged.connect(self._default_path)
        self._default_path()

    def _folder(self) -> Path:
        p = self.win.project
        base = p.path.parent if p.path else PROJECTS_DIR
        if base.name == "analysis" and (base.parent / "output").is_dir():
            base = base.parent / "output"  # Korea-style project: renders live in output/
        return base

    def _default_path(self):
        cur = self.path.text()
        if cur and not getattr(self, "_auto", False):
            return
        tag = "16x9" if self.fmt.currentData() == "16:9" else "9x16"
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in self.win.project.title).strip() or "export"
        self.path.setText(str(self._folder() / f"{safe} - {tag} - {time.strftime('%Y%m%d-%H%M')}.mp4"))
        self._auto = True

    def _browse(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save video as", self.path.text(), "MP4 video (*.mp4)")
        if f:
            self.path.setText(f if f.lower().endswith(".mp4") else f + ".mp4")
            self._auto = False

    def values(self):
        return self.fmt.currentData(), self.fps.currentData(), self.path.text()


def dark(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    for role, c in ((QPalette.Window, "#1b1d20"), (QPalette.WindowText, "#e3e5e8"), (QPalette.Base, "#141618"),
                    (QPalette.AlternateBase, "#1f2226"), (QPalette.Text, "#e3e5e8"), (QPalette.Button, "#2a2d32"),
                    (QPalette.ButtonText, "#e3e5e8"), (QPalette.Highlight, "#3d6fb6"),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#2a2d32"),
                    (QPalette.ToolTipText, "#e3e5e8")):
        p.setColor(role, QColor(c))
    app.setPalette(p)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "--import-korea":
        p = importers.import_korea()
        print(f"wrote {p.path} ({len(p.all_shots())} shots, {tc(p.total())})")
        return
    app = QApplication(sys.argv[:1])
    locale.setlocale(locale.LC_NUMERIC, "C")  # libmpv needs C numeric locale
    dark(app)
    w = MainWindow(argv[0] if argv else None)
    app.installEventFilter(w)
    if w.settings.value("geometry"):
        w.restoreGeometry(w.settings.value("geometry"))
        if w.settings.value("split"):
            w.split.restoreState(w.settings.value("split"))
        w.show()
    else:
        w.showMaximized()
    app.exec()
