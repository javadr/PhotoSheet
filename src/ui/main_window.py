"""Main window: layout controls, image list with per-image effects, live preview, export, print."""
from __future__ import annotations

import copy
import datetime

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QRunnable, QSettings, QSizeF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QMouseEvent, QPageSize, QPainter, QPalette, QPen, QPolygonF, QFont, QPixmap, QUndoCommand, QUndoStack
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from ..core import preset
from ..core.geometry import PageExtent, Rect, crop_overflow
from ..core.layouts import (BENTO, MM, MONTHS, RECIPES, Badges, Bento, Calendar, Carousel, Collage, Feature, Filmstrip,
                            FixedSize, FramedPrint, Honeycomb, ImageItem, Mosaic, NUp, Package, Passport,
                            Polaroid, Poster, Single, Spiral, Strip, Triptych, aspect, snap_delta)
from ..core.render import render_page

PAGES = {"A4": (595, 842), "Letter": (612, 792), "A5": (420, 595),
         "4 × 6 in": (288, 432), "5 × 7 in": (360, 504), "8 × 10 in": (576, 720)}   # portrait points; Orientation turns them
KINDS = {"N-up": NUp, "Single": Single, "Poster": Poster, "Carousel": Carousel,
         "Fixed size": FixedSize, "Passport": Passport, "Mosaic": Mosaic, "Photo strip": Strip, "Polaroid": Polaroid,
         "Triptych": Triptych, "Package": Package, "Collage": Collage, "Framed print": FramedPrint,
         "Badges": Badges, "Feature": Feature, "Film strip": Filmstrip, "Bento": Bento,
         "Golden spiral": Spiral, "Honeycomb": Honeycomb, "Calendar": Calendar}
KEYS = {"N-up": "nup", "Single": "single", "Poster": "poster", "Carousel": "carousel",
        "Fixed size": "fixed", "Passport": "passport", "Mosaic": "mosaic", "Photo strip": "strip", "Polaroid": "polaroid",
        "Triptych": "triptych", "Package": "package", "Collage": "collage", "Framed print": "framed",
        "Badges": "badges", "Feature": "feature", "Film strip": "filmstrip", "Bento": "bento",
        "Golden spiral": "spiral", "Honeycomb": "honeycomb", "Calendar": "calendar"}
