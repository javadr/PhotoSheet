"""Main window: layout controls, image list with per-image effects, live preview, export, print."""
from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QSettings, QSizeF, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QImage, QPageSize, QPainter, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QToolBar, QVBoxLayout, QWidget,
)

from ..core import preset
from ..core.geometry import PageExtent
from ..core.layouts import Carousel, ImageItem, NUp, Poster, Single
from ..core.render import render_page

PAGES = {"A4": (595, 842), "Letter": (612, 792)}
KINDS = {"N-up": NUp, "Single": Single, "Poster": Poster, "Carousel": Carousel}
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


def to_qimage(im: Image.Image) -> QImage:
    im = im.convert("RGB")
    return QImage(im.tobytes(), im.width, im.height, 3 * im.width, QImage.Format.Format_RGB888).copy()


class _Signals(QObject):
    done = Signal(int, object)   # generation, QImage or Exception


class _Job(QRunnable):
    def __init__(self, gen, layout, page, dpi, sig):
        super().__init__()
        self.gen, self.layout, self.page, self.dpi, self.sig = gen, layout, page, dpi, sig

    def run(self):
        try:
            res = to_qimage(render_page(self.layout, self.page, self.dpi))
        except Exception as e:  # shown in the status bar; never crash the worker
            res = e
        try:
            self.sig.done.emit(self.gen, res)
        except RuntimeError:
            pass  # window closed while rendering


