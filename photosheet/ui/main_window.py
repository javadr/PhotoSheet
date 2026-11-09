"""Main window: layout controls, image list with per-image effects, live preview, export, print."""
from __future__ import annotations

import copy

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QRectF, QRunnable, QSettings, QSizeF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QMouseEvent, QPageSize, QPainter, QPalette, QPen, QFont, QPixmap, QUndoCommand, QUndoStack
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from ..core import preset
from ..core.geometry import PageExtent
from ..core.layouts import MM, Carousel, FixedSize, ImageItem, Mosaic, NUp, Passport, Poster, Single, Strip
from ..core.render import render_page

PAGES = {"A4": (595, 842), "Letter": (612, 792), "A5": (420, 595),
         "4 × 6 in": (288, 432), "5 × 7 in": (360, 504)}   # portrait points; Orientation turns them
KINDS = {"N-up": NUp, "Single": Single, "Poster": Poster, "Carousel": Carousel,
         "Fixed size": FixedSize, "Passport": Passport, "Mosaic": Mosaic, "Photo strip": Strip}
KEYS = {"N-up": "nup", "Single": "single", "Poster": "poster", "Carousel": "carousel",
        "Fixed size": "fixed", "Passport": "passport", "Mosaic": "mosaic", "Photo strip": "strip"}
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


class _Job(QRunnable):
    def __init__(self, gen, layout, page, dpi, sig, current):
        super().__init__()
        self.gen, self.layout, self.page, self.dpi, self.sig = gen, layout, page, dpi, sig
        self.current = current

    def run(self):
        if not self.current():
            return  # superseded while queued: don't burn CPU on it
        try:
            res = to_qimage(render_page(self.layout, self.page, self.dpi, fast=True))
        except Exception as e:  # shown in the status bar; never crash the worker
            res = e
        try:
            self.sig.done.emit(self.gen, res)
        except RuntimeError:
            pass  # window closed while rendering


