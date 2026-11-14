"""Audio lanes under the player: the whole cut on one time scale, with three rows.

  Game   one block per shot: its own sound. Dark = muted, else its level in dB.
  Voice  voice-over clips. Drag the middle to move, drag the right edge to lengthen or shorten
         (everything after slides along), drag the left edge to let in more or less of the take.
  Music  backing-track clips, same handles, with their fades drawn as ramps.

Wheel scrolls, Ctrl+wheel zooms around the mouse, double-click opens a clip in the player,
right-click gives the clip's menu (level, fades, mute, delete...). Clicking empty space plays the
shot list from there.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .model import Project, shot_gain, tc
from .timeline import colour

ROWS = ("game", "voice", "music")
LABELS = {"game": "Game", "voice": "Voice", "music": "Music"}
LABEL_W = 52
RULER_H = 16
ROW_H = 24
EDGE_PX = 6
FILL = {"voice": QColor("#3fa7a0"), "music": QColor("#8f7ae6")}


class AudioLanes(QWidget):
    seekRequested = Signal(float)                 # cut seconds
    clipSelected = Signal(str, int)               # row, uid
    clipOpened = Signal(str, int)                 # row, uid (double-click)
    contextRequested = Signal(str, int, object)   # row, uid (or -1 for empty), global QPoint
    clipMoved = Signal(int, float, bool)          # uid, new start, finished
    clipTrimmed = Signal(int, str, float, bool)   # uid, 'in'|'out', new source time, finished

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(RULER_H + ROW_H * len(ROWS) + 6)
        self.setMouseTracking(True)
        self.project: Project | None = None
        self.pos = 0.0
        self.selected: int | None = None
        self.view0 = 0.0         # seconds at the left edge
        self.span = 0.0          # seconds across the width (0 = fit)
        self._drag = None

    # ---- geometry ----------------------------------------------------------
    def total(self) -> float:
        if not self.project:
            return 0.0
        ends = [self.project.total()] + [c.end for _, c in self.project.audio_clips()]
        return max(ends) or 1.0

    def _span(self):
        return self.span if self.span > 0 else self.total() * 1.01

    def _w(self):
        return max(self.width() - LABEL_W - 6, 10)

    def x_of(self, t):
        return LABEL_W + (t - self.view0) / self._span() * self._w()

    def t_of(self, x):
        return self.view0 + (x - LABEL_W) / self._w() * self._span()

    def row_rect(self, row) -> QRectF:
        i = ROWS.index(row)
        return QRectF(LABEL_W, RULER_H + i * ROW_H + 1, self._w(), ROW_H - 2)

    def row_at(self, y):
        i = int((y - RULER_H) // ROW_H)
        return ROWS[i] if 0 <= i < len(ROWS) and y >= RULER_H else None

    def ensure_visible(self, t):
        if self.span <= 0:
            return
        if t < self.view0 or t > self.view0 + self.span * 0.95:
            self.view0 = max(0.0, t - self.span * 0.2)

    # ---- items -------------------------------------------------------------
    def items(self, row):
        """[(uid, start, end, payload)] for a row."""
        p = self.project
        if not p:
            return []
        if row == "game":
            return [(s.uid, t, t + s.dur, s) for s, t in p.timeline()]
        return [(c.uid, c.start, c.end, c) for c in p.lane(row)]

    def hit(self, x, y):
        row = self.row_at(y)
        if row is None or x < LABEL_W:
            return None
        t = self.t_of(x)
        if row != "game":
            for uid, a, b, c in reversed(self.items(row)):
                if abs(self.x_of(b) - x) <= EDGE_PX and self.x_of(b) - self.x_of(a) > 2 * EDGE_PX:
                    return row, uid, "out", c
                if abs(self.x_of(a) - x) <= EDGE_PX and self.x_of(b) - self.x_of(a) > 2 * EDGE_PX:
                    return row, uid, "in", c
        for uid, a, b, c in reversed(self.items(row)):
            if a <= t < b:
                return row, uid, "body", c
        return row, None, None, None

    # ---- painting ----------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#16181b"))
        small = QFont(self.font())
        small.setPointSizeF(7.5)
        p.setFont(small)
        for row in ROWS:
            r = self.row_rect(row)
            p.fillRect(r, QColor("#22252a"))
            p.setPen(QColor("#9aa1a9"))
            p.drawText(QRectF(4, r.top(), LABEL_W - 6, r.height()), Qt.AlignVCenter | Qt.AlignLeft, LABELS[row])
        if not self.project or not self.project.all_shots():
            return
        p.setClipRect(QRectF(LABEL_W, 0, self._w() + 2, self.height()))
        # ruler
        span = self._span()
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if self._w() / (span / s) >= 55), 600)
        t = (int(self.view0 // step)) * step
        p.setPen(QColor("#6b7078"))
        while t <= self.view0 + span:
            x = self.x_of(t)
            p.drawLine(QPointF(x, RULER_H - 4), QPointF(x, RULER_H))
            p.drawText(QPointF(x + 2, RULER_H - 5), tc(t)[:-2])
            t += step
        # game row
        r = self.row_rect("game")
        n = 0
        for uid, a, b, s in self.items("game"):
            n += 1
            x0, x1 = self.x_of(a), self.x_of(b)
            if x1 < LABEL_W or x0 > self.width():
                continue
            box = QRectF(x0, r.top(), max(x1 - x0, 1), r.height())
            g = shot_gain(s)
            c = colour(n)
            if g is None:
                c = QColor("#34373d")
            else:
                c.setAlpha(110 + int(140 * min(1.0, 10 ** (g / 20))))
            p.fillRect(box, c)
            if uid == self.selected:
                p.setPen(QPen(QColor("white"), 2))
                p.drawRect(box)
            if box.width() > 26:
                p.setPen(QColor("#e8e8e8") if g is None else QColor("#101214"))
                p.drawText(box.adjusted(3, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           f"{n} " + ("M" if g is None else f"{g:+.0f}"))
        # voice / music rows
        for row in ("voice", "music"):
            r = self.row_rect(row)
            for uid, a, b, c in self.items(row):
                x0, x1 = self.x_of(a), self.x_of(b)
                if x1 < LABEL_W or x0 > self.width():
                    continue
                box = QRectF(x0, r.top(), max(x1 - x0, 1), r.height())
                col = QColor(FILL[row])
                if c.gain_db <= -60:
                    col = QColor("#34373d")
                p.fillRect(box, col)
                if c.fade_in > 0 or c.fade_out > 0:
                    path = QPainterPath()
                    xi = self.x_of(a + min(c.fade_in, c.dur))
                    xo = self.x_of(b - min(c.fade_out, c.dur))
                    path.moveTo(x0, box.bottom())
                    path.lineTo(xi, box.top())
                    path.lineTo(xo, box.top())
                    path.lineTo(x1, box.bottom())
                    p.setPen(QPen(QColor(255, 255, 255, 170), 1))
                    p.drawPath(path)
                p.setPen(QPen(QColor("#101214"), 1))
                p.drawLine(QPointF(x0, box.top()), QPointF(x0, box.bottom()))
                if uid == self.selected:
                    p.setPen(QPen(QColor("white"), 2))
                    p.drawRect(box)
                if box.width() > 30:
                    p.setPen(QColor("#0d0f11"))
                    label = (c.note or "").splitlines()[0] if c.note else ""
                    if c.gain_db:
                        label = f"{c.gain_db:+.0f}dB {label}"
                    p.drawText(box.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
        # playhead
        x = self.x_of(self.pos)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    # ---- mouse -------------------------------------------------------------
    def wheelEvent(self, e):
        tot = self.total()
        span = self._span()
        dy = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            anchor = self.t_of(e.position().x())
            span = min(max(span * (0.8 if dy > 0 else 1.25), 3.0), tot * 1.01)
            frac = (anchor - self.view0) / self._span()
            self.span = 0.0 if span >= tot else span
            self.view0 = max(0.0, anchor - frac * span) if self.span else 0.0
        elif self.span:
            self.view0 = min(max(0.0, self.view0 - dy / 120 * span * 0.1), max(tot - span * 0.5, 0))
        self.update()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._drag is None:
            h = self.hit(x, y)
            edge = h and h[2] in ("in", "out")
            self.setCursor(Qt.SizeHorCursor if edge else Qt.ArrowCursor)
            return
        kind, uid, t0, ref = self._drag
        dt = self.t_of(x) - t0
        if kind == "body":
            self.clipMoved.emit(uid, max(0.0, ref + dt), False)
        elif kind in ("in", "out"):
            self.clipTrimmed.emit(uid, kind, ref + dt, False)

    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        h = self.hit(x, y)
        if e.button() == Qt.RightButton:
            row = h[0] if h else self.row_at(y)
            if row:
                self.contextRequested.emit(row, h[1] if h and h[1] is not None else -1, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        if not h or h[1] is None:
            if x >= LABEL_W:
                self.seekRequested.emit(max(0.0, self.t_of(x)))
            return
        row, uid, part, c = h
        self.selected = uid
        self.clipSelected.emit(row, uid)
        if row != "game":
            if part == "body":
                self._drag = ("body", uid, self.t_of(x), c.start)
            elif part == "in":
                self._drag = ("in", uid, self.t_of(x), c.t_in)
            else:
                self._drag = ("out", uid, self.t_of(x), c.t_out)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        kind, uid, t0, ref = self._drag
        dt = self.t_of(e.position().x()) - t0
        self._drag = None
        if abs(dt) < 1e-3:
            return
        if kind == "body":
            self.clipMoved.emit(uid, max(0.0, ref + dt), True)
        else:
            self.clipTrimmed.emit(uid, kind, ref + dt, True)

    def mouseDoubleClickEvent(self, e):
        h = self.hit(e.position().x(), e.position().y())
        if h and h[1] is not None:
            self._drag = None
            self.clipOpened.emit(h[0], h[1])
