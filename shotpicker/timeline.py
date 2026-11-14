"""Playbar: clip mode shows the current clip with its marked shots; cut mode shows the whole shot list."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .model import tc

PALETTE = ["#e8735a", "#4fb3a9", "#e0b84f", "#8f7ae6", "#5aa0e8", "#d46fa8", "#8cc265", "#e89a4f"]
EDGE_PX = 6


def colour(n: int) -> QColor:
    return QColor(PALETTE[(n - 1) % len(PALETTE)])


class Timeline(QWidget):
    seekRequested = Signal(float, bool)          # seconds, exact
    shotClicked = Signal(int)                    # uid
    edgeDragged = Signal(int, str, float, bool)  # uid, 'in'|'out', seconds, finished
    cutClicked = Signal(int, float)              # shot index in list, offset seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(64)
        self.setMouseTracking(True)
        self.mode = "clip"
        self.duration = 0.0
        self.pos = 0.0
        self.shots = []          # clip mode: [(uid, number, t_in, t_out)]
        self.cut = []            # cut mode: [(uid, number, start, dur, section_start_name|None)]
        self.selected = None
        self.pending_in = None
        self._drag = None

    # ---- geometry ----------------------------------------------------------
    def _bar(self) -> QRectF:
        return QRectF(8, 18, self.width() - 16, self.height() - 30)

    def x_of(self, t):
        b = self._bar()
        return b.left() + (t / self.duration) * b.width() if self.duration > 0 else b.left()

    def t_of(self, x):
        b = self._bar()
        if self.duration <= 0:
            return 0.0
        return min(max((x - b.left()) / b.width(), 0.0), 1.0) * self.duration

    # ---- painting ----------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#16181b"))
        b = self._bar()
        p.fillRect(b, QColor("#2a2d32"))
        if self.duration <= 0:
            return
        small = QFont(self.font())
        small.setPointSizeF(7.5)
        p.setFont(small)
        # ticks
        step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600)
                    if b.width() / (self.duration / s) >= 60) if self.duration else 60
        p.setPen(QColor("#6b7078"))
        t = 0.0
        while t <= self.duration + 1e-6:
            x = self.x_of(t)
            p.drawLine(QPointF(x, b.bottom()), QPointF(x, b.bottom() + 3))
            p.drawText(QPointF(x + 2, self.height() - 1), tc(t)[:-2])
            t += step
        if self.mode == "clip":
            for uid, n, a, z in self.shots:
                r = QRectF(self.x_of(a), b.top(), max(self.x_of(z) - self.x_of(a), 2), b.height())
                c = colour(n)
                c.setAlpha(255 if uid == self.selected else 175)
                p.fillRect(r, c)
                if uid == self.selected:
                    p.setPen(QPen(QColor("white"), 2))
                    p.drawRect(r)
                p.setPen(QColor("#101214"))
                if r.width() > 14:
                    p.drawText(r.adjusted(3, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, str(n))
            if self.pending_in is not None:
                a, z = sorted((self.pending_in, self.pos))
                r = QRectF(self.x_of(a), b.top(), self.x_of(z) - self.x_of(a), b.height())
                p.fillRect(r, QColor(255, 255, 255, 50))
                p.setPen(QPen(QColor("#7CFC9A"), 2))
                x = self.x_of(self.pending_in)
                p.drawLine(QPointF(x, b.top() - 6), QPointF(x, b.bottom()))
                p.drawText(QPointF(x + 3, b.top() - 6), "IN")
        else:
            label_end = -1e9
            for uid, n, a, d, secname in self.cut:
                r = QRectF(self.x_of(a), b.top(), max(self.x_of(a + d) - self.x_of(a), 1), b.height())
                c = colour(n)
                c.setAlpha(255 if uid == self.selected else 140)
                p.fillRect(r, c)
                if secname is not None:
                    p.setPen(QPen(QColor("white"), 1))
                    p.drawLine(QPointF(r.left(), b.top() - 8), QPointF(r.left(), b.bottom()))
                    if r.left() > label_end:
                        p.drawText(QPointF(r.left() + 3, b.top() - 7), secname[:22])
                        label_end = r.left() + p.fontMetrics().horizontalAdvance(secname[:22]) + 8
        x = self.x_of(self.pos)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, 4), QPointF(x, b.bottom() + 2))

    # ---- mouse -------------------------------------------------------------
    def _edge_at(self, x):
        for uid, n, a, z in self.shots:
            for which, t in (("in", a), ("out", z)):
                if abs(self.x_of(t) - x) <= EDGE_PX:
                    return uid, which
        return None

    def _shot_at(self, x):
        t = self.t_of(x)
        hits = [s for s in self.shots if s[2] <= t <= s[3]]
        return hits[-1][0] if hits else None

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag is None:
            near = self.mode == "clip" and self._edge_at(x)
            self.setCursor(Qt.SizeHorCursor if near else Qt.ArrowCursor)
            return
        t = self.t_of(x)
        if self._drag[0] == "edge":
            self.edgeDragged.emit(self._drag[1], self._drag[2], t, False)
        elif self.mode == "clip":
            self.pos = t
            self.update()
            self.seekRequested.emit(t, False)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.duration <= 0:
            return
        x = e.position().x()
        t = self.t_of(x)
        if self.mode == "cut":
            for i, (uid, n, a, d, _) in enumerate(self.cut):
                if a <= t < a + d or i == len(self.cut) - 1:
                    self.cutClicked.emit(i, t - a)
                    return
            return
        edge = self._edge_at(x)
        if edge:
            self._drag = ("edge", *edge)
            return
        uid = self._shot_at(x)
        if uid is not None:
            self.shotClicked.emit(uid)
        self._drag = ("seek",)
        self.pos = t
        self.update()
        self.seekRequested.emit(t, False)

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        t = self.t_of(e.position().x())
        if self._drag[0] == "edge":
            self.edgeDragged.emit(self._drag[1], self._drag[2], t, True)
        else:
            self.seekRequested.emit(t, True)
        self._drag = None
