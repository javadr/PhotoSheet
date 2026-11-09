import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PIL import Image

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
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
    original = app.font()
    try:
        change_text_size(-50, persist=False)
        assert app.font().pointSizeF() >= 10.0
        change_text_size(+50, persist=False)
        assert app.font().pointSizeF() <= 24.0
    finally:
        app.setFont(original)  # don't leak a 24 pt font into the other tests


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


def _win_with_images(tmp_path, n=3):
    w = MainWindow()
    for i in range(n):
        p = tmp_path / f"{i}.png"
        Image.new("RGB", (40, 30), (i * 80, 0, 0)).save(p)
    w._add_files([str(tmp_path / f"{i}.png") for i in range(n)])
    return w


def test_remove_undo_redo_and_menu_labels(app, tmp_path):
    w = _win_with_images(tmp_path)
    assert len(w.layout_.images) == 3
    w.list.setCurrentRow(1)
    w.remove_selected()
    assert [i.path[-5:] for i in w.layout_.images] == ["0.png", "2.png"]
    assert w.undo_act.text() == "Undo Remove Image"
    w.undo_stack.undo()
    assert len(w.layout_.images) == 3 and w.list.count() == 3
    w.undo_stack.redo()
    assert len(w.layout_.images) == 2


def test_effect_tweaks_merge_into_one_undo_step_and_undo(app, tmp_path):
    w = _win_with_images(tmp_path, 1)
    w.list.setCurrentRow(0)
    for v in (1.2, 1.4, 1.6):
        w.gamma.setValue(v)
    assert w.layout_.images[0].effects == [("Gamma", {"gamma": 1.6})]
    w.undo_stack.undo()   # one step reverts the whole slider drag
    assert w.layout_.images[0].effects == []


def test_menus_and_panel_toggles(app):
    w = MainWindow()
    titles = [a.text().replace("&", "") for a in w.menuBar().actions()]
    assert titles == ["File", "Edit", "View", "Help"]
    assert w.remove_act.shortcutContext() == Qt.ShortcutContext.WidgetShortcut
    w.layout_dock.close()
    assert not w.layout_dock.isVisibleTo(w)
    w.layout_dock.toggleViewAction().trigger()
    assert w.layout_dock.isVisibleTo(w)


def test_effect_controls_disabled_until_an_image_is_selected(app, tmp_path):
    w = MainWindow()
    w.show()
    assert not any(c.isEnabled() for c in w.fx_widgets) and w.fx_hint.isVisible()
    w = _win_with_images(tmp_path, 1)
    w.show()
    w.list.setCurrentRow(0)
    assert all(c.isEnabled() for c in w.fx_widgets) and not w.fx_hint.isVisible()


def test_dropped_files_filtered_and_added(app, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    img = tmp_path / "a.png"
    Image.new("RGB", (10, 10)).save(img)
    (tmp_path / "notes.txt").write_text("x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(img)), QUrl.fromLocalFile(str(tmp_path / "notes.txt")),
                  QUrl("https://example.com/x.png")])
    from photosheet.ui.main_window import dropped_images
    assert dropped_images(mime) == [str(img)]
    w = MainWindow()
    w._add_files(dropped_images(mime))
    assert len(w.layout_.images) == 1 and w.undo_act.text() == "Undo Add Images"


def test_empty_page_shows_hint_pixels(app):
    w = MainWindow()
    from PySide6.QtGui import QImage
    blank = QImage(300, 300, QImage.Format.Format_RGB888)
    blank.fill(0xFFFFFF)
    px = w._paper(blank, 1.0).toImage()
    dark = sum(1 for x in range(0, 300, 2) for y in range(100, 200, 2) if px.pixelColor(x + 14, y + 14).value() < 200)
    assert dark > 20   # the prompt text was drawn on the page


def test_mouse_wheel_zooms(app):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    w = MainWindow()
    w.resize(1000, 700)
    w.show()
    app.processEvents()
    fit = w._fit_zoom()

    def wheel(dy, mod=Qt.KeyboardModifier.ControlModifier):
        e = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(), QPoint(0, dy),
                        Qt.MouseButton.NoButton, mod,
                        Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(w.scroll.viewport(), e)
    wheel(120)
    assert abs(w.zoom - fit * 1.25) < 1e-6
    wheel(-120)
    wheel(-120)
    assert abs(w.zoom - fit / 1.25) < 1e-6
    w.set_zoom(1.0)
    wheel(120, Qt.KeyboardModifier.NoModifier)   # plain wheel must not zoom
    assert w.zoom == 1.0


def test_zoom_is_instant_and_renders_are_coalesced(app, tmp_path):
    import time
    w = _win_with_images(tmp_path, 1)
    w.resize(1000, 700)
    w.show()
    deadline = time.time() + 5
    while w._base is None and time.time() < deadline:
        app.processEvents()
    assert w._base is not None
    before = w.preview.pixmap().width()
    gen = w._gen
    for _ in range(5):
        w.set_zoom((w.zoom or w._fit_zoom()) * 1.25)
    assert w.preview.pixmap().width() > before     # stretched immediately, no render yet
    assert w._gen == gen                           # five zooms, zero renders started
    time.sleep(0.2)
    app.processEvents()
    assert w._gen == gen + 1                       # one real render after input settled


def test_app_icon_file_loads(app):
    from pathlib import Path

    from PySide6.QtGui import QIcon
    icon = QIcon(str(Path(__import__("photosheet").__file__).parent / "icons" / "photosheet.png"))
    assert not icon.isNull() and icon.availableSizes()


def test_fixed_size_layout_presets_and_roundtrip(app, tmp_path):
    from photosheet.core.layouts import MM, FixedSize, Mosaic
    w = _win_with_images(tmp_path, 2)
    w.kind.setCurrentText("Fixed size")
    assert isinstance(w.layout_, FixedSize) and len(w.layout_.images) == 2
    assert abs(w.layout_.cell_w - 35 * MM) < 1e-6 and abs(w.layout_.cell_h - 45 * MM) < 1e-6
    w.size_preset.setCurrentText("4 × 6 in")
    assert abs(w.layout_.cell_w - 101.6 * MM) < 1e-6 and abs(w.layout_.cell_h - 152.4 * MM) < 1e-6
    w.cell_w.setValue(60)                                   # hand edit -> no longer a named preset
    assert w.size_preset.currentText() == "Custom" and abs(w.layout_.cell_w - 60 * MM) < 1e-6
    from photosheet.core import preset
    f = str(tmp_path / "f.json")
    preset.save(w.layout_, f)
    w.kind.setCurrentText("N-up")
    w.layout_ = preset.load(f)
    w._sync_controls()
    assert w.kind.currentText() == "Fixed size" and abs(w.cell_w.value() - 60) < .06
    w.kind.setCurrentText("Mosaic")
    assert isinstance(w.layout_, Mosaic) and len(w.layout_.images) == 2


def test_layout_rows_follow_selected_kind(app):
    w = MainWindow()
    w.show()
    w.kind.setCurrentText("Mosaic")
    assert w.row_h.isVisible() and w.gutter.isVisible() and not w.cell_w.isVisible() and not w.rows.isVisible()
    w.kind.setCurrentText("Fixed size")
    assert w.cell_w.isVisible() and w.gutter.isVisible() and not w.row_h.isVisible()
    w.kind.setCurrentText("Carousel")
    assert not w.gutter.isVisible() and w.segments.isVisible()
