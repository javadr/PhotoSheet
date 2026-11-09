import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PIL import Image

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from photosheet.core import preset
from photosheet.core.layouts import Carousel, ImageItem
from photosheet.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_window_switches_layouts_keeps_images_and_renders(app, tmp_path, qtbot=None):
    p = tmp_path / "a.png"
    Image.new("RGB", (80, 60), (0, 0, 255)).save(p)
    w = MainWindow()
    w.layout_.add(ImageItem(str(p)))
    w._fill_list()
    w.kind.setCurrentText("Carousel")
    assert isinstance(w.layout_, Carousel) and len(w.layout_.images) == 1
    w.list.setCurrentRow(0)
    w.gray.setChecked(True)
    assert w.layout_.images[0].effects == [("Desaturate", {})]
    # preset round trip through the UI
    f = str(tmp_path / "s.json")
    preset.save(w.layout_, f)
    w.layout_ = preset.load(f)
    w._sync_controls()
    assert w.kind.currentText() == "Carousel" and w.list.count() == 1


def test_page_buttons_are_named_and_text_never_below_minimum(app):
    from photosheet.ui.main_window import change_text_size
    w = MainWindow()
    from PySide6.QtWidgets import QToolButton
    names = {b.accessibleName() for b in w.findChildren(QToolButton)}
    assert {"Previous Page", "Next Page"} <= names
    change_text_size(-50, persist=False)
    assert app.font().pointSizeF() >= 10.0
    change_text_size(+50, persist=False)
    assert app.font().pointSizeF() <= 24.0


def test_fit_zoom_fits_viewport_and_actual_size_is_96dpi(app):
    w = MainWindow()
    w.resize(1000, 700)
    w.show()
    app.processEvents()
    z = w._fit_zoom()
    vp, p = w.scroll.viewport().size(), w.layout_.page
    px_w, px_h = p.width * z * 96 / 72, p.height * z * 96 / 72
    assert px_w <= vp.width() and px_h <= vp.height()           # whole page visible
    assert abs(px_h - (vp.height() - 2 * w.FIT_PAD)) < 2 or abs(px_w - (vp.width() - 2 * w.FIT_PAD)) < 2
    w.set_zoom(1.0)
    assert w.zoom == 1.0
    w.set_zoom(99)
    assert w.zoom == 4.0
    w.set_zoom(None)
    assert w.zoom is None
