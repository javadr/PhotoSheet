"""Main window: layout controls, image list with per-image effects, live preview, export, print."""
from __future__ import annotations

import copy
import math
import datetime
import os
import sys
import threading
import random

from PIL import Image
from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QSize, qVersion, QEvent, QObject, QPointF, QRectF, QRunnable, QSettings, QSizeF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel, QAction, QColor, QImage, QKeySequence, QMouseEvent, QPageSize, QPainter, QPalette, QPen, QPolygonF, QFont, QPixmap, QUndoCommand, QUndoStack
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QStyledItemDelegate, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QFrame, QGraphicsDropShadowEffect, QSlider, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from .. import __version__
from ..core import preset
from ..core.geometry import PageExtent, Rect, crop_overflow
from ..core.layouts import (BADGE_SIZES, BENTO, MM, MONTHS, RECIPES, SHAPE_NAMES, Badges, Bento, Brick, Calendar, Carousel, Checker, Collage, Diamond, Pyramid,
                            Fan, Feature, Filmstrip, FixedSize, FramedPrint, Honeycomb, ImageItem, Masonry, Mosaic,
                            NUp, Package, Passport, Polaroid, Poster, Ring, Scatter, Shapes, Single, Slant, Spiral, Stairs,
                            Strip, Timeline, Triptych, aspect, date_sort_key, snap_delta)
from ..core.render import Cancelled, render_page, save_pages, uncached

PAGES = {"A4": (595, 842), "Letter": (612, 792), "A5": (420, 595),
         "4 × 6 in": (288, 432), "5 × 7 in": (360, 504), "8 × 10 in": (576, 720)}   # portrait points; Orientation turns them
KINDS = {"N-up": NUp, "Single": Single, "Poster": Poster, "Carousel": Carousel,
         "Fixed size": FixedSize, "Passport": Passport, "Mosaic": Mosaic, "Photo strip": Strip, "Polaroid": Polaroid,
         "Triptych": Triptych, "Package": Package, "Collage": Collage, "Framed print": FramedPrint,
         "Badges": Badges, "Feature": Feature, "Film strip": Filmstrip, "Bento": Bento,
         "Golden spiral": Spiral, "Honeycomb": Honeycomb, "Calendar": Calendar, "Masonry": Masonry,
         "Shapes": Shapes, "Ring": Ring, "Scatter": Scatter, "Slant": Slant, "Fan": Fan, "Brick": Brick,
         "Timeline": Timeline, "Checker": Checker, "Stairs": Stairs,
         "Pyramid": Pyramid, "Diamond": Diamond}
KEYS = {"N-up": "nup", "Single": "single", "Poster": "poster", "Carousel": "carousel",
        "Fixed size": "fixed", "Passport": "passport", "Mosaic": "mosaic", "Photo strip": "strip", "Polaroid": "polaroid",
        "Triptych": "triptych", "Package": "package", "Collage": "collage", "Framed print": "framed",
        "Badges": "badges", "Feature": "feature", "Film strip": "filmstrip", "Bento": "bento",
        "Golden spiral": "spiral", "Honeycomb": "honeycomb", "Calendar": "calendar", "Masonry": "masonry",
        "Shapes": "shapes", "Ring": "ring", "Scatter": "scatter", "Slant": "slant", "Fan": "fan", "Brick": "brick",
        "Timeline": "timeline", "Checker": "checker", "Stairs": "stairs",
        "Pyramid": "pyramid", "Diamond": "diamond"}
# The layout menu is long, so it is grouped (HIG: group related items, use progressive disclosure).
KIND_GROUPS = (
    ("Grids", ("N-up", "Single", "Mosaic", "Masonry", "Brick", "Checker", "Pyramid", "Diamond", "Bento", "Feature")),
    ("Print sizes", ("Fixed size", "Passport", "Package", "Badges", "Photo strip", "Film strip")),
    ("Frames", ("Polaroid", "Framed print", "Triptych", "Poster")),
    ("Shapes", ("Shapes", "Honeycomb", "Ring", "Carousel", "Golden spiral")),
    ("Creative", ("Collage", "Scatter", "Fan", "Slant", "Stairs", "Timeline", "Calendar")),
)
assert sorted(k for _, ks in KIND_GROUPS for k in ks) == sorted(KINDS)
SHAPE_LABELS = {"Heart": "heart", "Star": "star", "Diamond": "diamond", "Triangle": "triangle",
                "Rounded square": "rounded", "Circle": "circle"}
assert set(SHAPE_LABELS.values()) == set(SHAPE_NAMES)
HANDLE = 5   # half the size of a Collage resize handle, in px
# The grid each layout starts with: (rows, columns). Masonry only uses the columns.
# Layouts that start on landscape paper (every other layout starts on portrait) until the user picks an orientation.
ORIENT_DEFAULTS = {"Brick": "Landscape"}
GRID_DEFAULTS = {"N-up": (3, 3), "Polaroid": (3, 3), "Shapes": (3, 2), "Masonry": (3, 3), "Brick": (5, 5), "Checker": (4, 4), "Pyramid": (4, 4), "Diamond": (5, 5)}
DIRECTIONS = {"Automatic": "auto", "Vertical": "vertical", "Horizontal": "horizontal"}   # which way strips run
SIZE_PRESETS = {"Passport 35 × 45 mm": (35, 45), "US passport 2 × 2 in": (50.8, 50.8),
                "Wallet 2.5 × 3.5 in": (63.5, 88.9), "4 × 6 in": (101.6, 152.4),
                "5 × 7 in": (127, 177.8), "ID 30 × 40 mm": (30, 40), "China visa 33 × 48 mm": (33, 48),
                "Canada passport 50 × 70 mm": (50, 70), "Custom": None}
IMG_FILTER = "Images (*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp);;All files (*)"


MIN_FONT_PT, MAX_FONT_PT = 10.0, 24.0  # HIG: macOS minimum text size is 10 pt


def restore_text_size() -> None:
    """Apply the saved text size, never below the platform minimum."""
    app = QApplication.instance()
    f = app.font()
    f.setPointSizeF(max(MIN_FONT_PT, float(QSettings("PhotoSheet", "PhotoSheet").value("fontPt", f.pointSizeF()))))
    app.setFont(f)


def change_text_size(delta: float, persist: bool = True) -> None:
    app = QApplication.instance()
    f = app.font()
    f.setPointSizeF(max(MIN_FONT_PT, min(MAX_FONT_PT, f.pointSizeF() + delta)))
    app.setFont(f)
    if persist:
        QSettings("PhotoSheet", "PhotoSheet").setValue("fontPt", f.pointSizeF())


IMG_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def dropped_images(mime) -> list[str]:
    """Local image files in a drag, in order. Anything else is ignored."""
    if not mime.hasUrls():
        return []
    return [u.toLocalFile() for u in mime.urls()
            if u.isLocalFile() and u.toLocalFile().lower().endswith(tuple(IMG_SUFFIXES))]


class _NumberedDelegate(QStyledItemDelegate):
    """Shows each row's position before its file name; the item text itself stays the bare file name."""
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.model().rowCount() > 1:   # a lone photo needs no number
            option.text = f"{index.row() + 1}.  {option.text}"


class _ImageList(QListWidget):
    """List that reorders internally but also takes image files dragged in from a file manager."""
    files_dropped = Signal(list)

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if dropped_images(e.mimeData()) else super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        e.acceptProposedAction() if dropped_images(e.mimeData()) else super().dragMoveEvent(e)

    def dropEvent(self, e):
        files = dropped_images(e.mimeData())
        if files:
            e.acceptProposedAction()
            self.files_dropped.emit(files)
        else:
            super().dropEvent(e)


def desk_color(window: QColor) -> QColor:
    """Backdrop behind the paper: a step away from the window colour so the white page's edge shows.

    Derived from the Window colour because themes can't be trusted: this desktop sets palette Mid to pure white.
    """
    return window.darker(122) if window.lightness() > 128 else window.lighter(170)


def to_qimage(im: Image.Image) -> QImage:
    im = im.convert("RGB")
    return QImage(im.tobytes(), im.width, im.height, 3 * im.width, QImage.Format.Format_RGB888).copy()


class _Edit(QUndoCommand):
    """Undoable change to the image list. State is deep-copied so effect tweaks undo too."""

    def __init__(self, win, text, before, after, merge=False):
        super().__init__(text)
        self.win, self.before, self.after, self.merge, self._applied = win, before, after, merge, True

    def id(self):
        return 1 if self.merge else -1  # consecutive tweaks of one control collapse into one undo step

    def mergeWith(self, other):
        self.after = other.after
        return True

    def redo(self):
        if self._applied:  # the first redo happens when pushed; the change is already made
            self._applied = False
            return
        self.win._set_images(self.after)

    def undo(self):
        self.win._set_images(self.before)


class _Signals(QObject):
    done = Signal(int, object)   # generation, QImage or Exception
    progress = Signal(int, int, int)   # generation, photos finished, photos on the page
    export_progress = Signal(int, str)   # progress in 1/1000, what is happening
    export_done = Signal(object)   # None, or the Exception


class _Job(QRunnable):
    def __init__(self, gen, layout, page, dpi, sig, current):
        super().__init__()
        self.gen, self.layout, self.page, self.dpi, self.sig = gen, layout, page, dpi, sig
        self.current = current

    def run(self):
        if not self.current():
            return  # superseded while queued: don't burn CPU on it
        try:
            res = to_qimage(render_page(self.layout, self.page, self.dpi, fast=True,
                                        progress=lambda d, t: self.sig.progress.emit(self.gen, d, t)))
        except Exception as e:  # shown in the status bar; never crash the worker
            res = e
        try:
            self.sig.done.emit(self.gen, res)
        except RuntimeError:
            pass  # window closed while rendering