class _PageLabel(QLabel):
    """Page preview that paints drag-and-drop feedback over the rendered page."""

    def __init__(self):
        super().__init__(alignment=Qt.AlignmentFlag.AlignCenter)
        self.overlay: list[tuple[QRectF, str]] = []   # ("src" dims the picked photo, "dst" outlines the target)
        self.setMouseTracking(True)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.overlay:
            return
        p = QPainter(self)
        hl = self.palette().color(QPalette.ColorRole.Highlight)
        for rect, kind in self.overlay:
            if kind == "src":
                p.fillRect(rect, QColor(0, 0, 0, 90))
            else:
                fill = QColor(hl)
                fill.setAlpha(70)
                p.setPen(QPen(hl, 3))
                p.setBrush(fill)
                p.drawRect(rect)
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
        self.pool = QThreadPool.globalInstance()
        self.undo_stack = QUndoStack(self)

        self.preview = _PageLabel()
        self.preview.setAccessibleName("Page preview")
        self.preview.installEventFilter(self)
        self._press, self._dragging, self._drop = None, False, None   # page drag-and-drop state
        self.zoom: float | None = None   # None = fit to window
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.preview)
        self.scroll.setWidgetResizable(True)
        self.scroll.setBackgroundRole(QPalette.ColorRole.Mid)  # a "desk" so the white paper's edge shows
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
        act(h, "About PhotoSheet", lambda: QMessageBox.about(
            self, "About PhotoSheet", "PhotoSheet\nPrint many photos per sheet, with ICC colour management."))

    def _build_layout_dock(self):
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
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
        self.rows, self.cols = self._spin(1, 20, 2), self._spin(1, 20, 2)
        self.gutter = self._spin(0, 100, 8)
        self.segments, self.overlap = self._spin(1, 64, 4), self._spin(0, 180, 30)
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
        self.strip_w, self.strip_h = self._dspin(20, 400, 51), self._dspin(40, 600, 152)
        self.crop = QCheckBox("Crop all images to fill")
        form = QFormLayout()
        self.rowsof = {}
        for key, label, w in [
            ("all", "Layout", self.kind), ("all", "Page", self.page_size), ("all", "Orientation", self.orient), ("all", "Margin (pt)", self.margin),
            ("nup", "Rows", self.rows), ("nup", "Columns", self.cols), ("nup,fixed,passport,mosaic,strip", "Gutter (pt)", self.gutter),
            ("nup", "Captions", self.caption), ("nup,fixed,passport", "", self.marks),
            ("carousel", "Segments", self.segments), ("carousel", "Overlap (°)", self.overlap),
            ("carousel", "Angle offset (°)", self.angle), ("carousel", "Inner radius (pt)", self.inner),
            ("poster", "Poster width (in)", self.poster_w), ("poster", "Poster height (in)", self.poster_h),
            ("fixed,passport", "Print size", self.size_preset), ("fixed,passport", "Width (mm)", self.cell_w),
            ("fixed,passport", "Height (mm)", self.cell_h), ("passport", "Copies per photo", self.copies),
            ("fixed,passport", "Per sheet", self.per_sheet), ("mosaic", "Row height (mm)", self.row_h),
            ("strip", "Frames per strip", self.frames), ("strip", "Strip width (mm)", self.strip_w),
            ("strip", "Strip height (mm)", self.strip_h),
            ("all", "", self.crop),
        ]:
            form.addRow(label, w)
            self.rowsof.setdefault(key, []).append(w)
            if w is self.size_preset or w is self.per_sheet:
                continue  # wired to _apply_size_preset, which then calls _layout_changed
            w_changed = getattr(w, "currentIndexChanged", None) or getattr(w, "toggled", None) or w.valueChanged
            w_changed.connect(self._layout_changed)
            if w in (self.cell_w, self.cell_h):
                w_changed.connect(self._size_edited)
        box = QWidget()
        box.setLayout(form)
        self.layout_dock = QDockWidget("Layout", self)
        self.layout_dock.setWidget(box)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.layout_dock)
        self._show_kind_rows()

    def _build_image_dock(self):
        self.list = _ImageList()
        self.list.files_dropped.connect(self._add_files)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.model().rowsMoved.connect(self._reordered)
        self.list.currentRowChanged.connect(self._load_effects)
        add, rem = QPushButton("Add…"), QPushButton("Remove")
        add.clicked.connect(self.add_images)
        rem.clicked.connect(self.remove_selected)
        self.gamma = QDoubleSpinBox(minimum=0.1, maximum=5, singleStep=0.1, value=1.0)
        self.temp = self._spin(-2000, 2000, 0)
        self.sharp = QDoubleSpinBox(minimum=0, maximum=5, singleStep=0.1, value=0)
        self.gray = QCheckBox("Desaturate")
        self.fliph, self.flipv = QCheckBox("Flip horizontal"), QCheckBox("Flip vertical")
        self.fx_hint = QLabel("Select an image to adjust it.")
        self.fx_widgets = [self.gamma, self.temp, self.sharp, self.gray, self.fliph, self.flipv]
        form = QFormLayout()
        for label, w in [("Gamma", self.gamma), ("Warm/Cool", self.temp), ("Sharpen", self.sharp),
                         ("", self.gray), ("", self.fliph), ("", self.flipv)]:
            form.addRow(label, w)
            (getattr(w, "toggled", None) or w.valueChanged).connect(self._effects_changed)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        lay = QVBoxLayout()
        lay.addWidget(self.list)
        lay.addLayout(row)
        lay.addWidget(self.fx_hint)
        lay.addLayout(form)
        box = QWidget()
        box.setLayout(lay)
        self.image_dock = QDockWidget("Images", self)
        self.image_dock.setWidget(box)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.image_dock)
        self._load_effects()

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
    def _show_kind_rows(self):
        k = KEYS[self.kind.currentText()]
        for key, widgets in self.rowsof.items():
            for w in widgets:
                w.parentWidget().layout().setRowVisible(w, key == "all" or k in key.split(","))

    def _layout_changed(self, *_):
        if self._loading:
            return
        self._show_kind_rows()
        old = self.layout_
        cls = KINDS[self.kind.currentText()]
        pw, ph = PAGES[self.page_size.currentText()]
        if self.orient.currentText() == "Landscape":
            pw, ph = ph, pw
        page = PageExtent(pw, ph)
        kind = self.kind.currentText()
        if kind == "Passport" and self._last_kind != "Passport":
            for sp, v in ((self.margin, 4), (self.gutter, 4)):   # shop-style sheets are nearly borderless
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        self._last_kind = kind
        m = self.margin.value()
        if not page.set_margins(m, m, m, m):
            return
        kw = {}
        if cls is NUp:
            kw = dict(rows=self.rows.value(), cols=self.cols.value(),
                      hgutter=self.gutter.value(), vgutter=self.gutter.value(),
                      caption=self.caption.currentText().lower(), marks=self.marks.isChecked())
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
                      strip_h=self.strip_h.value() * MM, gutter=self.gutter.value())
        elif cls is Mosaic:
            kw = dict(row_height=self.row_h.value() * MM, gutter=self.gutter.value())
        self.layout_ = cls(page=page, **kw)
        for it in old.images:
            it.crop = self.crop.isChecked()
            self.layout_.add(it)
        self._update_per_sheet()
        self.refresh()

    def _update_per_sheet(self):
        lay = self.layout_
        self.per_sheet.setText(f"{lay.cols} × {lay.rows} = {lay.per_page} photos" if isinstance(lay, FixedSize) else "")

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
                        ("gutter", self.gutter), ("frames", self.frames), ("segments", self.segments), ("overlap", self.overlap),
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
        if hasattr(lay, "caption"):
            self.caption.setCurrentText(lay.caption.capitalize())
        self.marks.setChecked(lay.marks)
        self._loading = False
        self._show_kind_rows()
        self._update_per_sheet()
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

    def _edit(self, text, change, merge=False):
        """Run change() on the image list and record it for undo."""
        before = copy.deepcopy(self.layout_.images)
        change()
        self.undo_stack.push(_Edit(self, text, before, copy.deepcopy(self.layout_.images), merge))

    def _set_images(self, images):
        row = self.list.currentRow()
        self.layout_.images[:] = copy.deepcopy(images)
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
        self._fill_list()
        self.refresh()

    def remove_selected(self):
        r = self.list.currentRow()
        if r >= 0:
            self._edit("Remove Image", lambda: self.layout_.images.pop(r))
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
        def change():
            it.effects, it.flip_h, it.flip_v = fx, self.fliph.isChecked(), self.flipv.isChecked()
        self._edit("Adjust Image", change, merge=True)
        self.refresh()

    # ---- preview ----
    def goto(self, n):
        self.page_no = max(0, min(n, self.layout_.pages - 1))
        self.refresh()

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

    def _slot_at(self, pos):
        return next(((i, r) for i, r in reversed(self._slots_now()) if self._slot_rect(r).contains(pos)), None)

    def _swap_images(self, i, j):
        def change():
            imgs = self.layout_.images
            imgs[i], imgs[j] = imgs[j], imgs[i]
        self._edit("Move Image", change)
        self._fill_list()
        self.list.setCurrentRow(j)   # the photo you moved stays selected
        self.refresh()

    def _page_mouse(self, ev) -> bool:
        if isinstance(self.layout_, (Carousel, Poster, Passport)):  # overlapping wedges / one tiled image / one photo in many slots
            return False
        t, left = ev.type(), Qt.MouseButton.LeftButton
        if t == QEvent.Type.MouseButtonPress and ev.button() == left:
            hit = self._slot_at(ev.position())
            self._press = (hit, ev.position()) if hit else None
            return False
        if t == QEvent.Type.MouseMove:
            if self._press and ev.buttons() & left:
                (src, src_rect), start = self._press
                if (ev.position() - start).manhattanLength() >= QApplication.startDragDistance():
                    self._dragging = True
                    self.preview.setCursor(Qt.CursorShape.ClosedHandCursor)
                if self._dragging:
                    hit = self._slot_at(ev.position())
                    self._drop = hit[0] if hit and hit[0] != src else None
                    self.preview.overlay = [(self._slot_rect(src_rect), "src")]
                    if self._drop is not None:
                        self.preview.overlay.append((self._slot_rect(hit[1]), "dst"))
                    self.preview.update()
                    return True
            elif not ev.buttons():
                self.preview.setCursor(Qt.CursorShape.OpenHandCursor if self._slot_at(ev.position())
                                       else Qt.CursorShape.ArrowCursor)
            return False
        if t == QEvent.Type.MouseButtonRelease and ev.button() == left:
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

    def refresh(self):
        self.page_no = max(0, min(self.page_no, self.layout_.pages - 1))
        self.page_label.setText(f" {self.page_no + 1} / {self.layout_.pages} ")
        self._gen += 1
        dpr = self.devicePixelRatioF()
        z = self.zoom if self.zoom is not None else self._fit_zoom()
        self._dpr, self._pending_zoom = dpr, z
        gen = self._gen
        self.pool.start(_Job(gen, self.layout_, self.page_no, self.SCREEN_DPI * z * dpr, self.sig,
                             lambda: gen == self._gen))

    def _on_rendered(self, gen, res):
        if gen != self._gen:
            return  # a newer render is on its way
        if isinstance(res, Exception):
            self.statusBar().show()
            self.statusBar().showMessage(f"Render failed: {res}")
        else:
            self.statusBar().clearMessage()
            self.statusBar().hide()
            self._base, self._base_zoom = res, self._pending_zoom
            self.preview.setPixmap(self._paper(res, self._dpr))

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
            preset.save(self.layout_, f)

    def export(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export", "sheet.pdf", "PDF (*.pdf);;PNG (*.png)")
        if not f:
            return
        pages = [render_page(self.layout_, i, 300) for i in range(self.layout_.pages)]
        if f.lower().endswith(".pdf"):
            pages[0].save(f, save_all=True, append_images=pages[1:], resolution=300)
        else:
            stem, _, ext = f.rpartition(".")
            for i, im in enumerate(pages):
                im.save(f"{stem}-{i + 1}.{ext}" if len(pages) > 1 else f, dpi=(300, 300))

    def print_(self):
        pr = QPrinter(QPrinter.PrinterMode.HighResolution)
        p = self.layout_.page
        pr.setPageSize(QPageSize(QSizeF(p.width, p.height), QPageSize.Unit.Point))
        pr.setFullPage(True)
        if QPrintDialog(pr, self).exec() != QPrintDialog.DialogCode.Accepted:
            return
        dpi = min(pr.resolution(), 300)
        painter = QPainter(pr)
        for i in range(self.layout_.pages):
            if i:
                pr.newPage()
            painter.drawImage(painter.viewport(), to_qimage(render_page(self.layout_, i, dpi)))
        painter.end()