class MainWindow(QMainWindow):
    PREVIEW_DPI = 100

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoSheet")
        self.layout_ = NUp(page=PageExtent(*PAGES["A4"]))
        self.layout_.page.set_margins(18, 18, 18, 18)
        self.page_no, self._gen, self._loading = 0, 0, False
        self.sig = _Signals()
        self.sig.done.connect(self._on_rendered)
        self.pool = QThreadPool.globalInstance()

        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName("Page preview")
        scroll = QScrollArea()
        scroll.setWidget(self.preview)
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)
        self._build_menu()
        self._build_layout_dock()
        self._build_image_dock()
        self.statusBar()
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
        tb = QToolBar("Pages")
        self.addToolBar(tb)
        # Glyph-only buttons need a real name for screen readers and Voice Control.
        for text, glyph, step, key in (("Previous Page", "◀", -1, "PgUp"), ("Next Page", "▶", 1, "PgDown")):
            a = act(tb, text, lambda _=False, d=step: self.goto(self.page_no + d), key)
            a.setIconText(glyph)
            a.setToolTip(f"{text} ({key})")
            tb.widgetForAction(a).setAccessibleName(text)
            if step < 0:
                self.page_label = QLabel(" 1 / 1 ")
                self.page_label.setAccessibleName("Current page")
                tb.addWidget(self.page_label)
        v = self.menuBar().addMenu("&View")
        act(v, "Larger Text", lambda: change_text_size(+1))
        act(v, "Smaller Text", lambda: change_text_size(-1))

    def _build_layout_dock(self):
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.page_size = QComboBox()
        self.page_size.addItems(PAGES)
        self.margin = self._spin(0, 200, 18)
        self.rows, self.cols = self._spin(1, 20, 2), self._spin(1, 20, 2)
        self.gutter = self._spin(0, 100, 8)
        self.segments, self.overlap = self._spin(1, 64, 4), self._spin(0, 180, 30)
        self.angle, self.inner = self._spin(-360, 360, 0), self._spin(0, 400, 0)
        self.poster_w, self.poster_h = self._spin(1, 400, 16), self._spin(1, 400, 12)
        self.crop = QCheckBox("Crop to fill")
        form = QFormLayout()
        self.rowsof = {}
        for key, label, w in [
            ("all", "Layout", self.kind), ("all", "Page", self.page_size), ("all", "Margin (pt)", self.margin),
            ("nup", "Rows", self.rows), ("nup", "Columns", self.cols), ("nup", "Gutter (pt)", self.gutter),
            ("carousel", "Segments", self.segments), ("carousel", "Overlap (°)", self.overlap),
            ("carousel", "Angle offset (°)", self.angle), ("carousel", "Inner radius (pt)", self.inner),
            ("poster", "Poster width (in)", self.poster_w), ("poster", "Poster height (in)", self.poster_h),
            ("all", "", self.crop),
        ]:
            form.addRow(label, w)
            self.rowsof.setdefault(key, []).append(w)
            w_changed = getattr(w, "currentIndexChanged", None) or getattr(w, "toggled", None) or w.valueChanged
            w_changed.connect(self._layout_changed)
        box = QWidget()
        box.setLayout(form)
        dock = QDockWidget("Layout", self)
        dock.setWidget(box)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self._show_kind_rows()

    def _build_image_dock(self):
        self.list = QListWidget()
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
        lay.addLayout(form)
        box = QWidget()
        box.setLayout(lay)
        dock = QDockWidget("Images", self)
        dock.setWidget(box)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    @staticmethod
    def _spin(lo, hi, val):
        s = QSpinBox(minimum=lo, maximum=hi)
        s.setValue(val)
        return s

    # ---- layout state ----
    def _show_kind_rows(self):
        k = self.kind.currentText().lower().replace("-", "")
        form = self.rowsof
        for key, widgets in form.items():
            for w in widgets:
                w.parentWidget().layout().setRowVisible(w, key in ("all", k))

    def _layout_changed(self, *_):
        if self._loading:
            return
        self._show_kind_rows()
        old = self.layout_
        cls = KINDS[self.kind.currentText()]
        page = PageExtent(*PAGES[self.page_size.currentText()])
        m = self.margin.value()
        if not page.set_margins(m, m, m, m):
            return
        kw = {}
        if cls is NUp:
            kw = dict(rows=self.rows.value(), cols=self.cols.value(),
                      hgutter=self.gutter.value(), vgutter=self.gutter.value())
        elif cls is Carousel:
            kw = dict(segments=self.segments.value(), overlap=self.overlap.value(),
                      angle_offset=self.angle.value(), inner_radius=self.inner.value())
        elif cls is Poster:
            kw = dict(poster_w=self.poster_w.value() * 72, poster_h=self.poster_h.value() * 72)
        self.layout_ = cls(page=page, **kw)
        for it in old.images:
            it.crop = self.crop.isChecked()
            self.layout_.add(it)
        self.refresh()

    def _sync_controls(self):
        """Push self.layout_ into the widgets (after loading a preset)."""
        self._loading = True
        lay = self.layout_
        name = next(n for n, c in KINDS.items() if type(lay) is c)
        self.kind.setCurrentText(name)
        for n, (w, h) in PAGES.items():
            if (w, h) == (lay.page.width, lay.page.height):
                self.page_size.setCurrentText(n)
        self.margin.setValue(int(lay.page.left))
        for attr, w in [("rows", self.rows), ("cols", self.cols), ("hgutter", self.gutter),
                        ("segments", self.segments), ("overlap", self.overlap),
                        ("angle_offset", self.angle), ("inner_radius", self.inner)]:
            if hasattr(lay, attr):
                w.setValue(int(getattr(lay, attr)))
        if isinstance(lay, Poster):
            self.poster_w.setValue(int(lay.poster_w / 72))
            self.poster_h.setValue(int(lay.poster_h / 72))
        self._loading = False
        self._show_kind_rows()
        self._fill_list()

    # ---- images ----
    def _fill_list(self):
        self.list.clear()
        for it in self.layout_.images:
            self.list.addItem(it.path.rsplit("/", 1)[-1])

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add images", "", IMG_FILTER)
        for f in files:
            self.layout_.add(ImageItem(f, crop=self.crop.isChecked()))
        self._fill_list()
        self.refresh()

    def remove_selected(self):
        r = self.list.currentRow()
        if r >= 0:
            del self.layout_.images[r]
            self._fill_list()
            self.refresh()

    def _reordered(self, *_):
        names = [self.list.item(i).text() for i in range(self.list.count())]
        pool = list(self.layout_.images)
        ordered = []
        for n in names:  # match by filename; duplicates keep relative order
            ordered.append(next(p for p in pool if p.path.rsplit("/", 1)[-1] == n))
            pool.remove(ordered[-1])
        self.layout_.images[:] = ordered
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
        it.effects, it.flip_h, it.flip_v = fx, self.fliph.isChecked(), self.flipv.isChecked()
        self.refresh()

    # ---- preview ----
    def goto(self, n):
        self.page_no = max(0, min(n, self.layout_.pages - 1))
        self.refresh()

    def refresh(self):
        self.page_no = max(0, min(self.page_no, self.layout_.pages - 1))
        self.page_label.setText(f" {self.page_no + 1} / {self.layout_.pages} ")
        self._gen += 1
        self.pool.start(_Job(self._gen, self.layout_, self.page_no, self.PREVIEW_DPI, self.sig))

    def _on_rendered(self, gen, res):
        if gen != self._gen:
            return  # a newer render is on its way
        if isinstance(res, Exception):
            self.statusBar().showMessage(f"Render failed: {res}")
        else:
            self.statusBar().clearMessage()
            self.preview.setPixmap(QPixmap.fromImage(res))

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