class _ExportJob(QRunnable):
    """Renders every page at 300 dpi while the user picks a file name, then saves once the path is known.
    shortcut: all pages are held in memory until saved (~26 MB per A4 page); stream to disk for very long documents."""

    def __init__(self, layout, sig):
        super().__init__()
        self.layout, self.sig = layout, sig
        self.path = None
        self.cancelled = False
        self.ready = threading.Event()   # set once the user has chosen a path, or cancelled

    def choose(self, path):
        self.path = path
        self.ready.set()

    def cancel(self):
        self.cancelled = True
        self.ready.set()

    def run(self):
        try:
            n = self.layout.pages
            pages = []
            def report(i, f):   # f = 0..1 through page i; rendering is the first 92% of the bar, saving the rest
                self.sig.export_progress.emit(int(920 * (i + f) / n), f"Page {i + 1} of {n}")

            for i in range(n):
                if self.cancelled:
                    return
                # a page is photos loading (60%) then photos being drawn (40%)
                pages.append(render_page(
                    self.layout, i, 300, cancel=lambda: self.cancelled,
                    progress=lambda d, t, i=i: report(i, 0.6 * d / max(t, 1)),
                    placing=lambda d, t, i=i: report(i, 0.6 + 0.4 * d / max(t, 1))))
                report(i, 1)
            self.ready.wait()
            if self.cancelled:
                return
            self.sig.export_progress.emit(920, "Saving")
            f = self.path
            save_pages(pages, f, 300, after=lambda i: self.sig.export_progress.emit(920 + 80 * (i + 1) // n, "Saving"))
            res = None
        except Cancelled:
            return
        except Exception as e:
            res = e
        try:
            self.sig.export_done.emit(res)
        except RuntimeError:
            pass  # window closed meanwhile


class _GroupedDelegate(QStyledItemDelegate):
    """Popup rows: unselectable headings get a tinted band and a rule above (not the first); layouts are indented under them."""
    PAD, INDENT = 8, 14

    @staticmethod
    def _is_heading(index) -> bool:
        return not (index.flags() & Qt.ItemFlag.ItemIsEnabled)

    def sizeHint(self, option, index):
        sz = super().sizeHint(option, index)
        if self._is_heading(index):
            sz.setHeight(sz.height() + (self.PAD if index.row() else 2))   # air above a new group
        return sz

    def paint(self, painter, option, index):
        if not self._is_heading(index):
            option.rect.adjust(self.INDENT, 0, 0, 0)
            return super().paint(painter, option, index)
        pal, r = option.palette, option.rect
        ink, paper = pal.color(QPalette.ColorRole.WindowText), pal.color(QPalette.ColorRole.Window)
        mix = lambda t: QColor(*(round(a * t + b * (1 - t)) for a, b in zip(ink.getRgb()[:3], paper.getRgb()[:3])))
        painter.save()
        top = r.top() + (self.PAD if index.row() else 0)
        if index.row():
            painter.setPen(QPen(mix(0.35)))
            painter.drawLine(r.left(), r.top() + 2, r.right(), r.top() + 2)    # the separator between groups
        painter.fillRect(r.adjusted(0, top - r.top(), 0, 0), mix(0.10))
        font = QFont(option.font)
        font.setBold(True)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
        painter.setFont(font)
        painter.setPen(mix(0.85))                                            # secondary ink, about 7:1 on the band
        painter.drawText(r.adjusted(6, top - r.top(), -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         index.data())
        painter.restore()


class SliderSpin(QWidget):
    """A slider with the exact value beside it in a number box (HIG: sliders: supplement with a text field and
    stepper). The minimum is on the leading side, the preview updates while dragging, and typing a value works too."""
    valueChanged = Signal(int)

    def __init__(self, lo: int, hi: int, value: int, suffix: str = ""):
        super().__init__()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setMinimumWidth(70)
        self.spin = QSpinBox(minimum=lo, maximum=hi, suffix=suffix)
        self.spin.setValue(value)
        self.slider.setValue(value)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self._from_spin)

    def _from_spin(self, v: int) -> None:
        if self.slider.value() != v:
            self.slider.setValue(v)
        self.valueChanged.emit(v)

    def value(self) -> int:
        return self.spin.value()

    def setValue(self, v: int) -> None:
        self.spin.setValue(v)

    def setToolTip(self, text: str) -> None:
        super().setToolTip(text)
        self.slider.setToolTip(text)
        self.spin.setToolTip(text)

    def set_label(self, label: str) -> None:
        """What a screen reader announces for the two controls."""
        self.slider.setAccessibleName(f"{label} slider")
        self.spin.setAccessibleName(label)


class _Edge(QFrame):
    """A one-pixel line that marks the edge of a scrolling area: only shown while there is more content that way."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(1)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Mid)   # paints with Mid, but the colour is worked out from Window
        self.hide()
        self._recolor()

    def _recolor(self):
        want = desk_color(self.palette().color(QPalette.ColorRole.Window))
        if self.palette().color(QPalette.ColorRole.Mid) != want:   # setPalette fires PaletteChange again: stop there
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.Mid, want)
            self.setPalette(pal)

    def changeEvent(self, ev):
        if ev.type() == QEvent.Type.PaletteChange:
            self._recolor()
        super().changeEvent(ev)


class ScrollPane(QWidget):
    """Content that scrolls vertically when its panel is too short for it (HIG scroll views: support the usual
    scrolling, and make it apparent when content continues). A thin line shows at the top and bottom edge only
    while there is more content in that direction. Focus moving to a control scrolls it into view."""

    def __init__(self, content: QWidget, min_height: int = 110):
        super().__init__()
        self.content = content
        self.area = QScrollArea()
        self.area.setWidget(content)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setMinimumHeight(min_height)
        self.top, self.bottom = _Edge(), _Edge()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.top)
        lay.addWidget(self.area, 1)
        lay.addWidget(self.bottom)
        bar = self.area.verticalScrollBar()
        bar.valueChanged.connect(self._edges)
        bar.rangeChanged.connect(self._edges)
        # Qt scrolls to a control on Tab only; assistive technology and voice control move focus directly.
        QApplication.instance().focusChanged.connect(self._reveal)

    def _reveal(self, _old, new):
        if new is not None and self.content.isAncestorOf(new):
            self.area.ensureWidgetVisible(new, 0, 12)

    def _edges(self, *_):
        bar = self.area.verticalScrollBar()
        self.top.setVisible(bar.value() > bar.minimum())
        self.bottom.setVisible(bar.value() < bar.maximum())

    def fit_width(self):
        """Wide enough for the content and a vertical scroll bar, so the bar never covers a control."""
        lay = self.content.layout()
        if lay:
            lay.invalidate()
            lay.activate()
        self.area.setMinimumWidth(self.content.sizeHint().width() + self.area.verticalScrollBar().sizeHint().width() + 4)

    def scroll_to_top(self):
        self.area.verticalScrollBar().setValue(0)


class _LabelGroup:
    """Gives every form label in a group the width of the widest, so separate forms (the pinned and the scrolling
    part of a panel) line their fields up, and the column does not change width when the layout does."""

    def __init__(self):
        self.labels: list[QLabel] = []

    def width(self) -> int:
        return max((QLabel.sizeHint(l).width() for l in self.labels), default=0)

    def label(self, text: str) -> QLabel:
        lab = _GroupLabel(text, self)
        self.labels.append(lab)
        return lab


class _GroupLabel(QLabel):
    def __init__(self, text: str, group: _LabelGroup):
        super().__init__(text)
        self._group = group

    def sizeHint(self) -> QSize:
        s = super().sizeHint()
        return QSize(max(s.width(), self._group.width()), s.height())

    def minimumSizeHint(self) -> QSize:
        s = super().minimumSizeHint()
        return QSize(max(s.width(), self._group.width()), s.height())


class _PageLabel(QLabel):
    """Page preview that paints drag-and-drop feedback over the rendered page."""

    def __init__(self):
        super().__init__(alignment=Qt.AlignmentFlag.AlignCenter)
        self.overlay: list[tuple[QRectF | QPolygonF, str]] = []   # ("src" dims the picked photo, "dst" outlines the target)
        self.setMouseTracking(True)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.overlay:
            return
        p = QPainter(self)
        hl = self.palette().color(QPalette.ColorRole.Highlight)
        for shape, kind in self.overlay:
            if kind == "guide":                      # a snap line: a zero-width or zero-height rectangle
                p.setPen(QPen(QColor(255, 60, 160), 1, Qt.PenStyle.DashLine))
                p.drawLine(shape.left(), shape.top(), shape.right(), shape.bottom())
                continue
            poly = isinstance(shape, QPolygonF)
            if kind == "src":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 90))
            elif kind == "sel":                      # outline of the selected Collage photo
                p.setPen(QPen(hl, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
            elif kind == "rot":                      # the rotate knob: a round handle
                p.setPen(QPen(QColor(255, 255, 255), 1))
                p.setBrush(hl)
                p.drawEllipse(shape)
                continue
            elif kind == "handle":
                p.setPen(QPen(QColor(255, 255, 255), 1))
                p.setBrush(hl)
            else:
                fill = QColor(hl)
                fill.setAlpha(70)
                p.setPen(QPen(hl, 3))
                p.setBrush(fill)
            p.drawPolygon(shape) if poly else p.drawRect(shape)
        p.end()


class MainWindow(QMainWindow):
    SCREEN_DPI = 96   # 100% zoom
    FIT_PAD = 40      # px of desk around the paper in fit mode

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoSheet")
        self.layout_ = NUp(page=PageExtent(*PAGES["A4"]))
        self.layout_.page.set_margins(18, 18, 18, 18)
        self.page_no, self._gen, self._loading = 0, 0, False
        self.sig = _Signals()
        self.sig.done.connect(self._on_rendered)
        self.sig.progress.connect(self._on_progress)
        self.pool = QThreadPool.globalInstance()
        self.undo_stack = QUndoStack(self)

        self.preview = _PageLabel()
        self.preview.setAccessibleName("Page preview")
        self.preview.installEventFilter(self)
        self._press, self._dragging, self._drop = None, False, None   # page drag-and-drop state
        self._cl = None   # Collage photo being moved or resized
        self._pan = None   # photo being repositioned inside its frame
        self._pan_timer = QTimer(self, singleShot=True, interval=30, timeout=self.refresh)   # coalesce live re-renders
        self.zoom: float | None = None   # None = fit to window
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.preview)
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().setAutoFillBackground(True)
        self._apply_desk()
        self.scroll.viewport().installEventFilter(self)
        self._base, self._base_zoom, self._pending_zoom, self._dpr = None, 1.0, 1.0, 1.0
        self._fit_timer = QTimer(self, singleShot=True, interval=120)  # settles zoom/resize into one real render
        self._fit_timer.timeout.connect(self.refresh)
        central = QWidget()
        self._central_layout = QVBoxLayout(central)
        self._central_layout.setContentsMargins(0, 0, 0, 0)
        self._central_layout.setSpacing(0)
        self._central_layout.addWidget(self.scroll, 1)   # the page navigation row is added below it in _build_menu
        self.setCentralWidget(central)
        self.setAcceptDrops(True)
        self._build_layout_dock()
        self._build_image_dock()
        self._build_menu()
        self.busy = QFrame(self.scroll.viewport())   # floating status over the top of the preview while photos load
        self.busy.setObjectName("busy")
        self.busy.setMinimumWidth(260)
        self.busy.setStyleSheet(
            "#busy { background: palette(window); border: 1px solid palette(mid); border-radius: 10px; }"
            "QProgressBar { background: palette(midlight); border: none; border-radius: 2px; max-height: 4px; }"
            "QProgressBar::chunk { background: palette(highlight); border-radius: 2px; }")
        shadow = QGraphicsDropShadowEffect(self.busy, blurRadius=24, xOffset=0, yOffset=4)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.busy.setGraphicsEffect(shadow)
        self.busy_label, self.busy_count = QLabel("Preparing photos"), QLabel()
        self.busy_count.setEnabled(False)   # secondary text colour
        self.busy_bar = QProgressBar(textVisible=False)
        row = QHBoxLayout()
        row.addWidget(self.busy_label, 1)
        row.addWidget(self.busy_count)
        bl = QVBoxLayout(self.busy)
        bl.setContentsMargins(16, 12, 16, 14)
        bl.setSpacing(8)
        bl.addLayout(row)
        bl.addWidget(self.busy_bar)
        self.busy.hide()
        self._cold = 0
        self._export = None   # the running _ExportJob
        self._export_name, self._export_pm, self._export_stage = "", 0, ""
        self._creep = QTimer(self, interval=120)   # saving one PDF has no steps to report: ease towards the end
        self._creep.timeout.connect(self._creep_tick)
        self.sig.export_progress.connect(self._on_export_progress)
        self.sig.export_done.connect(self._on_export_done)
        self.busy_timer = QTimer(self, singleShot=True, interval=250)   # quick renders never flash a notice
        self.busy_timer.timeout.connect(self._show_busy)
        self.statusBar().hide()   # only shown while there is an error to report, so it leaves no blank strip
        self.refresh()

    # ---- construction ----
    def _build_menu(self):
        def act(menu, text, fn, key=None):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if key:
                a.setShortcut(key)
            menu.addAction(a)
            return a
        m = self.menuBar().addMenu("&File")
        act(m, "Add images…", self.add_images, "Ctrl+O")
        act(m, "Open preset…", self.open_preset)
        act(m, "Save preset…", self.save_preset, "Ctrl+S")
        m.addSeparator()
        act(m, "Export PDF/PNG…", self.export, "Ctrl+E")
        act(m, "Print…", self.print_, "Ctrl+P")
        m.addSeparator()
        act(m, "Quit", self.close, "Ctrl+Q")
        # Page navigation: a row centred directly under the page preview.
        nav = QWidget()
        row = QHBoxLayout(nav)
        row.setContentsMargins(0, 4, 0, 4)
        row.addStretch()
        # Glyph-only buttons need a real name for screen readers and Voice Control.
        for text, glyph, step, key in (("Previous Page", "◀", -1, "PgUp"), ("Next Page", "▶", 1, "PgDown")):
            a = QAction(text, self)
            a.triggered.connect(lambda _=False, d=step: self.goto(self.page_no + d))
            a.setShortcut(key)
            a.setIconText(glyph)
            a.setToolTip(f"{text} ({key})")
            self.addAction(a)   # keeps the shortcut working anywhere in the window
            btn = QToolButton()
            btn.setDefaultAction(a)
            btn.setAccessibleName(text)
            btn.setAutoRaise(True)
            row.addWidget(btn)
            if step < 0:
                self.page_label = QLabel(" 1 / 1 ")
                self.page_label.setAccessibleName("Current page")
                row.addWidget(self.page_label)
        row.addStretch()
        self._central_layout.addWidget(nav)
        e = self.menuBar().addMenu("&Edit")
        self.undo_act = self.undo_stack.createUndoAction(self, "Undo")
        self.undo_act.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_act = self.undo_stack.createRedoAction(self, "Redo")
        self.redo_act.setShortcut(QKeySequence.StandardKey.Redo)
        e.addActions([self.undo_act, self.redo_act])
        e.addSeparator()
        self.remove_act = act(e, "Remove Image", self.remove_selected, QKeySequence.StandardKey.Delete)
        # Delete only fires while the list has focus, so it can't eat a keystroke in a spin box.
        self.remove_act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.list.addAction(self.remove_act)
        act(e, "Center Photo in Frame", self._center_photo)
        act(e, "Sort Photos by Date", self._sort_by_date)
        e.addSeparator()
        self.front_act = act(e, "Bring to Front", lambda: self._z_order(True), "Ctrl+Shift+]")
        self.back_act = act(e, "Send to Back", lambda: self._z_order(False), "Ctrl+Shift+[")
        v = self.menuBar().addMenu("&View")
        v.addAction(self.layout_dock.toggleViewAction())
        v.addAction(self.image_dock.toggleViewAction())
        v.addSeparator()
        act(v, "Fit to Window", lambda: self.set_zoom(None), "Ctrl+0")
        act(v, "Actual Size", lambda: self.set_zoom(1.0), "Ctrl+1")
        act(v, "Zoom In", lambda: self.set_zoom((self.zoom or self._fit_zoom()) * 1.25), QKeySequence.StandardKey.ZoomIn)
        act(v, "Zoom Out", lambda: self.set_zoom((self.zoom or self._fit_zoom()) / 1.25), QKeySequence.StandardKey.ZoomOut)
        v.addSeparator()
        act(v, "Larger Text", lambda: change_text_size(+1))
        act(v, "Smaller Text", lambda: change_text_size(-1))
        h = self.menuBar().addMenu("&Help")
        act(h, "About PhotoSheet", self._about)
        act(h, "About Qt", lambda: QMessageBox.aboutQt(self, "About Qt"))

    def _about(self):
        box = QMessageBox(self)
        box.setWindowTitle("About PhotoSheet")
        box.setIconPixmap(QPixmap(os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "photosheet.png"))
                          .scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<h3>PhotoSheet {__version__}</h3>"
            "<p>Print many photos per sheet, with ICC colour management.</p>"
            "<p>Written by Javad Razavian<br>"
            '<a href="https://github.com/javadr/PhotoSheet">github.com/javadr/PhotoSheet</a></p>'
            f'<p>Built with Python {sys.version.split()[0]} and <a href="https://www.qt.io">Qt</a> {qVersion()} '
            f"(PySide6 {PYSIDE_VERSION}).</p>")
        qt = box.addButton("About Qt", QMessageBox.ButtonRole.HelpRole)
        box.addButton(QMessageBox.StandardButton.Close)
        qt.clicked.disconnect()   # a HelpRole button would otherwise close the box before the Qt dialog shows
        qt.clicked.connect(lambda: QMessageBox.aboutQt(box, "About Qt"))
        box.exec()

    def _build_layout_dock(self):
        self.kind = QComboBox()
        model = QStandardItemModel(self.kind)
        for group, names in KIND_GROUPS:                      # headings cannot be picked; the layouts under them can
            head = QStandardItem(group)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            model.appendRow(head)
            for name in names:
                model.appendRow(QStandardItem(name))
        self.kind.setModel(model)
        self.kind.setItemDelegate(_GroupedDelegate(self.kind))
        self.kind.setCurrentText("N-up")
        self.kind.setMaxVisibleItems(40)                      # the whole list fits: no scrolling inside a menu
        self.page_size = QComboBox()
        self.page_size.addItems(PAGES)
        self.orient = QComboBox()
        self.orient.addItems(["Portrait", "Landscape"])
        self.copies = self._spin(0, 99, 0)
        self.copies.setSpecialValueText("Fill sheet")   # 0: share the sheet between the photos
        self.copies.setToolTip("Copies of each photo. 'Fill sheet' repeats a single photo into every slot.")
        self.per_sheet = QLabel("")
        self._last_kind = "N-up"
        self.margin = self._spin(0, 200, 18)
        self.rows, self.cols = self._spin(1, 20, 3), self._spin(1, 20, 3)
        self._grid_touched = False   # until the user edits Rows or Columns, each layout starts with its own grid
        self._orient_touched = False  # likewise for Orientation: until it is chosen, each layout uses its own default
        self._orient_setting = False
        self._grid_setting = False
        self.gutter = self._spin(0, 100, 8)
        self.segments, self.overlap = self._spin(1, 64, 4), SliderSpin(0, 180, 30, "°")
        self.feather = SliderSpin(0, 100, 0, " pt")
        self.angle, self.inner = self._spin(-360, 360, 0), self._spin(0, 400, 0)
        self.poster_w, self.poster_h = self._spin(1, 400, 16), self._spin(1, 400, 12)
        self.size_preset = QComboBox()
        self.size_preset.addItems(SIZE_PRESETS)
        self.cell_w, self.cell_h = self._dspin(10, 400, 35), self._dspin(10, 400, 45)
        self.row_h = self._dspin(10, 300, 50)
        self.size_preset.currentIndexChanged.connect(self._apply_size_preset)
        self.caption = QComboBox()
        self.caption.addItems(["None", "Filename", "Number", "Both"])
        self.marks = QCheckBox("Cut marks")
        self.frames = self._spin(1, 12, 4)
        self.border = self._spin(2, 60, 10)
        self.panels = self._spin(1, 12, 3)
        self.vertical = QCheckBox("Stack panels vertically")
        self.recipe = QComboBox()
        self.recipe.addItems([label for label, _, _ in RECIPES.values()])
        self.recipe.currentIndexChanged.connect(self._apply_recipe)
        self.snap = QCheckBox("Snap to guides")
        self.snap.setChecked(True)
        self.snap.setToolTip("Moves and resizes snap to the page edges and centre, and to other photos.\n"
                             "Hold Ctrl while dragging to turn snapping off for that drag.")
        self.arrange_btn = QPushButton("Auto-arrange")
        self.arrange_btn.clicked.connect(self._auto_arrange)
        self.mat = self._spin(0, 200, 36)
        self.mat_color = QComboBox()
        self.mat_color.addItems(["White", "Black", "Cream"])
        self.badge_size = QComboBox()
        self.badge_size.addItems([n for n, *_ in BADGE_SIZES] + ["Custom"])
        self.badge_size.setToolTip("Standard pin-back button sizes. Pick one to set the diameter and a typical margin, or choose your own below.")
        self.diameter = self._dspin(10, 200, 25)
        self.diameter.setToolTip("Badge face diameter; any value works.")
        self.bleed = self._dspin(0, 30, 5.0)
        self.bleed.setToolTip("White paper around the badge face that folds behind the pin-back; the cut line sits at its edge. Picking a standard size sets a typical value (5 mm for a 25 mm badge).")
        self.fill_margin = QCheckBox("Photo fills the margin")
        self.fill_margin.setChecked(True)
        self.fill_margin.setToolTip("Run the photo out to the cut line. The photo is cropped at its edges, but no white shows if the cut is off.")
        self.staggered = QCheckBox("Staggered rows")
        self.staggered.setChecked(True)
        self.thumbs = self._spin(1, 12, 4)
        self.side = QCheckBox("Small photos on the side")
        self.hero_share = SliderSpin(20, 90, 70, " %")
        self.direction = QComboBox()
        self.direction.addItems(DIRECTIONS)
        self.direction.setToolTip("Automatic: strips run sideways on a landscape page and top to bottom on a portrait page.")
        self.bento = QComboBox()
        self.bento.addItems([label for label, _, _, _ in BENTO.values()])
        self.spiral_n = self._spin(2, 12, 5)
        self.spiral_share = SliderSpin(30, 80, 62, " %")
        self.spiral_cw = QCheckBox("Clockwise")
        self.spiral_cw.setChecked(True)
        self.hex_size = self._dspin(10, 150, 40)
        self.hex_half = QCheckBox("Half cells at the edges")
        self.hex_half.setToolTip("Also show the partial cells that poke in from the sides, cut off at the page edge. Off leaves that space white.")
        self.brick_half = QCheckBox("Half tiles at the row ends")
        self.brick_half.setChecked(True)
        self.brick_half.setToolTip("Off leaves the ends of the shifted rows white instead of filling them with half tiles.")
        self.hex_fill = QCheckBox("Fill the page")
        self.hex_fill.setToolTip("Widen the cells to run edge to edge and stretch them to reach the top and bottom.")
        self.cal_year = self._spin(1900, 2200, datetime.date.today().year)
        self.cal_month = QComboBox()
        self.cal_month.addItems(MONTHS)
        self.cal_months = self._spin(1, 24, 12)
        self.cal_week = QComboBox()
        self.cal_week.addItems(["Monday", "Sunday"])
        self.cal_share = SliderSpin(20, 80, 55, " %")
        self.shape_kind = QComboBox()
        self.shape_kind.addItems(SHAPE_LABELS)
        self.ring_n = self._spin(3, 36, 8)
        self.ring_center = QCheckBox("Photo in the centre")
        self.ring_center.setChecked(True)
        self.sc_n = self._spin(1, 30, 6)
        self.sc_size = SliderSpin(15, 90, 45, " %")
        self.sc_tilt = SliderSpin(0, 45, 12, "°")
        self.sc_border = self._spin(0, 40, 8)
        self.sc_seed = self._spin(0, 9999, 1)
        self.sc_seed.setToolTip("The same number always gives the same pile.")
        self.shuffle_btn = QPushButton("Shuffle")
        self.slant_panels = self._spin(2, 12, 5)
        self.slant = SliderSpin(-40, 40, 12, "°")
        self.slant.setToolTip("How far the panels lean. Negative leans left. A narrow page limits the lean.")
        self.fan_n = self._spin(1, 12, 5)
        self.fan_spread = SliderSpin(10, 120, 60, "°")
        self.fan_border = SliderSpin(2, 15, 6, " %")
        self.tl_n = self._spin(1, 12, 6)
        self.st_n = self._spin(1, 12, 5)
        self.st_rise = QCheckBox("Climb to the top right")
        self.st_rise.setToolTip("Start at the bottom left and step up, instead of starting at the top left and stepping down.")
        self.ck_empty = QCheckBox("First cell empty")
        self.ck_empty.setToolTip("Start the pattern on the second square instead of the first.")
        self.tl_dates = QCheckBox("Show dates")
        self.tl_dates.setChecked(True)
        self.tl_dates.setToolTip("Dates come from each photo's own data; the file name is shown for photos without one.")
        self.shuffle_btn.clicked.connect(self._shuffle)
        self.collage_hint = QLabel("Drag a photo to move it, drag a corner\nto resize (Shift = free shape).")
        self.strip_w, self.strip_h = self._dspin(20, 400, 51), self._dspin(40, 600, 152)
        self.crop = QCheckBox("Crop all images to fill")
        self.crop.setChecked(True)
        self.crop.setToolTip("With this on, drag a photo on the page to reposition it inside its frame.\n"
                             "Hold Shift while dragging to swap two photos instead.")
        form = QFormLayout()      # the layout's own options: this part scrolls
        pinned = QFormLayout()    # layout, page, orientation, margin: always in view
        labels = _LabelGroup()
        pinned_widgets = (self.kind, self.page_size, self.orient, self.margin)
        self.rowsof = {}
        for key, label, w in [
            ("all", "Layout", self.kind), ("all", "Page", self.page_size), ("all", "Orientation", self.orient), ("all", "Margin (pt)", self.margin),
            ("nup,polaroid,shapes,brick,checker,pyramid,diamond", "Rows", self.rows), ("nup,polaroid,shapes,masonry,brick,checker", "Columns", self.cols),
            ("nup,fixed,passport,mosaic,strip,polaroid,triptych,package,badges,feature,bento,spiral,honeycomb,calendar,masonry,shapes,ring,brick,checker,pyramid,diamond,slant,timeline", "Gutter (pt)", self.gutter),
            ("nup,polaroid,framed", "Captions", self.caption),
            ("nup,fixed,passport,polaroid,package,badges,honeycomb,ring", "", self.marks),
            ("carousel", "Segments", self.segments), ("carousel", "Overlap (°)", self.overlap), ("nup,single,fixed,passport,carousel,mosaic,strip,polaroid,package,collage,framed,badges,feature,filmstrip,bento,spiral,honeycomb,calendar,masonry,shapes,ring,scatter,slant,fan,timeline,brick,checker,stairs,pyramid,diamond", "Feather (pt)", self.feather),
            ("carousel", "Angle offset (°)", self.angle), ("carousel", "Inner radius (pt)", self.inner),
            ("poster", "Poster width (in)", self.poster_w), ("poster", "Poster height (in)", self.poster_h),
            ("fixed,passport", "Print size", self.size_preset), ("fixed,passport", "Width (mm)", self.cell_w),
            ("fixed,passport", "Height (mm)", self.cell_h), ("passport", "Copies per photo", self.copies),
            ("fixed,passport,package,badges,honeycomb,shapes,ring,slant", "Per sheet", self.per_sheet), ("mosaic", "Row height (mm)", self.row_h),
            ("strip,filmstrip", "Frames per strip", self.frames), ("strip,filmstrip", "Strip width (mm)", self.strip_w),
            ("strip", "Strip length (mm)", self.strip_h), ("strip,filmstrip", "Direction", self.direction),
            ("polaroid", "Border (pt)", self.border), ("triptych", "Panels", self.panels),
            ("triptych", "", self.vertical), ("package", "Sheet", self.recipe),
            ("collage", "", self.snap), ("collage", "", self.arrange_btn), ("collage", "", self.collage_hint),
            ("framed", "Mat width (pt)", self.mat), ("framed", "Mat colour", self.mat_color),
            ("badges", "Badge size", self.badge_size), ("badges", "Diameter (mm)", self.diameter), ("badges", "Pin-back margin (mm)", self.bleed), ("badges", "", self.fill_margin), ("badges", "", self.staggered),
            ("feature", "Small photos", self.thumbs), ("feature", "", self.side), ("feature", "Hero size (%)", self.hero_share),
            ("bento", "Template", self.bento), ("spiral", "Photos per page", self.spiral_n),
            ("spiral", "Big photo share (%)", self.spiral_share), ("spiral", "", self.spiral_cw),
            ("honeycomb", "Cell width (mm)", self.hex_size), ("honeycomb", "", self.hex_fill), ("honeycomb", "", self.hex_half), ("brick", "", self.brick_half), ("calendar", "Year", self.cal_year),
            ("calendar", "First month", self.cal_month), ("calendar", "Months", self.cal_months),
            ("calendar", "Week starts on", self.cal_week), ("calendar", "Photo size (%)", self.cal_share),
            ("shapes", "Shape", self.shape_kind), ("ring", "Photos in the ring", self.ring_n), ("ring", "", self.ring_center),
            ("scatter", "Photos per page", self.sc_n), ("scatter", "Photo size (%)", self.sc_size),
            ("scatter", "Tilt (°)", self.sc_tilt), ("scatter", "Border (pt)", self.sc_border),
            ("scatter", "Random seed", self.sc_seed), ("scatter", "", self.shuffle_btn),
            ("slant", "Panels", self.slant_panels), ("slant", "Lean", self.slant),
            ("fan", "Cards per page", self.fan_n), ("fan", "Spread", self.fan_spread), ("fan", "Border", self.fan_border),
            ("timeline", "Photos per page", self.tl_n), ("timeline", "", self.tl_dates), ("checker", "", self.ck_empty), ("stairs", "Photos per page", self.st_n), ("stairs", "", self.st_rise),
            ("all", "", self.crop),
        ]:
            (pinned if any(w is x for x in pinned_widgets) else form).addRow(labels.label(label), w)
            if isinstance(w, SliderSpin):
                w.set_label(label)
            self.rowsof.setdefault(key, []).append(w)
            if any(w is x for x in (self.size_preset, self.badge_size, self.per_sheet, self.recipe, self.arrange_btn, self.collage_hint,
                                   self.shuffle_btn)):
                continue  # wired separately (presets, recipe, button) or have no value to watch
            w_changed = getattr(w, "currentIndexChanged", None) or getattr(w, "toggled", None) or w.valueChanged
            w_changed.connect(self._layout_changed)
            if w in (self.cell_w, self.cell_h):
                w_changed.connect(self._size_edited)
        self.badge_size.activated.connect(self._pick_badge_size)
        self.diameter.valueChanged.connect(self._sync_badge_size)
        self._sync_badge_size()
        side = self.style().pixelMetric(QStyle.PixelMetric.PM_LayoutLeftMargin)   # the style's own panel margin
        pinned.setContentsMargins(side, 6, side, 0)
        form.setContentsMargins(side, 6, side, 6)
        options = QWidget()
        options.setLayout(form)
        head = QWidget()
        head.setLayout(pinned)
        self._pinned_form, self._pinned_side = pinned, side
        self.layout_pane = ScrollPane(options)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(head)
        lay.addWidget(self.layout_pane, 1)
        self.layout_pane.area.verticalScrollBar().rangeChanged.connect(self._align_pinned)
        self.layout_dock = QDockWidget("Layout", self)
        self.layout_dock.setWidget(box)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.layout_dock)
        self.orient.currentIndexChanged.connect(self._touch_orient)
        self.rows.valueChanged.connect(self._touch_grid)
        self.cols.valueChanged.connect(self._touch_grid)
        self._scrolled_kind = None
        self._show_kind_rows()

    def _build_image_dock(self):
        self.list = _ImageList()
        self.list.setItemDelegate(_NumberedDelegate(self.list))
        self.list.files_dropped.connect(self._add_files)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.model().rowsMoved.connect(self._reordered)
        self.list.currentRowChanged.connect(self._load_effects)
        self.list.currentRowChanged.connect(lambda *_: self._refresh_overlay())
        add, rem = QPushButton("Add…"), QPushButton("Remove")
        add.clicked.connect(self.add_images)
        rem.clicked.connect(self.remove_selected)
        self.gamma = QDoubleSpinBox(minimum=0.1, maximum=5, singleStep=0.1, value=1.0)
        self.temp = self._spin(-2000, 2000, 0)
        self.sharp = QDoubleSpinBox(minimum=0, maximum=5, singleStep=0.1, value=0)
        self.gray, self.auto = QCheckBox("Desaturate"), QCheckBox("Auto contrast")
        self.fliph, self.flipv = QCheckBox("Flip horizontal"), QCheckBox("Flip vertical")
        self.angle = QDoubleSpinBox(minimum=-180, maximum=180, decimals=1, singleStep=1, suffix="°", wrapping=True)
        ccw, cw = QPushButton("⟲ 90°"), QPushButton("⟳ 90°")
        for b, d in ((ccw, -90), (cw, 90)):
            b.clicked.connect(lambda _=False, d=d: self.angle.setValue((self.angle.value() + d + 180) % 360 - 180))
        self.fx_hint = QLabel("Select an image to adjust it.")
        self.fx_widgets = [self.gamma, self.temp, self.sharp, self.angle, ccw, cw, self.gray, self.auto, self.fliph, self.flipv]
        form = QFormLayout()
        for label, w in [("Gamma", self.gamma), ("Warm/Cool", self.temp), ("Sharpen", self.sharp),
                         ("Rotate", self.angle)]:
            form.addRow(label, w)
            w.valueChanged.connect(self._effects_changed)
        turn = QHBoxLayout()
        turn.addWidget(ccw)
        turn.addWidget(cw)
        form.addRow("", turn)
        checks = QGridLayout()   # flips share the left column, tone effects the right
        for r, (a, b) in enumerate(((self.fliph, self.gray), (self.flipv, self.auto))):
            checks.addWidget(a, r, 0)
            checks.addWidget(b, r, 1)
            a.toggled.connect(self._effects_changed)
            b.toggled.connect(self._effects_changed)
        form.addRow(checks)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        fx = QWidget()
        fxl = QVBoxLayout(fx)
        fxl.setContentsMargins(0, 0, 0, 0)
        fxl.addWidget(self.fx_hint)
        fxl.addLayout(form)
        self.fx_pane = ScrollPane(fx, min_height=90)
        self.list.setMinimumHeight(90)
        lay = QVBoxLayout()
        lay.addWidget(self.list, 1)
        lay.addLayout(row)
        lay.addWidget(self.fx_pane)
        box = QWidget()
        box.setLayout(lay)
        self.image_dock = QDockWidget("Images", self)
        self.image_dock.setWidget(box)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.image_dock)
        self.fx_pane.fit_width()
        self._load_effects()

    def _pick_badge_size(self, i):
        if i < len(BADGE_SIZES):
            self.diameter.setValue(BADGE_SIZES[i][1])
            self.bleed.setValue(BADGE_SIZES[i][2])

    def _sync_badge_size(self):   # show the matching standard size, or Custom
        i = next((k for k, (_, mm, _) in enumerate(BADGE_SIZES) if abs(mm - self.diameter.value()) < 0.05), len(BADGE_SIZES))
        self.badge_size.setCurrentIndex(i)

    @staticmethod
    def _dspin(lo, hi, val):
        s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=1, singleStep=1.0)
        s.setValue(val)
        return s

    def _apply_size_preset(self, *_):
        size = SIZE_PRESETS[self.size_preset.currentText()]
        if size:
            for sp, v in ((self.cell_w, size[0]), (self.cell_h, size[1])):
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        self._layout_changed()

    def _touch_orient(self, *_):
        if not self._orient_setting:
            self._orient_touched = True

    def _touch_grid(self, *_):
        if not self._grid_setting:
            self._grid_touched = True

    def _recipe_key(self) -> str:
        return list(RECIPES)[self.recipe.currentIndex()]

    def _apply_recipe(self, *_):
        """Picking a studio sheet also picks its paper (8 x 10 in)."""
        if self._loading:
            return
        paper = RECIPES[self._recipe_key()][1]
        for name, dims in PAGES.items():
            if dims == paper:
                self.page_size.blockSignals(True)
                self.page_size.setCurrentText(name)
                self.page_size.blockSignals(False)
                self.orient.blockSignals(True)
                self.orient.setCurrentText("Portrait")
                self.orient.blockSignals(False)
        self._layout_changed()

    def _size_edited(self, *_):
        self.size_preset.blockSignals(True)  # a hand-typed size is no longer a named preset
        self.size_preset.setCurrentText("Custom")
        self.size_preset.blockSignals(False)

    @staticmethod
    def _spin(lo, hi, val):
        s = QSpinBox(minimum=lo, maximum=hi)
        s.setValue(val)
        return s

    # ---- layout state ----
    def _align_pinned(self, *_):
        """Keep the pinned fields' right edge level with the scrolling ones: leave room for the scroll bar while it shows."""
        bar = self.layout_pane.area.verticalScrollBar()
        m = self._pinned_form.contentsMargins()
        right = self._pinned_side + (bar.sizeHint().width() if bar.maximum() > bar.minimum() else 0)
        if m.right() != right:
            self._pinned_form.setContentsMargins(m.left(), m.top(), right, m.bottom())

    def _show_kind_rows(self):
        k = KEYS[self.kind.currentText()]
        for key, widgets in self.rowsof.items():
            for w in widgets:
                visible = (key == "all" or k in key.split(",")) and not (w is self.crop and k in ("badges", "slant", "carousel"))   # these always fill their shape
                if w is self.per_sheet:   # a readout with nothing to say takes no room (no label with a blank value)
                    visible = visible and bool(self.per_sheet.text())
                w.parentWidget().layout().setRowVisible(w, visible)
        self.layout_pane.fit_width()
        if self._scrolled_kind != k:                       # a different layout starts at the top of its options
            self._scrolled_kind = k
            self.layout_pane.scroll_to_top()
            # Qt only grows a dock to fit; ask for 1 px so it clamps to this layout's minimum, wider or narrower.
            QTimer.singleShot(0, lambda: self.resizeDocks([self.layout_dock], [1], Qt.Orientation.Horizontal))

    def _layout_changed(self, *_):
        if self._loading:
            return
        self._show_kind_rows()
        old = self.layout_
        cls = KINDS[self.kind.currentText()]
        want = ORIENT_DEFAULTS.get(self.kind.currentText(), "Portrait")
        if self.kind.currentText() != self._last_kind and not self._orient_touched and self.orient.currentText() != want:
            self._orient_setting = True                    # nobody chose an orientation yet: this layout's own default
            self.orient.blockSignals(True)
            self.orient.setCurrentText(want)
            self.orient.blockSignals(False)
            self._orient_setting = False
        pw, ph = PAGES[self.page_size.currentText()]
        if self.orient.currentText() == "Landscape":
            pw, ph = ph, pw
        page = PageExtent(pw, ph)
        kind = self.kind.currentText()
        if kind in ("Passport", "Package") and self._last_kind not in ("Passport", "Package"):
            for sp, v in ((self.margin, 4), (self.gutter, 4)):   # shop-style sheets are nearly borderless
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        if kind == "Badges" and self._last_kind != "Badges":                # cut-out badges sit close together
            self.gutter.blockSignals(True)
            self.gutter.setValue(2)
            self.gutter.blockSignals(False)
        if kind == "Film strip" and self._last_kind != "Film strip":        # 35 mm film: 6 frames on 35 mm
            for sp, v in ((self.strip_w, 35.0), (self.frames, 6)):
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        elif kind == "Photo strip" and self._last_kind == "Film strip":     # back to booth strips
            for sp, v in ((self.strip_w, 51.0), (self.frames, 4)):
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        if kind != self._last_kind and kind in GRID_DEFAULTS and not self._grid_touched:
            self._grid_setting = True                      # the user has not chosen a grid yet: use this layout's own
            for sp, v in zip((self.rows, self.cols), GRID_DEFAULTS[kind]):
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
            self._grid_setting = False
        self._last_kind = kind
        m = self.margin.value()
        if not page.set_margins(m, m, m, m):
            return
        kw = {}
        if cls in (NUp, Polaroid):
            kw = dict(rows=self.rows.value(), cols=self.cols.value(),
                      hgutter=self.gutter.value(), vgutter=self.gutter.value(),
                      caption=self.caption.currentText().lower(), marks=self.marks.isChecked())
            if cls is Polaroid:
                kw["border"] = self.border.value()
        elif cls is Carousel:
            kw = dict(segments=self.segments.value(), overlap=self.overlap.value(),
                      angle_offset=self.angle.value(), inner_radius=self.inner.value())
        elif cls is Poster:
            kw = dict(poster_w=self.poster_w.value() * 72, poster_h=self.poster_h.value() * 72)
        elif cls in (FixedSize, Passport):
            kw = dict(cell_w=self.cell_w.value() * MM, cell_h=self.cell_h.value() * MM,
                      hgutter=self.gutter.value(), vgutter=self.gutter.value(), marks=self.marks.isChecked())
            if cls is Passport:
                kw["copies"] = self.copies.value()
        elif cls is Strip:
            kw = dict(frames=self.frames.value(), strip_w=self.strip_w.value() * MM,
                      strip_h=self.strip_h.value() * MM, gutter=self.gutter.value(),
                      direction=DIRECTIONS[self.direction.currentText()])
        elif cls is Mosaic:
            kw = dict(row_height=self.row_h.value() * MM, gutter=self.gutter.value())
        elif cls is Triptych:
            kw = dict(panels=self.panels.value(), gap=self.gutter.value(), vertical=self.vertical.isChecked())
        elif cls is Package:
            kw = dict(prints=[tuple(x) for x in RECIPES[self._recipe_key()][2]], gutter=self.gutter.value(),
                      marks=self.marks.isChecked())
        elif cls is Collage:
            kw = dict(snap=self.snap.isChecked())
        elif cls is FramedPrint:
            kw = dict(mat=self.mat.value(), color=self.mat_color.currentText().lower(),
                      caption=self.caption.currentText().lower())
        elif cls is Badges:
            kw = dict(diameter=self.diameter.value() * MM, gutter=self.gutter.value(),
                      staggered=self.staggered.isChecked(), bleed=self.bleed.value() * MM,
                      fill_margin=self.fill_margin.isChecked(), marks=self.marks.isChecked())
        elif cls is Feature:
            kw = dict(thumbs=self.thumbs.value(), side=self.side.isChecked(),
                      hero_share=self.hero_share.value() / 100, gutter=self.gutter.value())
        elif cls is Filmstrip:
            kw = dict(frames=self.frames.value(), strip_w=self.strip_w.value() * MM, gutter=self.gutter.value(),
                      direction=DIRECTIONS[self.direction.currentText()])
        elif cls is Bento:
            kw = dict(template=list(BENTO)[self.bento.currentIndex()], gutter=self.gutter.value())
        elif cls is Spiral:
            kw = dict(photos=self.spiral_n.value(), share=self.spiral_share.value() / 100,
                      clockwise=self.spiral_cw.isChecked(), gutter=self.gutter.value())
        elif cls is Honeycomb:
            kw = dict(size=self.hex_size.value() * MM, gutter=self.gutter.value(), fill=self.hex_fill.isChecked(), half_cells=self.hex_half.isChecked(), marks=self.marks.isChecked())
        elif cls is Masonry:
            kw = dict(columns=self.cols.value(), gutter=self.gutter.value())
        elif cls is Shapes:
            kw = dict(rows=self.rows.value(), cols=self.cols.value(), gutter=self.gutter.value(),
                      kind=SHAPE_LABELS[self.shape_kind.currentText()])
        elif cls is Ring:
            kw = dict(photos=self.ring_n.value(), center=self.ring_center.isChecked(), gutter=self.gutter.value(),
                      marks=self.marks.isChecked())
        elif cls is Scatter:
            kw = dict(photos=self.sc_n.value(), size=self.sc_size.value() / 100, tilt=self.sc_tilt.value(),
                      border=self.sc_border.value(), seed=self.sc_seed.value())
        elif cls is Slant:
            kw = dict(panels=self.slant_panels.value(), slant=self.slant.value(), gutter=self.gutter.value())
        elif cls is Fan:
            kw = dict(photos=self.fan_n.value(), spread=self.fan_spread.value(), border=self.fan_border.value() / 100)
        elif cls is Brick:
            kw = dict(columns=max(2, self.cols.value()), rows=self.rows.value(), gutter=self.gutter.value(),
                      half_cells=self.brick_half.isChecked())
        elif cls is Checker:
            kw = dict(columns=self.cols.value(), rows=self.rows.value(), gutter=self.gutter.value(),
                      first_empty=self.ck_empty.isChecked())
        elif cls in (Pyramid, Diamond):
            kw = dict(rows=self.rows.value(), gutter=self.gutter.value())
        elif cls is Stairs:
            kw = dict(photos=self.st_n.value(), rising=self.st_rise.isChecked())
        elif cls is Timeline:
            kw = dict(photos=self.tl_n.value(), dates=self.tl_dates.isChecked(), gutter=self.gutter.value())
        elif cls is Calendar:
            kw = dict(year=self.cal_year.value(), month=self.cal_month.currentIndex() + 1, months=self.cal_months.value(),
                      week_start=self.cal_week.currentText().lower(), photo_share=self.cal_share.value() / 100,
                      gutter=self.gutter.value())
        self.layout_ = cls(page=page, feather=self.feather.value() if cls not in (Poster, Triptych) else 0, **kw)
        for it in old.images:
            it.crop = self.crop.isChecked()
            self.layout_.add(it)
        if cls is Carousel and not isinstance(old, Carousel):
            self._sync_segments()   # entering Carousel: one wedge per photo
        self.preview.overlay = []
        self._update_per_sheet()
        self._update_collage_actions()
        self.refresh()

    def _sync_segments(self):
        """A Carousel divides its circle between the photos it has: follow the count on add, remove and undo."""
        if isinstance(self.layout_, Carousel):
            n = max(1, len(self.layout_.images))
            self.layout_.segments = n
            self.segments.blockSignals(True)
            self.segments.setValue(n)
            self.segments.blockSignals(False)

    def _shuffle(self):
        self.sc_seed.setValue(random.randint(0, 9999))   # fires _layout_changed through the spin box

    def _update_per_sheet(self):
        lay = self.layout_
        if isinstance(lay, FixedSize):
            self.per_sheet.setText(f"{lay.cols} × {lay.rows} = {lay.per_page} photos")
        elif isinstance(lay, Package):
            total = sum(int(n) for _, _, n in lay.prints)
            self.per_sheet.setText(f"{total} prints, all fit" if not lay.missing
                                   else f"{lay.missing} of {total} do not fit: use bigger paper")
        elif isinstance(lay, Badges):
            self.per_sheet.setText(f"{lay.per_page} badges per page" if lay.per_page else "too big for the page")
        elif isinstance(lay, Shapes):
            self.per_sheet.setText(f"{lay.cols} × {lay.rows} = {lay.per_page} photos")
        elif isinstance(lay, Slant):
            self.per_sheet.setText(f"limited to {abs(lay.effective_slant):.0f}° on this page" if lay.slant_limited else "")
        elif isinstance(lay, Ring):
            self.per_sheet.setText(f"{lay.per_page} photos per page" + (" (the centre needs 6 or more in the ring)"
                                                                         if lay.center_left_out else ""))
        elif isinstance(lay, Honeycomb):
            self.per_sheet.setText(f"{lay.per_page} cells per page" if lay.per_page else "too big for the page")
        else:
            self.per_sheet.setText("")
        self._show_kind_rows()

    def _sync_controls(self):
        """Push self.layout_ into the widgets (after loading a preset)."""
        self._loading = True
        lay = self.layout_
        name = next(n for n, c in KINDS.items() if type(lay) is c)
        self.kind.setCurrentText(name)
        self._last_kind = name
        for n, (w, h) in PAGES.items():
            for orient, dims in (("Portrait", (w, h)), ("Landscape", (h, w))):
                if dims == (lay.page.width, lay.page.height):
                    self.page_size.setCurrentText(n)
                    self.orient.setCurrentText(orient)
        self.margin.setValue(int(lay.page.left))
        for attr, w in [("rows", self.rows), ("cols", self.cols), ("hgutter", self.gutter),
                        ("gutter", self.gutter), ("gap", self.gutter), ("panels", self.panels), ("border", self.border),
                        ("mat", self.mat), ("thumbs", self.thumbs), ("columns", self.cols),
                        ("frames", self.frames), ("segments", self.segments), ("overlap", self.overlap), ("feather", self.feather),
                        ("angle_offset", self.angle), ("inner_radius", self.inner)]:
            if hasattr(lay, attr):
                w.setValue(int(getattr(lay, attr)))
        if isinstance(lay, Poster):
            self.poster_w.setValue(int(lay.poster_w / 72))
            self.poster_h.setValue(int(lay.poster_h / 72))
        if isinstance(lay, Passport):
            self.copies.setValue(lay.copies)
        if isinstance(lay, FixedSize):
            self.cell_w.setValue(lay.cell_w / MM)
            self.cell_h.setValue(lay.cell_h / MM)
            match = [n for n, v in SIZE_PRESETS.items() if v and abs(v[0] - lay.cell_w / MM) < .05 and abs(v[1] - lay.cell_h / MM) < .05]
            self.size_preset.setCurrentText(match[0] if match else "Custom")
        if isinstance(lay, Mosaic):
            self.row_h.setValue(lay.row_height / MM)
        if isinstance(lay, Strip):
            self.strip_w.setValue(lay.strip_w / MM)
            self.strip_h.setValue(lay.strip_h / MM)
            self.direction.setCurrentText(next(k for k, v in DIRECTIONS.items() if v == lay.direction))
        if isinstance(lay, Triptych):
            self.vertical.setChecked(lay.vertical)
        if isinstance(lay, Package):
            keys = [i for i, (_, _, pr) in enumerate(RECIPES.values())
                    if [tuple(x) for x in pr] == [tuple(x) for x in lay.prints]]
            if keys:
                self.recipe.setCurrentIndex(keys[0])
        if isinstance(lay, Collage):
            self.snap.setChecked(lay.snap)
        if isinstance(lay, FramedPrint):
            self.mat_color.setCurrentText(lay.color.capitalize())
        if isinstance(lay, Bento):
            self.bento.setCurrentIndex(list(BENTO).index(lay.template))
        if isinstance(lay, (Masonry, Brick, Checker)):
            self.cols.setValue(lay.columns)
        if isinstance(lay, Brick):
            self.brick_half.setChecked(lay.half_cells)
        if isinstance(lay, (Brick, Checker, Pyramid)):
            self.rows.setValue(lay.rows)
        if isinstance(lay, Slant):
            self.slant_panels.setValue(lay.panels)
            self.slant.setValue(round(lay.slant))
        if isinstance(lay, Fan):
            self.fan_n.setValue(lay.photos)
            self.fan_spread.setValue(round(lay.spread))
            self.fan_border.setValue(round(lay.border * 100))
        if isinstance(lay, Checker):
            self.ck_empty.setChecked(lay.first_empty)
        if isinstance(lay, Stairs):
            self.st_n.setValue(lay.photos)
            self.st_rise.setChecked(lay.rising)
        if isinstance(lay, Timeline):
            self.tl_n.setValue(lay.photos)
            self.tl_dates.setChecked(lay.dates)
        if isinstance(lay, Shapes):
            self.shape_kind.setCurrentText(next(k for k, v in SHAPE_LABELS.items() if v == lay.kind))
        if isinstance(lay, Ring):
            self.ring_n.setValue(lay.photos)
            self.ring_center.setChecked(lay.center)
        if isinstance(lay, Scatter):
            self.sc_n.setValue(lay.photos)
            self.sc_size.setValue(round(lay.size * 100))
            self.sc_tilt.setValue(round(lay.tilt))
            self.sc_border.setValue(round(lay.border))
            self.sc_seed.setValue(lay.seed)
        if isinstance(lay, Spiral):
            self.spiral_n.setValue(lay.photos)
            self.spiral_share.setValue(round(lay.share * 100))
            self.spiral_cw.setChecked(lay.clockwise)
        if isinstance(lay, Honeycomb):
            self.hex_size.setValue(lay.size / MM)
            self.hex_fill.setChecked(lay.fill)
            self.hex_half.setChecked(lay.half_cells)
        if isinstance(lay, Calendar):
            self.cal_year.setValue(lay.year)
            self.cal_month.setCurrentIndex(lay.month - 1)
            self.cal_months.setValue(lay.months)
            self.cal_week.setCurrentText(lay.week_start.capitalize())
            self.cal_share.setValue(round(lay.photo_share * 100))
        if isinstance(lay, Badges):
            self.diameter.setValue(lay.diameter / MM)
            self.staggered.setChecked(lay.staggered)
            self.bleed.setValue(lay.bleed / MM)
            self.fill_margin.setChecked(lay.fill_margin)
        if isinstance(lay, Feature):
            self.side.setChecked(lay.side)
            self.hero_share.setValue(round(lay.hero_share * 100))
        if hasattr(lay, "caption"):
            self.caption.setCurrentText(lay.caption.capitalize())
        self.marks.setChecked(lay.marks)
        self._loading = False
        self._show_kind_rows()
        self._update_per_sheet()
        self._update_collage_actions()
        self._fill_list()

    def dragEnterEvent(self, e):
        if dropped_images(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = dropped_images(e.mimeData())
        if files:
            e.acceptProposedAction()
            self._add_files(files)

    # ---- images ----
    def _fill_list(self):
        self.list.clear()
        for it in self.layout_.images:
            self.list.addItem(it.path.rsplit("/", 1)[-1])
        self.image_dock.setWindowTitle(f"Images ({self.list.count()})" if self.list.count() > 1 else "Images")

    def _edit(self, text, change, merge=False):
        """Run change() on the image list and record it for undo."""
        before = copy.deepcopy(self.layout_.images)
        change()
        self.undo_stack.push(_Edit(self, text, before, copy.deepcopy(self.layout_.images), merge))

    def _set_images(self, images):
        row = self.list.currentRow()
        self.layout_.images[:] = copy.deepcopy(images)
        self._sync_segments()
        self._fill_list()
        self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._load_effects()
        self.refresh()

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add images", "", IMG_FILTER)
        if files:
            self._add_files(files)

    def _add_files(self, files):
        def change():
            for f in files:
                self.layout_.add(ImageItem(f, crop=self.crop.isChecked()))
        self._edit("Add Images", change)
        self._sync_segments()
        self._fill_list()
        self.refresh()

    def remove_selected(self):
        r = self.list.currentRow()
        if r >= 0:
            self._edit("Remove Image", lambda: self.layout_.images.pop(r))
            self._sync_segments()
            self._fill_list()
            self.list.setCurrentRow(min(r, self.list.count() - 1))
            self.refresh()

    def _reordered(self, *_):
        names = [self.list.item(i).text() for i in range(self.list.count())]
        pool = list(self.layout_.images)
        ordered = []
        for n in names:  # match by filename; duplicates keep relative order
            ordered.append(next(p for p in pool if p.path.rsplit("/", 1)[-1] == n))
            pool.remove(ordered[-1])
        self._edit("Reorder Images", lambda: self.layout_.images.__setitem__(slice(None), ordered))
        self.refresh()

    def _selected(self):
        r = self.list.currentRow()
        return self.layout_.images[r] if 0 <= r < len(self.layout_.images) else None

    def _load_effects(self, *_):
        it = self._selected()
        self._loading = True
        fx = dict(it.effects) if it else {}
        self.gamma.setValue(fx.get("Gamma", {}).get("gamma", 1.0))
        self.temp.setValue(fx.get("Temperature", {}).get("change", 0))
        self.sharp.setValue(fx.get("UnsharpMask", {}).get("amount", 0))
        self.gray.setChecked("Desaturate" in fx)
        self.auto.setChecked("Autocontrast" in fx)
        self.angle.setValue(it.angle if it else 0)
        self.fliph.setChecked(bool(it and it.flip_h))
        self.flipv.setChecked(bool(it and it.flip_v))
        for w in self.fx_widgets:
            w.setEnabled(it is not None)
        self.fx_hint.setVisible(it is None)
        self._loading = False

    def _effects_changed(self, *_):
        it = self._selected()
        if self._loading or not it:
            return
        fx = []
        if self.gamma.value() != 1.0:
            fx.append(("Gamma", {"gamma": self.gamma.value()}))
        if self.temp.value():
            fx.append(("Temperature", {"change": self.temp.value()}))
        if self.sharp.value():
            fx.append(("UnsharpMask", {"radius": 3.0, "amount": self.sharp.value()}))
        if self.gray.isChecked():
            fx.append(("Desaturate", {}))
        if self.auto.isChecked():
            fx.append(("Autocontrast", {}))
        def change():
            it.effects, it.flip_h, it.flip_v = fx, self.fliph.isChecked(), self.flipv.isChecked()
            it.angle = self.angle.value()
        self._edit("Adjust Image", change, merge=True)
        self.refresh()

    # ---- preview ----
    def goto(self, n):
        self.page_no = max(0, min(n, self.layout_.pages - 1))
        self.refresh()

    def _apply_desk(self):
        vp = self.scroll.viewport()
        pal = vp.palette()
        pal.setColor(vp.backgroundRole(), desk_color(self.palette().color(QPalette.ColorRole.Window)))
        vp.setPalette(pal)

    def changeEvent(self, ev):
        if ev.type() == QEvent.Type.PaletteChange:   # light/dark switch while the app is open
            self._apply_desk()
        super().changeEvent(ev)

    def _fit_zoom(self) -> float:
        vp, p = self.scroll.viewport().size(), self.layout_.page
        pad = 2 * self.FIT_PAD
        avail = min((vp.width() - pad) / p.width, (vp.height() - pad) / p.height)  # px per point
        return max(0.1, avail * 72 / self.SCREEN_DPI)

    def set_zoom(self, z):
        self.zoom = None if z is None else max(0.1, min(4.0, z))
        self._rescale_preview()      # instant: stretch the last render
        self._fit_timer.start()      # sharp: one real render once input settles

    # ---- drag a photo on the page onto another to swap them ----
    def _slot_rect(self, r) -> QRectF:
        """A layout slot (points) as a rectangle in the preview widget's coordinates."""
        pm = self.preview.pixmap()
        z = self.zoom if self.zoom is not None else self._fit_zoom()
        k = self.SCREEN_DPI * z / 72                      # screen px per point
        size = pm.deviceIndependentSize()
        ox = (self.preview.width() - size.width()) / 2 + 14   # 14 = the paper margin drawn by _paper
        oy = (self.preview.height() - size.height()) / 2 + 14
        return QRectF(ox + r.x * k, oy + r.y * k, r.w * k, r.h * k)

    def _slots_now(self):
        """[(index into layout_.images, slot rect)] for the page on screen. Photos are matched by identity."""
        imgs = self.layout_.images
        if not imgs or self.preview.pixmap().isNull():
            return []
        try:
            return [(next(i for i, x in enumerate(imgs) if x is it), r) for it, r in self.layout_.slots(self.page_no)]
        except IndexError:
            return []

    def _carousel_shapes(self):
        """{image index: wedge polygon in widget coordinates}, plus the order the wedges are painted in."""
        z = self.zoom if self.zoom is not None else self._fit_zoom()
        k = self.SCREEN_DPI * z / 72
        origin = self._slot_rect(Rect(0, 0, 0, 0)).topLeft()
        wedges = self.layout_.wedges()
        shapes = {i: QPolygonF([QPointF(origin.x() + x * k, origin.y() + y * k) for x, y in poly])
                  for i, (_, poly, _) in enumerate(wedges)}
        return shapes, [*range(0, len(wedges), 2), *range(1, len(wedges), 2)]   # evens first, odds on top

    def _cut_polygons(self):
        """{image index: outline in widget coordinates} for layouts that cut photos to polygons, else {}."""
        polys = self.layout_.outlines(self.page_no) if self.layout_.images else None
        if not polys or self.preview.pixmap().isNull():
            return {}
        z = self.zoom if self.zoom is not None else self._fit_zoom()
        k = self.SCREEN_DPI * z / 72
        o = self._slot_rect(Rect(0, 0, 0, 0)).topLeft()
        idxs = [i for i, _ in self._slots_now()]
        return {i: QPolygonF([QPointF(o.x() + x * k, o.y() + y * k) for x, y in poly]) for i, poly in zip(idxs, polys)}

    def _shape(self, idx, rect):
        """What to draw/test for a slot: the wedge for a Carousel, the outline for a cut layout, else its rectangle."""
        if isinstance(self.layout_, Carousel):
            return self._carousel_shapes()[0][idx]
        cut = self._cut_polygons()
        return cut.get(idx) or self._slot_rect(rect)

    def _slot_at(self, pos):
        slots = self._slots_now()
        if isinstance(self.layout_, Carousel) and slots:
            # wedges overlap: the one painted last (on top) wins, like what the user sees
            shapes, paint_order = self._carousel_shapes()
            by_idx = dict(slots)
            for i in reversed(paint_order):
                if i in by_idx and shapes[i].containsPoint(pos, Qt.FillRule.OddEvenFill):
                    return i, by_idx[i]
            return None
        cut = self._cut_polygons()
        if cut and slots:                                      # a photo is only "hit" inside its slanted shape
            by_idx = dict(slots)
            return next(((i, by_idx[i]) for i in reversed(list(cut)) if i in by_idx
                         and cut[i].containsPoint(pos, Qt.FillRule.OddEvenFill)), None)
        return next(((i, r) for i, r in reversed(slots) if self._slot_rect(r).contains(pos)), None)

    def _sort_by_date(self):
        """Put the photos in the order they were taken (their own date, else the file's date). Undoable."""
        imgs = self.layout_.images
        if len(imgs) < 2 or [id(i) for i in imgs] == [id(i) for i in sorted(imgs, key=date_sort_key)]:
            return
        self._edit("Sort by Date", lambda: imgs.sort(key=date_sort_key))
        self._fill_list()
        self.refresh()

    def _center_photo(self):
        it = self._selected()
        if it and (it.hpan, it.vpan) != (0.5, 0.5):
            def change():
                it.hpan = it.vpan = 0.5
            self._edit("Center Photo", change)
            self.refresh()

    def _swap_images(self, i, j):
        def change():
            imgs = self.layout_.images
            imgs[i], imgs[j] = imgs[j], imgs[i]
        self._edit("Move Image", change)
        self._fill_list()
        self.list.setCurrentRow(j)   # the photo you moved stays selected
        self.refresh()

    def _start_pan(self, hit, pos):
        """Begin repositioning the photo under the cursor, if it is cropped and has room to move."""
        idx, rect = hit
        item = self.layout_.images[idx]
        carousel = isinstance(self.layout_, (Carousel, Slant))   # these always crop, and never rotate
        if not (item.crop or carousel) or isinstance(self.layout_, (Poster, Triptych, Collage)):
            return None
        a = aspect(item)
        ox, oy = crop_overflow(a * 1000, 1000, rect, 0 if carousel else item.turn(rect.w, rect.h))
        if ox < 0.5 and oy < 0.5:
            return None
        z = self.zoom if self.zoom is not None else self._fit_zoom()
        return dict(idx=idx, item=item, start=pos, h0=item.hpan, v0=item.vpan, ox=ox, oy=oy,
                    k=self.SCREEN_DPI * z / 72, before=copy.deepcopy(self.layout_.images), moved=False)

    def _pan_to(self, pos):
        pn = self._pan
        d = pos - pn["start"]
        pn["moved"] = True
        it = pn["item"]
        # dragging the photo right shows more of its left side, so the crop offset goes down
        if pn["ox"] >= 0.5:
            it.hpan = min(1.0, max(0.0, pn["h0"] - d.x() / pn["k"] / pn["ox"]))
        if pn["oy"] >= 0.5:
            it.vpan = min(1.0, max(0.0, pn["v0"] - d.y() / pn["k"] / pn["oy"]))
        if not self._pan_timer.isActive():
            self._pan_timer.start()

    def _end_pan(self):
        pn, self._pan = self._pan, None
        self.preview.setCursor(Qt.CursorShape.ArrowCursor)
        if not pn["moved"]:
            self.list.setCurrentRow(pn["idx"])   # a plain click selects the photo
        elif (pn["item"].hpan, pn["item"].vpan) != (pn["h0"], pn["v0"]):
            self.undo_stack.push(_Edit(self, "Reposition Photo", pn["before"], copy.deepcopy(self.layout_.images)))
            self.refresh()

    # ---- Collage: move and resize photos freely ----
    def _update_collage_actions(self):
        on = isinstance(self.layout_, Collage)
        for a in (self.front_act, self.back_act):
            a.setEnabled(on)

    def _auto_arrange(self):
        if isinstance(self.layout_, Collage) and self.layout_.images:
            self._edit("Auto-Arrange", self.layout_.autoarrange)
            self.refresh()

    def _z_order(self, front: bool):
        row = self.list.currentRow()
        imgs = self.layout_.images
        if not isinstance(self.layout_, Collage) or not 0 <= row < len(imgs):
            return
        to = len(imgs) - 1 if front else 0
        if row == to:
            return
        self._edit("Bring to Front" if front else "Send to Back", lambda: imgs.insert(to, imgs.pop(row)))
        self._fill_list()
        self.list.setCurrentRow(to)
        self.refresh()

    def _pt_to_widget(self, x: float, y: float) -> QPointF:
        return self._slot_rect(Rect(x, y, 0, 0)).topLeft()

    def _box_corners(self, item, slot) -> list[QPointF]:
        """Corners (top-left, top-right, bottom-left, bottom-right) of a Collage box turned by its angle, in widget px."""
        r = self._slot_rect(slot)
        c, t = r.center(), math.radians(item.angle)
        co, si = math.cos(t), math.sin(t)
        return [QPointF(c.x() + sx * r.width() / 2 * co - sy * r.height() / 2 * si,
                        c.y() + sx * r.width() / 2 * si + sy * r.height() / 2 * co)
                for sy in (-1, 1) for sx in (-1, 1)]

    def _rot_handle(self, item, slot) -> tuple[QPointF, QPointF]:
        """(middle of the top edge, the rotate handle 26 px beyond it) of a Collage box, in widget px."""
        pts = self._box_corners(item, slot)
        top = (pts[0] + pts[1]) / 2
        t = math.radians(item.angle)
        return top, QPointF(top.x() + 26 * math.sin(t), top.y() - 26 * math.cos(t))

    def _sel_overlay(self, item, slot) -> list:
        pts = self._box_corners(item, slot)
        top, knob = self._rot_handle(item, slot)
        return [(QPolygonF([pts[0], pts[1], pts[3], pts[2]]), "sel"), (QPolygonF([top, knob]), "sel")] + [
            (QRectF(pt.x() - HANDLE, pt.y() - HANDLE, 2 * HANDLE, 2 * HANDLE), "handle") for pt in pts] + [
            (QRectF(knob.x() - HANDLE - 1, knob.y() - HANDLE - 1, 2 * HANDLE + 2, 2 * HANDLE + 2), "rot")]

    def _on_rot_handle(self, pos, item, slot) -> bool:
        k = self._rot_handle(item, slot)[1]
        return abs(pos.x() - k.x()) <= HANDLE + 4 and abs(pos.y() - k.y()) <= HANDLE + 4

    def _refresh_overlay(self):
        """Selection outline and resize handles for the selected Collage photo."""
        if not isinstance(self.layout_, Collage) or self._cl:
            return
        ov = []
        slots = dict(self._slots_now())
        row = self.list.currentRow()
        if row in slots:
            ov = self._sel_overlay(self.layout_.images[row], slots[row])
        self.preview.overlay = ov
        self.preview.update()

    def _corner_at(self, pos, corners):
        for c, pt in enumerate(corners):
            if abs(pos.x() - pt.x()) <= HANDLE + 3 and abs(pos.y() - pt.y()) <= HANDLE + 3:
                return c
        return None

    def _collage_drag(self, ev):
        """Apply the current mouse position to the photo being moved or resized (snapping unless Ctrl is held)."""
        cl, lay = self._cl, self.layout_
        d = ev.position() - cl["start"]
        if not cl["moved"] and d.manhattanLength() < QApplication.startDragDistance():
            return
        cl["moved"] = True
        dx, dy = d.x() / cl["k"], d.y() / cl["k"]
        x, y, w, h = cl["box0"]
        mods = ev.modifiers()
        free = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        snap = lay.snap and not (mods & Qt.KeyboardModifier.ControlModifier)
        tol = 6 / cl["k"]
        xs, ys = lay.snap_targets(skip=cl["idx"])
        if cl["mode"] == "rotate":                              # the knob follows the mouse; 15 deg steps unless Ctrl
            c = self._slot_rect(Rect(x, y, w, h)).center()
            v = ev.position() - c
            ang = math.degrees(math.atan2(v.x(), -v.y()))
            ang = ang if mods & Qt.KeyboardModifier.ControlModifier else round(ang / 15) * 15
            cl["item"].angle = ang = (ang + 180) % 360 - 180
            self._loading = True                                # show it in the Rotate box without re-triggering an edit
            self.angle.setValue(ang)
            self._loading = False
            new, snap = lay.clamp((x, y, w, h), ang), False
        elif cl["mode"] == "move":
            nx, ny = x + dx, y + dy
            if snap:
                sx = snap_delta([nx, nx + w / 2, nx + w], xs, tol)
                sy = snap_delta([ny, ny + h / 2, ny + h], ys, tol)
                nx += sx or 0.0
                ny += sy or 0.0
            new = lay.clamp((nx, ny, w, h), cl["item"].angle)
        else:
            c = cl["corner"]                                   # 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right
            sx, sy = (1 if c in (1, 3) else -1), (1 if c in (2, 3) else -1)
            ang = cl["item"].angle
            co, si = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            cx0, cy0 = x + w / 2, y + h / 2
            ax, ay = -sx * w / 2, -sy * h / 2                  # the opposite corner stays put (box frame, from the centre)
            mx = sx * w / 2 + dx * co + dy * si                # mouse moved into the box frame
            my = sy * h / 2 - dx * si + dy * co
            if snap and not ang:
                mx += snap_delta([cx0 + mx], xs, tol) or 0.0
                my += snap_delta([cy0 + my], ys, tol) or 0.0
            nw, nh = max(abs(mx - ax), 12.0), max(abs(my - ay), 12.0)
            pg = lay.page                                       # the growing edge stops at the page margin (unturned only)
            room_w = room_h = float("inf")
            if not ang:
                room_w = (pg.left + pg.imageable_w - (cx0 + ax)) if sx > 0 else (cx0 + ax - pg.left)
                room_h = (pg.top + pg.imageable_h - (cy0 + ay)) if sy > 0 else (cy0 + ay - pg.top)
            if free:
                nw, nh = min(nw, room_w), min(nh, room_h)
            else:                                               # keep the photo's shape: follow the bigger change
                k = min(max(nw / w, nh / h), room_w / w, room_h / h)
                nw, nh = w * k, h * k
            lx, ly = ax + sx * nw / 2, ay + sy * nh / 2         # new centre in the box frame, then back to the page
            ncx, ncy = cx0 + lx * co - ly * si, cy0 + lx * si + ly * co
            new = lay.clamp((ncx - nw / 2, ncy - nh / 2, nw, nh), ang)
        cl["item"].box = tuple(new)
        ov = self._sel_overlay(cl["item"], Rect(*new))
        p = lay.page
        top, bottom = self._pt_to_widget(0, 0), self._pt_to_widget(p.width, p.height)
        if snap:                                                # draw the guide lines that were hit
            for v in (new[0], new[0] + new[2] / 2, new[0] + new[2]):
                if any(abs(v - t) < 0.01 for t in xs):
                    gx = self._pt_to_widget(v, 0).x()
                    ov.append((QRectF(gx, top.y(), 0, bottom.y() - top.y()), "guide"))
            for v in (new[1], new[1] + new[3] / 2, new[1] + new[3]):
                if any(abs(v - t) < 0.01 for t in ys):
                    gy = self._pt_to_widget(0, v).y()
                    ov.append((QRectF(top.x(), gy, bottom.x() - top.x(), 0), "guide"))
        self.preview.overlay = ov
        self.preview.update()
        if not self._pan_timer.isActive():
            self._pan_timer.start()                             # live re-render, coalesced

    def _collage_mouse(self, ev) -> bool:
        t, left = ev.type(), Qt.MouseButton.LeftButton
        lay = self.layout_
        if t == QEvent.Type.MouseButtonPress and ev.button() == left:
            pos = ev.position()
            slots = dict(self._slots_now())
            row = self.list.currentRow()
            mode = idx = corner = None
            if row in slots:                                    # the selected photo's handles win over photos beneath
                if self._on_rot_handle(pos, lay.images[row], slots[row]):
                    mode, idx = "rotate", row
                else:
                    corner = self._corner_at(pos, self._box_corners(lay.images[row], slots[row]))
                    if corner is not None:
                        mode, idx = "resize", row
            if mode is None:
                hit = self._slot_at(pos)                        # topmost photo under the cursor
                if hit:
                    mode, idx = "move", hit[0]
            if mode is None:
                return False
            self.list.setCurrentRow(idx)
            z = self.zoom if self.zoom is not None else self._fit_zoom()
            r = slots[idx]
            self._cl = dict(mode=mode, idx=idx, corner=corner, start=pos, box0=(r.x, r.y, r.w, r.h), angle0=lay.images[idx].angle,
                            k=self.SCREEN_DPI * z / 72, before=copy.deepcopy(lay.images), moved=False,
                            item=lay.images[idx])
            if mode == "rotate":
                self.preview.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        if t == QEvent.Type.MouseMove:
            if self._cl and ev.buttons() & left:
                self._collage_drag(ev)
                return True
            if not ev.buttons():
                slots = dict(self._slots_now())
                row = self.list.currentRow()
                corner = self._corner_at(ev.position(), self._box_corners(lay.images[row], slots[row])) if row in slots else None
                if row in slots and self._on_rot_handle(ev.position(), lay.images[row], slots[row]):
                    shape = Qt.CursorShape.OpenHandCursor
                elif corner is not None:
                    shape = Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else Qt.CursorShape.SizeBDiagCursor
                elif self._slot_at(ev.position()):
                    shape = Qt.CursorShape.SizeAllCursor
                else:
                    shape = Qt.CursorShape.ArrowCursor
                self.preview.setCursor(shape)
            return False
        if t == QEvent.Type.MouseButtonRelease and ev.button() == left and self._cl:
            cl, self._cl = self._cl, None
            if cl["moved"] and (cl["item"].box != tuple(cl["box0"]) or cl["item"].angle != cl["angle0"]):
                self.undo_stack.push(_Edit(self, {"move": "Move Photo", "resize": "Resize Photo", "rotate": "Rotate Photo"}[cl["mode"]],
                                           cl["before"], copy.deepcopy(lay.images)))
            self.refresh()
            self._refresh_overlay()
            return True
        return False

    def _page_mouse(self, ev) -> bool:
        if isinstance(self.layout_, Collage):
            return self._collage_mouse(ev)
        t, left = ev.type(), Qt.MouseButton.LeftButton
        # one tiled image / one photo in many slots / free positions: swapping makes no sense
        swap_ok = not isinstance(self.layout_, (Poster, Passport, Triptych, Package, Collage))
        if t == QEvent.Type.MouseButtonPress and ev.button() == left:
            hit = self._slot_at(ev.position())
            self._press, self._pan = None, None
            if hit:
                shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._pan = None if shift else self._start_pan(hit, ev.position())
                if not self._pan and swap_ok:
                    self._press = (hit, ev.position())
            return False
        if t == QEvent.Type.MouseMove:
            if self._pan and ev.buttons() & left:
                if self._pan["moved"] or (ev.position() - self._pan["start"]).manhattanLength() >= QApplication.startDragDistance():
                    self.preview.setCursor(Qt.CursorShape.SizeAllCursor)
                    self._pan_to(ev.position())
                return True
            if self._press and ev.buttons() & left and swap_ok:
                (src, src_rect), start = self._press
                if (ev.position() - start).manhattanLength() >= QApplication.startDragDistance():
                    self._dragging = True
                    self.preview.setCursor(Qt.CursorShape.ClosedHandCursor)
                if self._dragging:
                    hit = self._slot_at(ev.position())
                    self._drop = hit[0] if hit and hit[0] != src else None
                    self.preview.overlay = [(self._shape(src, src_rect), "src")]
                    if self._drop is not None:
                        self.preview.overlay.append((self._shape(hit[0], hit[1]), "dst"))
                    self.preview.update()
                    return True
            elif not ev.buttons():
                self.preview.setCursor(Qt.CursorShape.OpenHandCursor if self._slot_at(ev.position())
                                       else Qt.CursorShape.ArrowCursor)
            return False
        if t == QEvent.Type.MouseButtonRelease and ev.button() == left:
            if self._pan:
                self._end_pan()
                return True
            press, dragging, drop = self._press, self._dragging, self._drop
            self._press, self._dragging, self._drop = None, False, None
            self.preview.overlay = []
            self.preview.update()
            self.preview.setCursor(Qt.CursorShape.ArrowCursor)
            if press:
                src = press[0][0]
                if dragging and drop is not None:
                    self._swap_images(src, drop)
                elif not dragging:
                    self.list.setCurrentRow(src)   # a plain click selects the photo
                return True
        return False

    def eventFilter(self, obj, ev):
        if obj is self.preview:
            return self._page_mouse(ev) if isinstance(ev, QMouseEvent) else False
        if obj is self.scroll.viewport():
            if (ev.type() == QEvent.Type.Wheel and ev.angleDelta().y()
                    and ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
                # Ctrl+wheel = zoom (one notch = 120 units = 1.25x); plain wheel scrolls
                self.set_zoom((self.zoom or self._fit_zoom()) * 1.25 ** (ev.angleDelta().y() / 120))
                return True
            if ev.type() == QEvent.Type.Resize:
                self._place_busy()
            if ev.type() == QEvent.Type.Resize and self.zoom is None:
                self._rescale_preview()
                self._fit_timer.start()  # debounce: re-fit once resizing settles
        return super().eventFilter(obj, ev)

    def _rescale_preview(self):
        """Show the last rendered page stretched to the current zoom, without re-rendering."""
        if self._base is None:
            return
        z = self.zoom if self.zoom is not None else self._fit_zoom()
        r = z / self._base_zoom
        img = self._base.scaled(max(1, round(self._base.width() * r)), max(1, round(self._base.height() * r)),
                                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(self._paper(img, self._dpr))
        self._refresh_overlay()

    def _paper(self, img: QImage, dpr: float) -> QPixmap:
        """The rendered page on a soft shadow with a hairline edge."""
        pad = round(14 * dpr)
        pm = QPixmap(img.width() + 2 * pad, img.height() + 2 * pad)
        pm.fill(Qt.GlobalColor.transparent)
        qp = QPainter(pm)
        for i in range(8):  # cheap stepped shadow, no blur filter needed
            qp.fillRect(pad - i + round(2 * dpr), pad - i + round(3 * dpr), img.width() + 2 * i,
                        img.height() + 2 * i, QColor(0, 0, 0, 9))
        qp.drawImage(pad, pad, img)
        if not self.layout_.images:
            f = QFont(QApplication.font())
            f.setPixelSize(round(16 * dpr))
            qp.setFont(f)
            qp.setPen(QColor(0, 0, 0, 130))
            qp.drawText(pad, pad, img.width(), img.height(), Qt.AlignmentFlag.AlignCenter,
                        "Drop photos here\nor choose File ▸ Add Images…")
        qp.setPen(QColor(0, 0, 0, 70))
        qp.drawRect(pad - 1, pad - 1, img.width() + 1, img.height() + 1)
        qp.end()
        pm.setDevicePixelRatio(dpr)
        return pm

    def _show_busy(self):
        if not self._cold or self._export:   # photos already loaded: just drawing, which is quick
            return
        self.busy.adjustSize()
        self._place_busy()
        self.busy.show()
        self.busy.raise_()

    def _on_progress(self, gen, done, total):
        if gen != self._gen or self._export:
            return
        self.busy_bar.setRange(0, total)
        self.busy_bar.setValue(done)
        self.busy_count.setText(f"{done} of {total}")

    def _place_busy(self):
        self.busy.move((self.scroll.viewport().width() - self.busy.width()) // 2, 12)

    def refresh(self):
        self.page_no = max(0, min(self.page_no, self.layout_.pages - 1))
        self.page_label.setText(f" {self.page_no + 1} / {self.layout_.pages} ")
        if isinstance(self.layout_, Collage):
            self.layout_.slots(0)   # place new photos here, on the UI thread, before the render thread reads them
        self._gen += 1
        dpr = self.devicePixelRatioF()
        z = self.zoom if self.zoom is not None else self._fit_zoom()
        self._dpr, self._pending_zoom = dpr, z
        gen = self._gen
        try:   # only this page's photos that are not cached yet; layouts without slots just skip the notice
            self._cold = uncached([i for i, _ in self.layout_.slots(self.page_no)])
        except Exception:
            self._cold = 0
        self.busy_timer.start()
        self.pool.start(_Job(gen, self.layout_, self.page_no, self.SCREEN_DPI * z * dpr, self.sig,
                             lambda: gen == self._gen))

    def _on_rendered(self, gen, res):
        if gen != self._gen:
            return  # a newer render is on its way
        self.busy_timer.stop()
        if not self._export:
            self.busy.hide()
        if isinstance(res, Exception):
            self.statusBar().show()
            self.statusBar().showMessage(f"Render failed: {res}")
        else:
            self.statusBar().clearMessage()
            self.statusBar().hide()
            self._base, self._base_zoom = res, self._pending_zoom
            self.preview.setPixmap(self._paper(res, self._dpr))
            self._refresh_overlay()

    # ---- files ----
    def open_preset(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open preset", "", "Preset (*.json)")
        if f:
            try:
                self.layout_ = preset.load(f)
            except Exception as e:
                QMessageBox.warning(self, "Open preset", str(e))
                return
            self._sync_controls()
            self.refresh()

    def save_preset(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save preset", "sheet.json", "Preset (*.json)")
        if f:
            try:
                preset.save(self.layout_, f)
            except OSError as e:
                QMessageBox.warning(self, "Save preset", f"Could not save {os.path.basename(f)}: {e}")

    def export(self):
        if self._export:
            return   # one at a time; the popup shows the running one
        if isinstance(self.layout_, Collage):
            self.layout_.slots(0)   # place new photos on the UI thread first, as refresh() does
        job = self._export = _ExportJob(copy.deepcopy(self.layout_), self.sig)   # snapshot: later edits don't leak in
        self._export_pm, self._export_stage = 0, ""
        self.pool.start(job)   # rendering runs while the file dialog is open
        f, _ = QFileDialog.getSaveFileName(self, "Export", "sheet.pdf", "PDF (*.pdf);;PNG (*.png)")
        if not f:
            job.cancel()
            self._export = None
            return
        self._export_name = os.path.basename(f)
        job.choose(f)
        self._export_popup()

    def _export_popup(self):
        self.busy_label.setText(f"Exporting {self._export_name}")
        self.busy_bar.setRange(0, 1000)
        self.busy_bar.setValue(self._export_pm)
        self.busy_count.setText(f"{self._export_stage} · {self._export_pm // 10}%")
        self.busy.adjustSize()
        self._place_busy()
        self.busy.show()
        self.busy.raise_()

    def _on_export_progress(self, pm, stage):
        self._export_pm, self._export_stage = max(self._export_pm, pm), stage
        if stage == "Saving" and not self._creep.isActive():
            self._creep.start()
        if self._export and self._export.path:   # the popup only exists once a path is chosen
            self._export_popup()

    def _creep_tick(self):
        self._export_pm += max(1, (990 - self._export_pm) // 25)   # slows down, never claims to be finished
        if self._export_pm >= 990 or not self._export:
            self._creep.stop()
        if self._export and self._export.path:
            self._export_popup()

    def _on_export_done(self, err):
        self._export = None
        self._creep.stop()
        if err:
            self.busy.hide()
            QMessageBox.warning(self, "Export", f"Could not export {self._export_name}: {err}")
            return
        self.busy_label.setText(f"Exported {self._export_name}")
        self.busy_bar.setRange(0, 1)
        self.busy_bar.setValue(1)
        self.busy_count.setText("Done")
        QTimer.singleShot(2500, lambda: None if self._export else self.busy.hide())

    def print_(self):
        pr = QPrinter(QPrinter.PrinterMode.HighResolution)
        p = self.layout_.page
        pr.setPageSize(QPageSize(QSizeF(p.width, p.height), QPageSize.Unit.Point))
        pr.setFullPage(True)
        if QPrintDialog(pr, self).exec() != QPrintDialog.DialogCode.Accepted:
            return
        dpi = min(pr.resolution(), 300)
        painter = QPainter(pr)
        try:
            for i in range(self.layout_.pages):
                if i:
                    pr.newPage()
                painter.drawImage(painter.viewport(), to_qimage(render_page(self.layout_, i, dpi)))
        except Exception as e:
            QMessageBox.warning(self, "Print", f"Printing stopped: {e}")
        finally:
            painter.end()