HANDLE = 5   # half the size of a Collage resize handle, in px
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
        self.diameter = self._dspin(10, 200, 38)
        self.diameter.setToolTip("Common pin-back badge graphics: 25, 32, 38, 44, 58 and 75 mm.")
        self.staggered = QCheckBox("Staggered rows")
        self.staggered.setChecked(True)
        self.thumbs = self._spin(1, 12, 4)
        self.side = QCheckBox("Small photos on the side")
        self.hero_share = self._spin(20, 90, 70)
        self.direction = QComboBox()
        self.direction.addItems(DIRECTIONS)
        self.direction.setToolTip("Automatic: strips run sideways on a landscape page and top to bottom on a portrait page.")
        self.bento = QComboBox()
        self.bento.addItems([label for label, _, _, _ in BENTO.values()])
        self.spiral_n = self._spin(2, 12, 5)
        self.spiral_share = self._spin(30, 80, 62)
        self.spiral_cw = QCheckBox("Clockwise")
        self.spiral_cw.setChecked(True)
        self.hex_size = self._dspin(10, 150, 40)
        self.cal_year = self._spin(1900, 2200, datetime.date.today().year)
        self.cal_month = QComboBox()
        self.cal_month.addItems(MONTHS)
        self.cal_months = self._spin(1, 24, 12)
        self.cal_week = QComboBox()
        self.cal_week.addItems(["Monday", "Sunday"])
        self.cal_share = self._spin(20, 80, 55)
        self.collage_hint = QLabel("Drag a photo to move it, drag a corner\nto resize (Shift = free shape).")
        self.strip_w, self.strip_h = self._dspin(20, 400, 51), self._dspin(40, 600, 152)
        self.crop = QCheckBox("Crop all images to fill")
        self.crop.setToolTip("With this on, drag a photo on the page to reposition it inside its frame.\n"
                             "Hold Shift while dragging to swap two photos instead.")
        form = QFormLayout()
        self.rowsof = {}
        for key, label, w in [
            ("all", "Layout", self.kind), ("all", "Page", self.page_size), ("all", "Orientation", self.orient), ("all", "Margin (pt)", self.margin),
            ("nup,polaroid", "Rows", self.rows), ("nup,polaroid", "Columns", self.cols),
            ("nup,fixed,passport,mosaic,strip,polaroid,triptych,package,badges,feature,bento,spiral,honeycomb,calendar", "Gutter (pt)", self.gutter),
            ("nup,polaroid,framed", "Captions", self.caption),
            ("nup,fixed,passport,polaroid,package,badges,honeycomb", "", self.marks),
            ("carousel", "Segments", self.segments), ("carousel", "Overlap (°)", self.overlap),
            ("carousel", "Angle offset (°)", self.angle), ("carousel", "Inner radius (pt)", self.inner),
            ("poster", "Poster width (in)", self.poster_w), ("poster", "Poster height (in)", self.poster_h),
            ("fixed,passport", "Print size", self.size_preset), ("fixed,passport", "Width (mm)", self.cell_w),
            ("fixed,passport", "Height (mm)", self.cell_h), ("passport", "Copies per photo", self.copies),
            ("fixed,passport,package,badges,honeycomb", "Per sheet", self.per_sheet), ("mosaic", "Row height (mm)", self.row_h),
            ("strip,filmstrip", "Frames per strip", self.frames), ("strip,filmstrip", "Strip width (mm)", self.strip_w),
            ("strip,filmstrip", "Direction", self.direction),
            ("strip", "Strip length (mm)", self.strip_h),
            ("polaroid", "Border (pt)", self.border), ("triptych", "Panels", self.panels),
            ("triptych", "", self.vertical), ("package", "Sheet", self.recipe),
            ("collage", "", self.snap), ("collage", "", self.arrange_btn), ("collage", "", self.collage_hint),
            ("framed", "Mat width (pt)", self.mat), ("framed", "Mat colour", self.mat_color),
            ("badges", "Diameter (mm)", self.diameter), ("badges", "", self.staggered),
            ("feature", "Small photos", self.thumbs), ("feature", "", self.side), ("feature", "Hero size (%)", self.hero_share),
            ("bento", "Template", self.bento), ("spiral", "Photos per page", self.spiral_n),
            ("spiral", "Big photo share (%)", self.spiral_share), ("spiral", "", self.spiral_cw),
            ("honeycomb", "Cell width (mm)", self.hex_size), ("calendar", "Year", self.cal_year),
            ("calendar", "First month", self.cal_month), ("calendar", "Months", self.cal_months),
            ("calendar", "Week starts on", self.cal_week), ("calendar", "Photo size (%)", self.cal_share),
            ("all", "", self.crop),
        ]:
            form.addRow(label, w)
            self.rowsof.setdefault(key, []).append(w)
            if any(w is x for x in (self.size_preset, self.per_sheet, self.recipe, self.arrange_btn, self.collage_hint)):
                continue  # wired separately (presets, recipe, button) or have no value to watch
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
        self.list.currentRowChanged.connect(lambda *_: self._refresh_overlay())
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
        if kind in ("Passport", "Package") and self._last_kind not in ("Passport", "Package"):
            for sp, v in ((self.margin, 4), (self.gutter, 4)):   # shop-style sheets are nearly borderless
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
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
                      staggered=self.staggered.isChecked(), marks=self.marks.isChecked())
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
            kw = dict(size=self.hex_size.value() * MM, gutter=self.gutter.value(), marks=self.marks.isChecked())
        elif cls is Calendar:
            kw = dict(year=self.cal_year.value(), month=self.cal_month.currentIndex() + 1, months=self.cal_months.value(),
                      week_start=self.cal_week.currentText().lower(), photo_share=self.cal_share.value() / 100,
                      gutter=self.gutter.value())
        self.layout_ = cls(page=page, **kw)
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
        elif isinstance(lay, Honeycomb):
            self.per_sheet.setText(f"{lay.per_page} cells per page" if lay.per_page else "too big for the page")
        else:
            self.per_sheet.setText("")

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
                        ("mat", self.mat), ("thumbs", self.thumbs),
                        ("frames", self.frames), ("segments", self.segments), ("overlap", self.overlap),
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
        if isinstance(lay, Spiral):
            self.spiral_n.setValue(lay.photos)
            self.spiral_share.setValue(round(lay.share * 100))
            self.spiral_cw.setChecked(lay.clockwise)
        if isinstance(lay, Honeycomb):
            self.hex_size.setValue(lay.size / MM)
        if isinstance(lay, Calendar):
            self.cal_year.setValue(lay.year)
            self.cal_month.setCurrentIndex(lay.month - 1)
            self.cal_months.setValue(lay.months)
            self.cal_week.setCurrentText(lay.week_start.capitalize())
            self.cal_share.setValue(round(lay.photo_share * 100))
        if isinstance(lay, Badges):
            self.diameter.setValue(lay.diameter / MM)
            self.staggered.setChecked(lay.staggered)
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

    def _shape(self, idx, rect):
        """What to draw/test for a slot: the wedge for a Carousel, otherwise its rectangle."""
        if isinstance(self.layout_, Carousel):
            return self._carousel_shapes()[0][idx]
        return self._slot_rect(rect)

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
        return next(((i, r) for i, r in reversed(slots) if self._slot_rect(r).contains(pos)), None)

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
        carousel = isinstance(self.layout_, Carousel)   # a Carousel always crops, and never rotates
        if not (item.crop or carousel) or isinstance(self.layout_, (Poster, Triptych, Collage)):
            return None
        a = aspect(item)
        ox, oy = crop_overflow(a * 1000, 1000, rect, 0 if carousel else item.rotation)
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

    def _refresh_overlay(self):
        """Selection outline and resize handles for the selected Collage photo."""
        if not isinstance(self.layout_, Collage) or self._cl:
            return
        ov = []
        slots = dict(self._slots_now())
        row = self.list.currentRow()
        if row in slots:
            r = self._slot_rect(slots[row])
            ov.append((r, "sel"))
            for pt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                ov.append((QRectF(pt.x() - HANDLE, pt.y() - HANDLE, 2 * HANDLE, 2 * HANDLE), "handle"))
        self.preview.overlay = ov
        self.preview.update()

    def _corner_at(self, pos, rect: QRectF):
        corners = (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight())
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
        if cl["mode"] == "move":
            nx, ny = x + dx, y + dy
            if snap:
                sx = snap_delta([nx, nx + w / 2, nx + w], xs, tol)
                sy = snap_delta([ny, ny + h / 2, ny + h], ys, tol)
                nx += sx or 0.0
                ny += sy or 0.0
            new = lay.clamp((nx, ny, w, h))
        else:
            c = cl["corner"]                                   # 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right
            ax = x if c in (1, 3) else x + w                   # the opposite corner stays where it is
            ay = y if c in (2, 3) else y + h
            mx = (x + w if c in (1, 3) else x) + dx
            my = (y + h if c in (2, 3) else y) + dy
            if snap:
                mx += snap_delta([mx], xs, tol) or 0.0
                my += snap_delta([my], ys, tol) or 0.0
            nw, nh = max(abs(mx - ax), 12.0), max(abs(my - ay), 12.0)
            pg = lay.page                                       # the growing edge stops at the page margin;
            room_w = (pg.left + pg.imageable_w - ax) if c in (1, 3) else (ax - pg.left)   # the held corner never moves
            room_h = (pg.top + pg.imageable_h - ay) if c in (2, 3) else (ay - pg.top)
            if free:
                nw, nh = min(nw, room_w), min(nh, room_h)
            else:                                               # keep the photo's shape: follow the bigger change
                k = min(max(nw / w, nh / h), room_w / w, room_h / h)
                nw, nh = w * k, h * k
            nx = ax if c in (1, 3) else ax - nw
            ny = ay if c in (2, 3) else ay - nh
            new = lay.clamp((nx, ny, nw, nh))
        cl["item"].box = tuple(new)
        r = self._slot_rect(Rect(*new))
        ov = [(r, "sel")] + [(QRectF(pt.x() - HANDLE, pt.y() - HANDLE, 2 * HANDLE, 2 * HANDLE), "handle")
                             for pt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight())]
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
                corner = self._corner_at(pos, self._slot_rect(slots[row]))
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
            self._cl = dict(mode=mode, idx=idx, corner=corner, start=pos, box0=(r.x, r.y, r.w, r.h),
                            k=self.SCREEN_DPI * z / 72, before=copy.deepcopy(lay.images), moved=False,
                            item=lay.images[idx])
            return True
        if t == QEvent.Type.MouseMove:
            if self._cl and ev.buttons() & left:
                self._collage_drag(ev)
                return True
            if not ev.buttons():
                slots = dict(self._slots_now())
                row = self.list.currentRow()
                corner = self._corner_at(ev.position(), self._slot_rect(slots[row])) if row in slots else None
                if corner is not None:
                    shape = Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else Qt.CursorShape.SizeBDiagCursor
                elif self._slot_at(ev.position()):
                    shape = Qt.CursorShape.SizeAllCursor
                else:
                    shape = Qt.CursorShape.ArrowCursor
                self.preview.setCursor(shape)
            return False
        if t == QEvent.Type.MouseButtonRelease and ev.button() == left and self._cl:
            cl, self._cl = self._cl, None
            if cl["moved"] and cl["item"].box != tuple(cl["box0"]):
                self.undo_stack.push(_Edit(self, "Move Photo" if cl["mode"] == "move" else "Resize Photo",
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
