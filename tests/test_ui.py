import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PIL import Image

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
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
    w.rows.setValue(2)      # these tests were written for a 2 x 2 grid and for plain-drag swapping (crop off);
    w.cols.setValue(2)      # the app itself starts at 3 x 3 with "Crop all images to fill" on
    w.crop.setChecked(False)
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


def test_caption_marks_and_strip_controls(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import MM, Strip
    w = _win_with_images(tmp_path, 3)
    w.show()
    assert w.caption.isVisible() and w.marks.isVisible() and not w.frames.isVisible()
    w.caption.setCurrentText("Both")
    w.marks.setChecked(True)
    assert w.layout_.caption == "both" and w.layout_.marks and w.layout_.captions(0)
    w.kind.setCurrentText("Fixed size")
    assert w.layout_.marks and w.marks.isVisible() and not w.caption.isVisible()
    w.kind.setCurrentText("Photo strip")
    assert isinstance(w.layout_, Strip) and len(w.layout_.images) == 3
    assert w.frames.isVisible() and w.strip_w.isVisible() and not w.marks.isVisible()
    w.frames.setValue(3)
    w.strip_h.setValue(200)
    assert w.layout_.frames == 3 and abs(w.layout_.strip_h - 200 * MM) < 1e-6
    f = str(tmp_path / "s.json")
    preset.save(w.layout_, f)
    w.kind.setCurrentText("N-up")
    w.layout_ = preset.load(f)
    w._sync_controls()
    assert w.kind.currentText() == "Photo strip" and w.frames.value() == 3 and abs(w.strip_h.value() - 200) < .06


def _mouse(app, w, kind, pos, mod=Qt.KeyboardModifier.NoModifier):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease}
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    held = left if kind != "release" else none
    ev = QMouseEvent(types[kind], QPointF(pos), QPointF(pos), left if kind != "move" else none, held,
                     mod)
    app.sendEvent(w.preview, ev)


def _ready_window(app, tmp_path, n=3):
    import time
    w = _win_with_images(tmp_path, n)
    w.resize(1100, 800)
    w.show()
    t = time.time() + 5
    while w.preview.pixmap().isNull() and time.time() < t:
        app.processEvents()
    return w


def test_drag_photo_onto_another_swaps_them_and_undoes(app, tmp_path):
    w = _ready_window(app, tmp_path)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    assert names() == ["0.png", "1.png", "2.png"]
    slots = dict(w._slots_now())
    a, c = w._slot_rect(slots[0]).center(), w._slot_rect(slots[2]).center()
    _mouse(app, w, "press", a)
    _mouse(app, w, "move", (a + c) / 2)
    assert w.preview.overlay and w._dragging                   # feedback is showing
    _mouse(app, w, "move", c)
    assert [k for _, k in w.preview.overlay] == ["src", "dst"]
    _mouse(app, w, "release", c)
    assert names() == ["2.png", "1.png", "0.png"] and not w.preview.overlay
    assert w.undo_act.text() == "Undo Move Image" and w.list.currentRow() == 2
    w.undo_stack.undo()
    assert names() == ["0.png", "1.png", "2.png"]


def test_click_selects_and_drop_outside_cancels(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _ready_window(app, tmp_path)
    slots = dict(w._slots_now())
    p1 = w._slot_rect(slots[1]).center()
    _mouse(app, w, "press", p1)
    _mouse(app, w, "release", p1)
    assert w.list.currentRow() == 1                            # plain click selects, nothing moves
    p0 = w._slot_rect(slots[0]).center()
    _mouse(app, w, "press", p0)
    _mouse(app, w, "move", p0 + QPointF(40, 0))
    _mouse(app, w, "release", QPointF(2, 2))                   # released on the desk, outside every photo
    assert [i.path[-5:] for i in w.layout_.images] == ["0.png", "1.png", "2.png"]
    assert not w.undo_stack.canUndo() or w.undo_act.text() == "Undo Add Images"


def test_page_drag_is_safe_when_empty(app, tmp_path):
    w = MainWindow()
    w.show()
    assert w._slots_now() == []                                # empty window: no crash
    w.kind.setCurrentText("Single")
    assert w._slots_now() == []                                # Single with no images used to raise IndexError
    w.kind.setCurrentText("Carousel")
    assert w._slots_now() == [] and w._slot_at(QPointF(100, 100)) is None


def test_page_navigation_sits_centred_below_the_preview(app, tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QToolBar, QToolButton
    w = _win_with_images(tmp_path, 6)       # 2 pages in the default 2x2 layout
    w.resize(1100, 800)
    w.show()
    app.processEvents()
    assert not w.findChildren(QToolBar)                      # nothing left in a top toolbar
    prev, nxt = [next(b for b in w.findChildren(QToolButton) if b.accessibleName() == n)
                 for n in ("Previous Page", "Next Page")]
    g = lambda wd: wd.mapTo(w, QPoint(0, 0))
    scroll_bottom = g(w.scroll).y() + w.scroll.height()
    assert g(prev).y() >= scroll_bottom and g(nxt).y() >= scroll_bottom      # directly below the preview
    mid = (g(prev).x() + g(nxt).x() + nxt.width()) / 2
    assert abs(mid - (g(w.scroll).x() + w.scroll.width() / 2)) < 3            # horizontally centred on it
    nxt.click()
    assert w.page_no == 1 and w.page_label.text().strip() == "2 / 2"
    prev.click()
    assert w.page_no == 0


def test_no_empty_strip_below_navigation_and_errors_still_show(app, tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QToolButton
    w = MainWindow()
    w.resize(1100, 800)
    w.show()
    app.processEvents()
    assert not w.statusBar().isVisible()
    nxt = next(b for b in w.findChildren(QToolButton) if b.accessibleName() == "Next Page")
    gap_below = w.height() - (nxt.mapTo(w, QPoint(0, 0)).y() + nxt.height())
    assert gap_below <= 8                                   # buttons sit at the window's bottom edge
    w._on_rendered(w._gen, RuntimeError("boom"))            # a render error must still be visible
    assert w.statusBar().isVisible() and "boom" in w.statusBar().currentMessage()
    w._on_rendered(w._gen, QImage())                        # next good render clears it again
    assert not w.statusBar().isVisible()


def test_passport_layout_four_by_two_on_landscape_4x6(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import MM, Passport
    w = _win_with_images(tmp_path, 1)
    w.show()
    w.kind.setCurrentText("Passport")
    assert isinstance(w.layout_, Passport) and w.copies.isVisible() and w.per_sheet.isVisible()
    assert (w.margin.value(), w.gutter.value()) == (4, 4)          # switched to shop-style near-borderless
    w.page_size.setCurrentText("4 × 6 in")
    w.orient.setCurrentText("Landscape")
    assert (w.layout_.page.width, w.layout_.page.height) == (432, 288)
    assert w.per_sheet.text() == "4 × 2 = 8 photos"
    assert len(w.layout_.slots(0)) == 8                            # one photo, "Fill sheet"
    w.copies.setValue(3)
    assert w.layout_.copies == 3 and len(w.layout_.slots(0)) == 3
    w.size_preset.setCurrentText("US passport 2 × 2 in")
    assert w.per_sheet.text() == "2 × 1 = 2 photos"
    f = str(tmp_path / "p.json")
    preset.save(w.layout_, f)
    w.kind.setCurrentText("N-up")
    w.orient.setCurrentText("Portrait")
    w.layout_ = preset.load(f)
    w._sync_controls()
    assert (w.kind.currentText(), w.page_size.currentText(), w.orient.currentText(), w.copies.value()) == \
        ("Passport", "4 × 6 in", "Landscape", 3)
    assert abs(w.cell_w.value() - 50.8) < .06 and w.size_preset.currentText() == "US passport 2 × 2 in"


def test_orientation_applies_to_every_layout_and_passport_drag_is_off(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _ready_window(app, tmp_path)
    w.orient.setCurrentText("Landscape")
    assert w.layout_.page.width > w.layout_.page.height
    w.kind.setCurrentText("Passport")
    _mouse(app, w, "press", QPointF(300, 300))
    assert w._press is None


def test_dragging_a_cropped_photo_repositions_it_and_undoes(app, tmp_path):
    from PySide6.QtCore import QPointF
    from photosheet.core.geometry import crop_overflow
    w = _ready_window(app, tmp_path)
    w.crop.setChecked(True)
    assert all(i.crop for i in w.layout_.images)
    for i in w.layout_.images:
        i.rotation = 0            # automatic rotation would turn the wide photo to fit the tall cell
    slots = dict(w._slots_now())
    item = w.layout_.images[0]
    ox, _ = crop_overflow(40 / 30 * 1000, 1000, slots[0], item.rotation)
    assert ox > 100                                           # a wide photo in a tall cell: plenty to pan
    k = w.SCREEN_DPI * w._fit_zoom() / 72
    c = w._slot_rect(slots[0]).center()
    _mouse(app, w, "press", c)
    _mouse(app, w, "move", c + QPointF(-30, 0))
    _mouse(app, w, "move", c + QPointF(-60, 0))               # drag the photo 60 px left
    assert abs(item.hpan - (0.5 + 60 / k / ox)) < 1e-6        # ...so more of its right side shows
    assert item.vpan == 0.5 and not w.preview.overlay          # only the free axis moves; no swap feedback
    _mouse(app, w, "release", c + QPointF(-60, 0))
    assert w.undo_act.text() == "Undo Reposition Photo"
    w.undo_stack.undo()
    assert item.hpan == 0.5 or w.layout_.images[0].hpan == 0.5
    _mouse(app, w, "press", c)
    _mouse(app, w, "move", c + QPointF(5000, 0))              # far past the edge: clamped, never negative
    assert w.layout_.images[0].hpan == 0.0
    _mouse(app, w, "release", c)


def test_shift_drag_swaps_even_when_cropped_and_click_selects(app, tmp_path):
    w = _ready_window(app, tmp_path)
    w.crop.setChecked(True)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    slots = dict(w._slots_now())
    a, c = w._slot_rect(slots[0]).center(), w._slot_rect(slots[2]).center()
    shift = Qt.KeyboardModifier.ShiftModifier
    _mouse(app, w, "press", a, shift)
    _mouse(app, w, "move", c, shift)
    _mouse(app, w, "release", c, shift)
    assert names() == ["2.png", "1.png", "0.png"]
    assert all(i.hpan == 0.5 for i in w.layout_.images)       # nothing was repositioned
    p = w._slot_rect(dict(w._slots_now())[1]).center()
    _mouse(app, w, "press", p)
    _mouse(app, w, "release", p)
    assert w.list.currentRow() == 1                            # a plain click still just selects


def test_center_photo_resets_framing_and_passport_can_pan(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _ready_window(app, tmp_path, 1)
    w.crop.setChecked(True)
    w.list.setCurrentRow(0)
    w.layout_.images[0].hpan = 0.9
    w._center_photo()
    assert w.layout_.images[0].hpan == 0.5 and w.undo_act.text() == "Undo Center Photo"
    w.kind.setCurrentText("Passport")                          # many slots share one photo: panning frames them all
    w.crop.setChecked(True)
    slots = w._slots_now()
    c = w._slot_rect(slots[0][1]).center()
    _mouse(app, w, "press", c)
    _mouse(app, w, "move", c + QPointF(0, -20))               # auto-rotated photo in a portrait cell: only vertical room
    assert w._pan is not None and w.layout_.images[0].vpan != 0.5 and w.layout_.images[0].hpan == 0.5
    _mouse(app, w, "release", c + QPointF(0, -20))


def _carousel_window(app, tmp_path, n=4):
    w = _ready_window(app, tmp_path, n)
    w.kind.setCurrentText("Carousel")
    t = __import__("time").time() + 5
    while __import__("time").time() < t and not w._slots_now():
        app.processEvents()
    return w


def _carousel_points(w):
    """Wedge centres and seams, as widget points. Wedge i spans 90 degrees clockwise from 'up' (45 + 90*i at its middle)."""
    import math

    from photosheet.core.geometry import Rect
    p = w.layout_.page
    cx, cy, r = p.left + p.imageable_w / 2, p.top + p.imageable_h / 2, min(p.imageable_w, p.imageable_h) / 2
    at_angle = lambda deg, f=0.5: w._slot_rect(Rect(cx + r * f * math.sin(math.radians(deg)),
                                                    cy - r * f * math.cos(math.radians(deg)), 0, 0)).topLeft()
    return at_angle


def test_carousel_hit_test_uses_wedges_and_the_top_wedge_wins_in_overlaps(app, tmp_path):
    from photosheet.core.geometry import Rect
    w = _carousel_window(app, tmp_path)
    assert w.layout_.segments == 4 and len(w.layout_.images) == 4
    ang = _carousel_points(w)
    for i in range(4):
        assert w._slot_at(ang(45 + 90 * i))[0] == i             # the middle of each wedge picks that wedge
    # at every seam two wedges overlap (30 degrees); the odd wedge is painted last, so it is the one you see and grab
    assert [w._slot_at(ang(a))[0] for a in (0, 90, 180, 270)] == [3, 1, 1, 3]
    assert w._slot_at(w._slot_rect(Rect(2, 2, 0, 0)).topLeft()) is None   # the page corner is in no wedge


def test_carousel_drag_swaps_wedges_and_pans_the_photo(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _carousel_window(app, tmp_path)
    ang = _carousel_points(w)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    a, c = ang(45), ang(225)                                  # wedge 0 -> wedge 2
    shift = Qt.KeyboardModifier.ShiftModifier
    _mouse(app, w, "press", a, shift)
    _mouse(app, w, "move", c, shift)
    assert [type(s).__name__ for s, _ in w.preview.overlay] == ["QPolygonF", "QPolygonF"]   # wedge-shaped feedback
    _mouse(app, w, "release", c, shift)
    assert names() == ["2.png", "1.png", "0.png", "3.png"]    # wedges 0 and 2 swapped
    item = w.layout_.images[1]                                # no crop checkbox needed: a Carousel always crops
    before = (item.hpan, item.vpan)
    s = ang(135)
    _mouse(app, w, "press", s)
    _mouse(app, w, "move", s + QPointF(-25, -25))
    _mouse(app, w, "move", s + QPointF(-60, -60))
    assert w._pan is not None and (item.hpan, item.vpan) != before
    _mouse(app, w, "release", s + QPointF(-60, -60))
    assert w.undo_act.text() == "Undo Reposition Photo"


def test_carousel_follows_the_number_of_photos_on_add_remove_and_undo(app, tmp_path):
    from photosheet.core.geometry import segment_angles
    w = _win_with_images(tmp_path, 4)
    w.kind.setCurrentText("Carousel")
    assert (w.layout_.segments, w.segments.value()) == (4, 4)       # entering Carousel: one wedge per photo
    w.list.setCurrentRow(1)
    w.remove_selected()
    assert (w.layout_.segments, w.segments.value(), len(w.layout_.wedges())) == (3, 3, 3)
    spans = [b - a for a, b in segment_angles(w.layout_.segments, w.layout_.angle_offset)]
    assert spans == [120, 120, 120]                                  # the circle was re-divided, no gap left
    w.undo_stack.undo()                                              # undo the removal: the wedge comes back
    assert (w.layout_.segments, w.segments.value(), len(w.layout_.wedges())) == (4, 4, 4)
    w.undo_stack.redo()
    assert w.layout_.segments == 3
    while w.layout_.images:                                          # remove everything: never fewer than 1 segment
        w.list.setCurrentRow(0)
        w.remove_selected()
    assert w.layout_.segments == 1 and w.segments.value() == 1
    w._add_files([str(tmp_path / f"{i}.png") for i in range(3)] * 2)   # adding keeps the box in step with the layout
    assert (w.layout_.segments, w.segments.value()) == (6, 6)


def test_manual_segment_count_is_kept_until_the_photos_change(app, tmp_path):
    w = _win_with_images(tmp_path, 3)
    w.kind.setCurrentText("Carousel")
    w.segments.setValue(8)                                           # deliberate: 3 photos, 8 wedges
    assert w.layout_.segments == 8 and len(w.layout_.wedges()) == 3
    w.overlap.setValue(10)                                           # unrelated change must not reset it
    assert w.layout_.segments == 8


def test_desk_contrasts_with_the_white_page_on_light_and_dark_themes_and_ignores_palette_mid(app):
    from PySide6.QtGui import QColor, QPalette
    from photosheet.ui.main_window import desk_color
    for window in ("#faf9f8", "#ffffff", "#eeeeee", "#2b2b2b", "#1e1e1e"):
        d, w = desk_color(QColor(window)), QColor(window)
        assert d != w and abs(d.lightness() - w.lightness()) >= 15, window   # clearly set apart from the window
        assert abs(d.lightness() - 255) >= 15, window                          # and from the white page
    win = MainWindow()
    vp = win.scroll.viewport()
    assert vp.autoFillBackground()
    assert vp.palette().color(vp.backgroundRole()) == desk_color(win.palette().color(QPalette.ColorRole.Window))


# ---- Polaroid, Triptych, Package, Collage ----

def _scale(w):
    return w.SCREEN_DPI * (w.zoom if w.zoom is not None else w._fit_zoom()) / 72   # screen px per point


def _collage_window(app, tmp_path, n=3, boxes=((60, 60, 120, 90), (260, 80, 120, 90), (160, 300, 120, 90)), snap=False):
    w = _ready_window(app, tmp_path, n)
    w.kind.setCurrentText("Collage")
    w.snap.setChecked(snap)
    for it, b in zip(w.layout_.images, boxes):
        it.box = b
    w.refresh()
    return w


def test_new_layouts_keep_photos_show_their_controls_and_roundtrip_presets(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Collage, Package, Polaroid, Triptych
    w = _win_with_images(tmp_path, 3)
    w.show()
    w.kind.setCurrentText("Polaroid")
    assert isinstance(w.layout_, Polaroid) and len(w.layout_.images) == 3
    assert w.border.isVisible() and w.rows.isVisible() and w.caption.isVisible() and w.marks.isVisible()
    w.border.setValue(14)
    w.caption.setCurrentText("Number")
    assert w.layout_.border == 14 and w.layout_.caption == "number" and w.layout_.cards(0)
    w.kind.setCurrentText("Triptych")
    assert isinstance(w.layout_, Triptych) and w.panels.isVisible() and w.vertical.isVisible() and not w.border.isVisible()
    w.panels.setValue(5)
    w.vertical.setChecked(True)
    assert (w.layout_.panels, w.layout_.vertical) == (5, True) and w.layout_.pages == 3
    w.kind.setCurrentText("Package")
    assert isinstance(w.layout_, Package) and w.recipe.isVisible() and w.per_sheet.isVisible() and not w.panels.isVisible()
    assert (w.margin.value(), w.gutter.value()) == (4, 4)
    w.kind.setCurrentText("Collage")
    assert isinstance(w.layout_, Collage) and w.snap.isVisible() and w.arrange_btn.isVisible() and not w.recipe.isVisible()
    assert len(w.layout_.images) == 3 and all(i.box for i in w.layout_.images)
    for setup, check in (
        (lambda: (w.kind.setCurrentText("Polaroid"), w.border.setValue(14)), lambda: (w.kind.currentText(), w.border.value()) == ("Polaroid", 14)),
        (lambda: (w.kind.setCurrentText("Triptych"), w.panels.setValue(4), w.vertical.setChecked(True)),
         lambda: (w.kind.currentText(), w.panels.value(), w.vertical.isChecked()) == ("Triptych", 4, True)),
        (lambda: (w.kind.setCurrentText("Package"), w.recipe.setCurrentIndex(3)),
         lambda: (w.kind.currentText(), w.recipe.currentIndex()) == ("Package", 3)),
        (lambda: (w.kind.setCurrentText("Collage"), w.snap.setChecked(False)),
         lambda: (w.kind.currentText(), w.snap.isChecked()) == ("Collage", False)),
    ):
        setup()
        f = str(tmp_path / "p.json")
        preset.save(w.layout_, f)
        w.kind.setCurrentText("N-up")
        w.snap.setChecked(True)
        w.layout_ = preset.load(f)
        w._sync_controls()
        assert check()


def test_package_recipe_picks_the_paper_and_says_when_prints_do_not_fit(app, tmp_path):
    w = _win_with_images(tmp_path, 1)
    w.kind.setCurrentText("Package")
    w.page_size.setCurrentText("A4")
    w.recipe.setCurrentText("2 × 4×6")                           # choosing a sheet also chooses its paper
    assert w.page_size.currentText() == "8 × 10 in" and (w.layout_.page.width, w.layout_.page.height) == (576, 720)
    assert w.per_sheet.text() == "2 prints, all fit" and len(w.layout_.slots(0)) == 2
    w.page_size.setCurrentText("A5")                              # too small for two 4 x 6
    assert "do not fit" in w.per_sheet.text() and w.layout_.missing >= 1


def test_polaroid_photos_swap_on_the_page_but_triptych_does_not_start_a_drag(app, tmp_path):
    w = _ready_window(app, tmp_path)
    w.kind.setCurrentText("Polaroid")
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    slots = dict(w._slots_now())
    a, c = w._slot_rect(slots[0]).center(), w._slot_rect(slots[2]).center()
    _mouse(app, w, "press", a)
    _mouse(app, w, "move", (a + c) / 2)
    _mouse(app, w, "move", c)
    _mouse(app, w, "release", c)
    assert names() == ["2.png", "1.png", "0.png"]
    w.kind.setCurrentText("Triptych")
    slots = dict(w._slots_now())
    p = w._slot_rect(slots[0]).center()
    _mouse(app, w, "press", p)
    assert w._press is None and w._pan is None


def test_collage_drag_moves_a_photo_and_undoes(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _collage_window(app, tmp_path)
    k = _scale(w)
    slots = dict(w._slots_now())
    c = w._slot_rect(slots[0]).center()
    _mouse(app, w, "press", c)
    assert w.list.currentRow() == 0                              # grabbing a photo selects it
    _mouse(app, w, "move", c + QPointF(15, 10))
    _mouse(app, w, "move", c + QPointF(40, 30))
    _mouse(app, w, "release", c + QPointF(40, 30))
    x, y, bw, bh = w.layout_.images[0].box
    assert abs(x - (60 + 40 / k)) < 1e-6 and abs(y - (60 + 30 / k)) < 1e-6 and (bw, bh) == (120, 90)
    assert w.undo_act.text() == "Undo Move Photo"
    w.undo_stack.undo()
    assert w.layout_.images[0].box == (60, 60, 120, 90)
    _mouse(app, w, "press", c)
    _mouse(app, w, "release", c)                                 # a plain click moves nothing and adds no undo step
    assert w.layout_.images[0].box == (60, 60, 120, 90) and w.undo_act.text() != "Undo Move Photo"


def test_collage_corner_handle_resizes_keeping_shape_or_free_with_shift(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _collage_window(app, tmp_path)
    k = _scale(w)
    w.list.setCurrentRow(0)
    assert [kind for _, kind in w.preview.overlay].count("handle") == 4 and w.preview.overlay[0][1] == "sel"
    br = w._slot_rect(dict(w._slots_now())[0]).bottomRight()
    _mouse(app, w, "press", br)
    _mouse(app, w, "move", br + QPointF(20, 2))
    _mouse(app, w, "move", br + QPointF(60, 5))
    _mouse(app, w, "release", br + QPointF(60, 5))
    x, y, bw, bh = w.layout_.images[0].box
    assert (x, y) == (60, 60)                                    # the opposite (top-left) corner stays put
    assert abs(bw / bh - 120 / 90) < 1e-9 and abs(bw - (120 + 60 / k)) < 1e-6   # same shape, grew by the bigger change
    assert w.undo_act.text() == "Undo Resize Photo"
    w.undo_stack.undo()
    br = w._slot_rect(dict(w._slots_now())[0]).bottomRight()
    shift = Qt.KeyboardModifier.ShiftModifier
    _mouse(app, w, "press", br)
    _mouse(app, w, "move", br + QPointF(20, 2), shift)
    _mouse(app, w, "move", br + QPointF(60, 5), shift)
    _mouse(app, w, "release", br + QPointF(60, 5), shift)
    x, y, bw, bh = w.layout_.images[0].box
    assert abs(bw - (120 + 60 / k)) < 1e-6 and abs(bh - (90 + 5 / k)) < 1e-6   # Shift: width and height change on their own
    tl = w._slot_rect(dict(w._slots_now())[0]).topLeft()                         # a left-side handle anchors the right edge
    right_before = w.layout_.images[0].box[0] + w.layout_.images[0].box[2]
    _mouse(app, w, "press", tl)
    _mouse(app, w, "move", tl + QPointF(-15, -10))
    _mouse(app, w, "move", tl + QPointF(-30, -20))
    _mouse(app, w, "release", tl + QPointF(-30, -20))
    nx, ny, nw, nh = w.layout_.images[0].box
    assert abs(nx + nw - right_before) < 1e-6


def test_collage_snaps_to_guides_and_ctrl_turns_it_off(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _collage_window(app, tmp_path, boxes=((100, 100, 120, 90), (360, 200, 120, 90), (160, 500, 120, 90)), snap=True)
    k = _scale(w)
    left = w.layout_.page.left
    dx_pt = -(100 - left) + 2                                    # drop the photo 2 pt off the page margin
    c = w._slot_rect(dict(w._slots_now())[0]).center()
    ctrl = Qt.KeyboardModifier.ControlModifier
    for mod, expect in ((Qt.KeyboardModifier.NoModifier, left), (ctrl, 100 + dx_pt)):
        w.layout_.images[0].box = (100, 100, 120, 90)
        w.refresh()
        c = w._slot_rect(dict(w._slots_now())[0]).center()
        _mouse(app, w, "press", c, mod)
        _mouse(app, w, "move", c + QPointF(dx_pt * k / 2, 0), mod)
        _mouse(app, w, "move", c + QPointF(dx_pt * k, 0), mod)
        if mod == Qt.KeyboardModifier.NoModifier:
            assert any(kind == "guide" for _, kind in w.preview.overlay)           # the guide line shows while dragging
        _mouse(app, w, "release", c + QPointF(dx_pt * k, 0), mod)
        assert abs(w.layout_.images[0].box[0] - expect) < 1e-6, mod
        assert w.layout_.images[0].box[1] == 100                                   # y had nothing close to snap to
    # snapping to another photo: line up with photo 1's left edge
    w.layout_.images[0].box = (100, 100, 120, 90)
    w.refresh()
    c = w._slot_rect(dict(w._slots_now())[0]).center()
    dx = 360 - 100 - 3                                                             # 3 pt short of x = 360
    _mouse(app, w, "press", c)
    _mouse(app, w, "move", c + QPointF(dx * k / 2, 0))
    _mouse(app, w, "move", c + QPointF(dx * k, 0))
    _mouse(app, w, "release", c + QPointF(dx * k, 0))
    assert abs(w.layout_.images[0].box[0] - 360) < 1e-6


def test_collage_bring_to_front_send_to_back_and_auto_arrange(app, tmp_path):
    w = _collage_window(app, tmp_path)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    assert w.front_act.isEnabled() and w.back_act.isEnabled()
    w.list.setCurrentRow(0)
    w._z_order(True)
    assert names() == ["1.png", "2.png", "0.png"] and w.list.currentRow() == 2          # last = drawn on top
    assert w.undo_act.text() == "Undo Bring to Front"
    w._z_order(False)
    assert names() == ["0.png", "1.png", "2.png"] and w.list.currentRow() == 0
    w.undo_stack.undo()
    assert names() == ["1.png", "2.png", "0.png"]
    for it in w.layout_.images:
        it.box = (50, 50, 200, 150)                                                      # a pile
    w.arrange_btn.click()
    rects = [r for _, r in w.layout_.slots(0)]
    pg = w.layout_.page
    for i, a in enumerate(rects):
        assert a.x >= pg.left - 1e-6 and a.y >= pg.top - 1e-6 and a.x + a.w <= pg.width - pg.right + 1e-6
        for b in rects[i + 1:]:
            assert a.x + a.w <= b.x + 1e-6 or b.x + b.w <= a.x + 1e-6 or a.y + a.h <= b.y + 1e-6 or b.y + b.h <= a.y + 1e-6
    assert w.undo_act.text() == "Undo Auto-Arrange"
    w.kind.setCurrentText("N-up")
    assert not w.front_act.isEnabled() and not w.back_act.isEnabled() and w.preview.overlay == []


def test_collage_resize_stops_at_the_page_margin_and_keeps_the_held_corner(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _collage_window(app, tmp_path)
    pg = w.layout_.page
    k = _scale(w)
    w.list.setCurrentRow(0)                                      # handles belong to the selected photo
    x0, y0, bw, bh = w.layout_.images[0].box
    br = w._slot_rect(dict(w._slots_now())[0]).bottomRight()
    far = QPointF(5000, 5000)                                    # far beyond the page
    _mouse(app, w, "press", br)
    _mouse(app, w, "move", br + QPointF(20, 20))
    _mouse(app, w, "move", br + far)
    _mouse(app, w, "release", br + far)
    x, y, nw, nh = w.layout_.images[0].box
    assert (x, y) == (x0, y0)                                    # top-left held still
    assert x + nw <= pg.left + pg.imageable_w + 1e-6 and y + nh <= pg.top + pg.imageable_h + 1e-6
    assert abs(nw / nh - bw / bh) < 1e-9 and nw > bw             # grew as far as the page allows, same shape


# ---- Framed print, Badges, Feature, Film strip ----

def test_last_four_layouts_keep_photos_show_their_controls_and_roundtrip_presets(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import MM, Badges, Feature, Filmstrip, FramedPrint
    w = _win_with_images(tmp_path, 5)
    w.show()
    w.kind.setCurrentText("Framed print")
    assert isinstance(w.layout_, FramedPrint) and len(w.layout_.images) == 5
    assert w.mat.isVisible() and w.mat_color.isVisible() and w.caption.isVisible() and not w.diameter.isVisible()
    w.mat.setValue(60)
    w.mat_color.setCurrentText("Black")
    w.caption.setCurrentText("Number")
    assert (w.layout_.mat, w.layout_.color, w.layout_.caption) == (60, "black", "number") and w.layout_.pages == 5
    w.kind.setCurrentText("Badges")
    assert isinstance(w.layout_, Badges) and w.diameter.isVisible() and w.staggered.isVisible() and w.marks.isVisible()
    assert w.per_sheet.isVisible() and "badges per page" in w.per_sheet.text() and not w.mat.isVisible()
    w.diameter.setValue(58)
    w.staggered.setChecked(False)
    assert abs(w.layout_.diameter - 58 * MM) < 1e-6 and not w.layout_.staggered
    n_square = w.layout_.per_page
    w.staggered.setChecked(True)
    assert w.layout_.per_page >= n_square and str(w.layout_.per_page) in w.per_sheet.text()
    w.diameter.setValue(200)                                      # bigger than the page
    assert w.per_sheet.text() == "too big for the page" and w.layout_.slots(0) == []
    w.diameter.setValue(38)
    w.kind.setCurrentText("Feature")
    assert isinstance(w.layout_, Feature) and w.thumbs.isVisible() and w.side.isVisible() and w.hero_share.isVisible()
    w.thumbs.setValue(2)
    w.side.setChecked(True)
    w.hero_share.setValue(60)
    assert (w.layout_.thumbs, w.layout_.side, w.layout_.hero_share) == (2, True, 0.6) and w.layout_.pages == 2
    for setup, check in (
        (lambda: (w.kind.setCurrentText("Framed print"), w.mat.setValue(44), w.mat_color.setCurrentText("Cream")),
         lambda: (w.kind.currentText(), w.mat.value(), w.mat_color.currentText()) == ("Framed print", 44, "Cream")),
        (lambda: (w.kind.setCurrentText("Badges"), w.diameter.setValue(44), w.staggered.setChecked(False)),
         lambda: (w.kind.currentText(), round(w.diameter.value()), w.staggered.isChecked()) == ("Badges", 44, False)),
        (lambda: (w.kind.setCurrentText("Feature"), w.thumbs.setValue(3), w.side.setChecked(False), w.hero_share.setValue(55)),
         lambda: (w.kind.currentText(), w.thumbs.value(), w.side.isChecked(), w.hero_share.value()) == ("Feature", 3, False, 55)),
    ):
        setup()
        f = str(tmp_path / "p.json")
        preset.save(w.layout_, f)
        w.kind.setCurrentText("N-up")
        w.layout_ = preset.load(f)
        w._sync_controls()
        assert check()


def test_film_strip_sets_35mm_defaults_and_booth_strip_gets_its_own_back(app, tmp_path):
    from photosheet.core.layouts import MM, Filmstrip, Strip
    w = _win_with_images(tmp_path, 7)
    w.show()
    w.kind.setCurrentText("Photo strip")
    assert (w.strip_w.value(), w.frames.value()) == (51, 4)
    w.kind.setCurrentText("Film strip")
    assert type(w.layout_) is Filmstrip and (w.strip_w.value(), w.frames.value()) == (35, 6)
    assert w.frames.isVisible() and w.strip_w.isVisible() and not w.strip_h.isVisible()    # height follows from the frames
    assert abs(w.layout_.strip_w - 35 * MM) < 1e-6 and len(w.layout_.slots(0)) == 7
    w.frames.setValue(3)
    assert w.layout_.frames == 3 and w.layout_.strip_h < 35 * MM * 6
    w.kind.setCurrentText("Photo strip")                                                  # switching back restores booth defaults
    assert type(w.layout_) is Strip and (w.strip_w.value(), w.frames.value()) == (51, 4) and w.strip_h.isVisible()
    w.kind.setCurrentText("N-up")
    w.kind.setCurrentText("Photo strip")
    w.strip_w.setValue(60)                                                                 # a tuned booth strip survives other layouts
    w.kind.setCurrentText("N-up")
    w.kind.setCurrentText("Photo strip")
    assert w.strip_w.value() == 60


def test_page_drag_swaps_badges_and_feature_photos(app, tmp_path):
    w = _ready_window(app, tmp_path)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    for kind in ("Badges", "Feature"):
        w.layout_.images[:] = sorted(w.layout_.images, key=lambda i: i.path)
        w.kind.setCurrentText(kind)
        slots = dict(w._slots_now())
        a, c = w._slot_rect(slots[0]).center(), w._slot_rect(slots[2]).center()
        _mouse(app, w, "press", a)
        _mouse(app, w, "move", (a + c) / 2)
        _mouse(app, w, "move", c)
        _mouse(app, w, "release", c)
        assert names() == ["2.png", "1.png", "0.png"], kind


def test_strip_direction_follows_the_page_by_default_and_the_selector_overrides_it(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Filmstrip, Strip
    w = _win_with_images(tmp_path, 7)
    w.show()
    for kind, cls in (("Film strip", Filmstrip), ("Photo strip", Strip)):
        w.kind.setCurrentText(kind)
        assert type(w.layout_) is cls and w.direction.isVisible() and w.direction.currentText() == "Automatic"
        assert not w.layout_.horizontal                                           # portrait page
        w.orient.setCurrentText("Landscape")
        assert w.layout_.horizontal                                               # the same setting now runs sideways
        a, b = (r for _, r in w.layout_.slots(0)[:2])
        assert a.y == b.y and b.x > a.x                                           # frames in a row
        w.direction.setCurrentText("Vertical")                                    # forced back, still on a landscape page
        assert not w.layout_.horizontal
        a, b = (r for _, r in w.layout_.slots(0)[:2])
        assert a.x == b.x and b.y > a.y
        w.orient.setCurrentText("Portrait")
        w.direction.setCurrentText("Horizontal")                                  # forced sideways on a portrait page
        assert w.layout_.horizontal
        f = str(tmp_path / "p.json")
        preset.save(w.layout_, f)
        w.kind.setCurrentText("N-up")
        w.direction.setCurrentText("Automatic")
        w.layout_ = preset.load(f)
        w._sync_controls()
        assert w.kind.currentText() == kind and w.direction.currentText() == "Horizontal" and w.layout_.horizontal
        w.direction.setCurrentText("Automatic")
    assert not w.direction.isVisible() or w.kind.currentText() in ("Photo strip", "Film strip")
    w.kind.setCurrentText("N-up")
    assert not w.direction.isVisible()


# ---- Bento, Golden spiral, Honeycomb, Calendar ----

def test_last_four_layouts_keep_photos_show_controls_and_roundtrip_presets(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import MM, BENTO, Bento, Calendar, Honeycomb, Spiral
    w = _win_with_images(tmp_path, 6)
    w.show()
    w.kind.setCurrentText("Bento")
    assert isinstance(w.layout_, Bento) and len(w.layout_.images) == 6
    assert w.bento.isVisible() and w.gutter.isVisible() and not w.spiral_n.isVisible() and not w.cal_year.isVisible()
    w.bento.setCurrentIndex(list(BENTO).index("magazine"))
    assert w.layout_.template == "magazine" and w.layout_.pages == 2 and len(w.layout_.slots(0)) == 5
    w.kind.setCurrentText("Golden spiral")
    assert isinstance(w.layout_, Spiral) and w.spiral_n.isVisible() and w.spiral_share.isVisible() and w.spiral_cw.isVisible()
    w.spiral_n.setValue(3)
    w.spiral_share.setValue(50)
    w.spiral_cw.setChecked(False)
    assert (w.layout_.photos, w.layout_.share, w.layout_.clockwise) == (3, 0.5, False) and w.layout_.pages == 2
    w.kind.setCurrentText("Honeycomb")
    assert isinstance(w.layout_, Honeycomb) and w.hex_size.isVisible() and w.marks.isVisible() and w.per_sheet.isVisible()
    assert "cells per page" in w.per_sheet.text()
    w.hex_size.setValue(60)
    assert abs(w.layout_.size - 60 * MM) < 1e-6
    n = w.layout_.per_page
    w.hex_size.setValue(30)
    assert w.layout_.per_page > n                                                   # smaller cells: more per page
    w.kind.setCurrentText("Calendar")
    assert isinstance(w.layout_, Calendar) and w.cal_year.isVisible() and w.cal_month.isVisible() and not w.hex_size.isVisible()
    w.cal_year.setValue(2028)
    w.cal_month.setCurrentIndex(10)                                                 # November
    w.cal_months.setValue(4)
    w.cal_week.setCurrentText("Sunday")
    w.cal_share.setValue(45)
    lay = w.layout_
    assert (lay.year, lay.month, lay.months, lay.week_start, lay.photo_share) == (2028, 11, 4, "sunday", 0.45)
    assert lay.pages == 4 and w.page_label.text().strip().endswith("/ 4")
    for setup, check in (
        (lambda: (w.kind.setCurrentText("Bento"), w.bento.setCurrentIndex(3)),
         lambda: (w.kind.currentText(), w.bento.currentIndex()) == ("Bento", 3)),
        (lambda: (w.kind.setCurrentText("Golden spiral"), w.spiral_n.setValue(7), w.spiral_cw.setChecked(False)),
         lambda: (w.kind.currentText(), w.spiral_n.value(), w.spiral_cw.isChecked()) == ("Golden spiral", 7, False)),
        (lambda: (w.kind.setCurrentText("Honeycomb"), w.hex_size.setValue(55)),
         lambda: (w.kind.currentText(), round(w.hex_size.value())) == ("Honeycomb", 55)),
        (lambda: (w.kind.setCurrentText("Calendar"), w.cal_year.setValue(2031), w.cal_month.setCurrentIndex(5), w.cal_week.setCurrentText("Monday")),
         lambda: (w.kind.currentText(), w.cal_year.value(), w.cal_month.currentIndex(), w.cal_week.currentText()) == ("Calendar", 2031, 5, "Monday")),
    ):
        setup()
        f = str(tmp_path / "p.json")
        preset.save(w.layout_, f)
        w.kind.setCurrentText("N-up")
        w.layout_ = preset.load(f)
        w._sync_controls()
        assert check()


def test_page_drag_swaps_bento_and_spiral_photos_and_calendar_pages_navigate(app, tmp_path):
    w = _ready_window(app, tmp_path)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    for kind in ("Bento", "Golden spiral"):
        w.layout_.images[:] = sorted(w.layout_.images, key=lambda i: i.path)
        w.kind.setCurrentText(kind)
        slots = dict(w._slots_now())
        a, c = w._slot_rect(slots[0]).center(), w._slot_rect(slots[2]).center()
        _mouse(app, w, "press", a)
        _mouse(app, w, "move", (a + c) / 2)
        _mouse(app, w, "move", c)
        _mouse(app, w, "release", c)
        assert names() == ["2.png", "1.png", "0.png"], kind
    w.kind.setCurrentText("Calendar")
    assert w.layout_.pages == 12 and w.page_label.text().strip() == "1 / 12"
    w.goto(5)
    assert w.page_no == 5 and w.page_label.text().strip() == "6 / 12"
    assert len(w.layout_.slots(5)) == 1 and w.layout_.slots(5)[0][0] is w.layout_.images[5 % 3]   # photos repeat across months


def test_self_test_passes_in_the_source_tree_and_reports_failures(app, tmp_path, monkeypatch):
    from photosheet import selftest
    report = tmp_path / "report.txt"
    code = selftest.run(str(report))
    text = report.read_text(encoding="utf-8")
    assert code == 0 and "SELF-TEST PASSED" in text and "FAIL" not in text, text
    for step in ("data files", "colour management and effects", "every layout renders", "PDF export", "window and preview"):
        assert any(line.startswith("ok") and step in line for line in text.splitlines()), step
    # a broken bundle must fail loudly: pretend the icons were not packaged
    import photosheet
    monkeypatch.setattr(photosheet, "__file__", str(tmp_path / "__init__.py"))
    assert selftest.run(str(report)) == 1
    assert "FAIL  data files" in report.read_text(encoding="utf-8")


# ---- Masonry, Shapes, Ring, Scatter ----

def test_masonry_shapes_ring_scatter_keep_photos_show_controls_and_roundtrip_presets(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Masonry, Ring, Scatter, Shapes
    w = _win_with_images(tmp_path, 7)
    w.show()
    w.kind.setCurrentText("Masonry")
    assert isinstance(w.layout_, Masonry) and len(w.layout_.images) == 7
    assert w.cols.isVisible() and w.gutter.isVisible() and not w.rows.isVisible() and not w.shape_kind.isVisible()
    w.cols.setValue(4)
    assert w.layout_.columns == 4
    w.kind.setCurrentText("Shapes")
    assert isinstance(w.layout_, Shapes) and w.shape_kind.isVisible() and w.rows.isVisible() and w.cols.isVisible()
    w.rows.setValue(2)
    w.cols.setValue(2)
    w.shape_kind.setCurrentText("Star")
    assert (w.layout_.kind, w.layout_.rows, w.layout_.cols) == ("star", 2, 2) and w.layout_.pages == 2
    assert w.per_sheet.isVisible() and w.per_sheet.text() == "2 × 2 = 4 photos"
    w.shape_kind.setCurrentText("Rounded square")
    assert w.layout_.kind == "rounded"
    w.kind.setCurrentText("Ring")
    assert isinstance(w.layout_, Ring) and w.ring_n.isVisible() and w.ring_center.isVisible() and w.marks.isVisible()
    assert not w.shape_kind.isVisible() and "photos per page" in w.per_sheet.text()
    assert w.layout_.photos == 8 and w.layout_.per_page == 9 and "centre needs" not in w.per_sheet.text()
    w.ring_n.setValue(4)
    assert "the centre needs 6 or more" in w.per_sheet.text() and w.layout_.per_page == 4        # said out loud, not silent
    w.ring_center.setChecked(False)
    assert "centre needs" not in w.per_sheet.text()
    w.kind.setCurrentText("Scatter")
    assert isinstance(w.layout_, Scatter) and w.sc_n.isVisible() and w.sc_seed.isVisible() and w.shuffle_btn.isVisible()
    w.sc_n.setValue(3)
    w.sc_size.setValue(30)
    w.sc_tilt.setValue(20)
    w.sc_border.setValue(5)
    w.sc_seed.setValue(42)
    lay = w.layout_
    assert (lay.photos, lay.size, lay.tilt, lay.border, lay.seed) == (3, 0.3, 20, 5, 42) and lay.pages == 3
    before = [(r.x, r.y) for _, r, _, _ in w.layout_.pile(0)]
    w.shuffle_btn.click()
    after = [(r.x, r.y) for _, r, _, _ in w.layout_.pile(0)]
    assert w.layout_.seed == w.sc_seed.value() and (w.layout_.seed != 42 or before != after)
    w.sc_seed.setValue(42)
    assert [(r.x, r.y) for _, r, _, _ in w.layout_.pile(0)] == before                          # the seed brings the same pile back
    for setup, check in (
        (lambda: (w.kind.setCurrentText("Masonry"), w.cols.setValue(5)),
         lambda: (w.kind.currentText(), w.cols.value()) == ("Masonry", 5)),
        (lambda: (w.kind.setCurrentText("Shapes"), w.shape_kind.setCurrentText("Diamond"), w.rows.setValue(3), w.cols.setValue(3)),
         lambda: (w.kind.currentText(), w.shape_kind.currentText(), w.rows.value(), w.cols.value()) == ("Shapes", "Diamond", 3, 3)),
        (lambda: (w.kind.setCurrentText("Ring"), w.ring_n.setValue(12), w.ring_center.setChecked(False)),
         lambda: (w.kind.currentText(), w.ring_n.value(), w.ring_center.isChecked()) == ("Ring", 12, False)),
        (lambda: (w.kind.setCurrentText("Scatter"), w.sc_n.setValue(5), w.sc_size.setValue(60), w.sc_tilt.setValue(8), w.sc_border.setValue(11), w.sc_seed.setValue(77)),
         lambda: (w.kind.currentText(), w.sc_n.value(), w.sc_size.value(), w.sc_tilt.value(), w.sc_border.value(), w.sc_seed.value()) == ("Scatter", 5, 60, 8, 11, 77)),
    ):
        setup()
        f = str(tmp_path / "p.json")
        preset.save(w.layout_, f)
        w.kind.setCurrentText("N-up")
        w.layout_ = preset.load(f)
        w._sync_controls()
        assert check()


def test_page_drag_swaps_masonry_shapes_ring_and_scatter_photos(app, tmp_path):
    w = _ready_window(app, tmp_path)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    for kind in ("Masonry", "Shapes", "Ring", "Scatter"):
        w.layout_.images[:] = sorted(w.layout_.images, key=lambda i: i.path)
        w.kind.setCurrentText(kind)
        if kind == "Ring":
            w.ring_n.setValue(3)                                                              # 3 photos around, no tiny centre
        slots = dict(w._slots_now())
        a, c = w._slot_rect(slots[0]).center(), w._slot_rect(slots[2]).center()
        _mouse(app, w, "press", a)
        _mouse(app, w, "move", (a + c) / 2)
        _mouse(app, w, "move", c)
        _mouse(app, w, "release", c)
        assert names() == ["2.png", "1.png", "0.png"], kind


# ---- Slant, Fan, Brick, Timeline, the grouped layout menu, slider controls, Sort by Date ----

def test_layout_menu_is_grouped_headings_cannot_be_picked_and_every_layout_is_listed(app):
    from photosheet.ui.main_window import KIND_GROUPS, KINDS
    w = MainWindow()
    model = w.kind.model()
    rows = [(model.item(i).text(), bool(model.item(i).flags() & Qt.ItemFlag.ItemIsEnabled)) for i in range(model.rowCount())]
    headings = [t for t, ok in rows if not ok]
    assert headings == [g for g, _ in KIND_GROUPS]                                     # five headings, none selectable
    assert sorted(t for t, ok in rows if ok) == sorted(KINDS) and len(KINDS) == 29   # every layout is under exactly one heading
    assert w.kind.currentText() == "N-up" and bool(model.item(w.kind.currentIndex()).flags() & Qt.ItemFlag.ItemIsEnabled)
    # the order on screen is heading, its layouts, next heading...
    expect = [x for g, names in KIND_GROUPS for x in (g, *names)]
    assert [t for t, _ in rows] == expect
    assert w.kind.maxVisibleItems() >= len(rows)                                       # the whole menu fits without scrolling


def test_slider_and_number_box_stay_in_step_and_announce_their_names(app):
    from photosheet.ui.main_window import SliderSpin
    s = SliderSpin(-40, 40, 12, "°")
    seen = []
    s.valueChanged.connect(seen.append)
    assert (s.value(), s.slider.value(), s.spin.value()) == (12, 12, 12) and s.spin.suffix() == "°"
    s.slider.setValue(30)                                    # dragging the slider moves the number
    assert (s.value(), s.spin.value()) == (30, 30) and seen == [30]
    s.spin.setValue(-15)                                     # typing a number moves the slider
    assert s.slider.value() == -15 and seen == [30, -15]
    s.setValue(-15)                                          # no change: no signal
    assert seen == [30, -15]
    s.setValue(99)                                           # clamped to the range
    assert s.value() == 40 and s.slider.minimum() == -40 and s.slider.orientation() == Qt.Orientation.Horizontal
    w = MainWindow()
    for ctl, label in ((w.slant, "Lean"), (w.fan_spread, "Spread"), (w.sc_tilt, "Tilt (°)"), (w.hero_share, "Hero size (%)")):
        assert isinstance(ctl, SliderSpin) and ctl.spin.accessibleName() == label and ctl.slider.accessibleName() == f"{label} slider"


def test_slant_fan_brick_timeline_keep_photos_show_controls_and_roundtrip_presets(app, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Brick, Fan, Slant, Timeline
    w = _win_with_images(tmp_path, 7)
    w.show()
    w.kind.setCurrentText("Slant")
    assert isinstance(w.layout_, Slant) and len(w.layout_.images) == 7
    assert w.slant_panels.isVisible() and w.slant.isVisible() and w.gutter.isVisible() and not w.fan_n.isVisible()
    w.slant_panels.setValue(3)
    w.slant.setValue(-18)
    assert (w.layout_.panels, w.layout_.slant) == (3, -18) and w.layout_.pages == 3
    w.slant.setValue(40)                                                                # portrait page: the lean is limited
    assert w.layout_.slant_limited and w.per_sheet.isVisible() and w.per_sheet.text().startswith("limited to ")
    w.slant.setValue(10)
    assert w.per_sheet.text() == "" and not w.per_sheet.isVisible()                    # nothing to say: no stray "Per sheet" label
    w.kind.setCurrentText("Fixed size")
    assert w.per_sheet.isVisible() and "photos" in w.per_sheet.text()                  # layouts that always have a readout still show it
    w.kind.setCurrentText("Slant")
    w.kind.setCurrentText("Fan")
    assert isinstance(w.layout_, Fan) and w.fan_n.isVisible() and w.fan_spread.isVisible() and w.fan_border.isVisible()
    w.fan_n.setValue(4)
    w.fan_spread.setValue(90)
    w.fan_border.setValue(10)
    assert (w.layout_.photos, w.layout_.spread, w.layout_.border) == (4, 90, 0.1) and w.layout_.pages == 2
    w.kind.setCurrentText("Brick")
    assert isinstance(w.layout_, Brick) and w.rows.isVisible() and w.cols.isVisible() and not w.slant.isVisible()
    w.cols.setValue(4)
    w.rows.setValue(3)
    assert (w.layout_.columns, w.layout_.rows) == (4, 3)
    w.cols.setValue(1)                                                                  # a brick row needs two tiles: never crashes
    assert w.layout_.columns == 2
    w.kind.setCurrentText("Timeline")
    assert isinstance(w.layout_, Timeline) and w.tl_n.isVisible() and w.tl_dates.isVisible() and not w.fan_n.isVisible()
    w.tl_n.setValue(3)
    w.tl_dates.setChecked(False)
    assert (w.layout_.photos, w.layout_.dates) == (3, False) and w.layout_.pages == 3
    for setup, check in (
        (lambda: (w.kind.setCurrentText("Slant"), w.slant_panels.setValue(6), w.slant.setValue(-9)),
         lambda: (w.kind.currentText(), w.slant_panels.value(), w.slant.value()) == ("Slant", 6, -9)),
        (lambda: (w.kind.setCurrentText("Fan"), w.fan_n.setValue(7), w.fan_spread.setValue(45), w.fan_border.setValue(4)),
         lambda: (w.kind.currentText(), w.fan_n.value(), w.fan_spread.value(), w.fan_border.value()) == ("Fan", 7, 45, 4)),
        (lambda: (w.kind.setCurrentText("Brick"), w.cols.setValue(5), w.rows.setValue(2)),
         lambda: (w.kind.currentText(), w.cols.value(), w.rows.value()) == ("Brick", 5, 2)),
        (lambda: (w.kind.setCurrentText("Timeline"), w.tl_n.setValue(8), w.tl_dates.setChecked(True)),
         lambda: (w.kind.currentText(), w.tl_n.value(), w.tl_dates.isChecked()) == ("Timeline", 8, True)),
    ):
        setup()
        f = str(tmp_path / "p.json")
        preset.save(w.layout_, f)
        w.kind.setCurrentText("N-up")
        w.layout_ = preset.load(f)
        w._sync_controls()
        assert check()


def test_sort_photos_by_date_orders_by_taken_date_is_undoable_and_a_noop_when_sorted(app, tmp_path):
    from PIL import Image
    w = MainWindow()
    names = {"late": "2024:09:01 10:00:00", "early": "2023:01:05 10:00:00", "mid": "2023:11:20 10:00:00"}
    for n, when in names.items():
        ex = Image.Exif()
        ex[306] = when
        Image.new("RGB", (40, 30)).save(tmp_path / f"{n}.jpg", exif=ex)
    w._add_files([str(tmp_path / f"{n}.jpg") for n in ("late", "early", "mid")])
    order = lambda: [i.path.rsplit("/", 1)[-1][:-4] for i in w.layout_.images]
    assert order() == ["late", "early", "mid"]
    stack_before = w.undo_stack.count()
    w._sort_by_date()
    assert order() == ["early", "mid", "late"] and w.undo_act.text() == "Undo Sort by Date"
    assert [w.list.item(i).text() for i in range(3)] == ["early.jpg", "mid.jpg", "late.jpg"]   # the list follows
    w.undo_stack.undo()
    assert order() == ["late", "early", "mid"]
    w.undo_stack.redo()
    n = w.undo_stack.count()
    w._sort_by_date()                                                                          # already in order
    assert w.undo_stack.count() == n and order() == ["early", "mid", "late"]
    assert any(a.text() == "Sort Photos by Date" for a in w.menuBar().actions()[1].menu().actions())   # reachable from the Edit menu


def test_slant_clicks_follow_the_slanted_shape_not_the_bounding_box(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = _ready_window(app, tmp_path, 3)
    w.kind.setCurrentText("Slant")
    w.slant_panels.setValue(3)
    w.slant.setValue(20)
    cut = w._cut_polygons()
    assert sorted(cut) == [0, 1, 2]
    centre = lambda poly: QPointF(sum(poly[i].x() for i in range(4)) / 4, sum(poly[i].y() for i in range(4)) / 4)
    for i in range(3):
        assert w._slot_at(centre(cut[i]))[0] == i                                              # the middle of a panel picks that panel
    r0 = w._slot_rect(dict(w._slots_now())[0])
    corner = QPointF(r0.left() + 3, r0.top() + 3)                                              # inside panel 0's box, outside its shape
    assert not cut[0].containsPoint(corner, Qt.FillRule.OddEvenFill) and r0.contains(corner)
    assert w._slot_at(corner) is None
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    a, c = centre(cut[0]), centre(cut[2])
    shift = Qt.KeyboardModifier.ShiftModifier                                                  # plain drag repositions; Shift swaps
    _mouse(app, w, "press", a, shift)
    _mouse(app, w, "move", (a + c) / 2, shift)
    assert [type(s).__name__ for s, _ in w.preview.overlay][:1] == ["QPolygonF"]                # feedback is the slanted shape too
    _mouse(app, w, "move", c, shift)
    _mouse(app, w, "release", c, shift)
    assert names() == ["2.png", "1.png", "0.png"]
    item = w.layout_.images[1]
    before = (item.hpan, item.vpan)
    mid = centre(cut[1])
    _mouse(app, w, "press", mid)
    _mouse(app, w, "move", mid + QPointF(5, 8))
    _mouse(app, w, "move", mid + QPointF(12, 30))
    assert w._pan is not None and (item.hpan, item.vpan) != before                              # a plain drag moves the photo in its panel
    _mouse(app, w, "release", mid + QPointF(12, 30))
    assert w.undo_act.text() == "Undo Reposition Photo"


def test_page_drag_swaps_fan_brick_and_timeline_photos(app, tmp_path):
    w = _ready_window(app, tmp_path)
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    for kind in ("Brick", "Timeline", "Fan"):
        w.layout_.images[:] = sorted(w.layout_.images, key=lambda i: i.path)
        w.kind.setCurrentText(kind)
        slots = dict(w._slots_now())
        a, c = w._slot_rect(slots[0]).center(), w._slot_rect(slots[2]).center()
        if kind == "Fan":                                      # cards overlap: grab each one where it is not covered
            from PySide6.QtCore import QPointF
            ra, rc = w._slot_rect(slots[0]), w._slot_rect(slots[2])
            a, c = QPointF(ra.left() + 3, ra.center().y()), QPointF(rc.right() - 3, rc.center().y())
        _mouse(app, w, "press", a)
        _mouse(app, w, "move", (a + c) / 2)
        _mouse(app, w, "move", c)
        _mouse(app, w, "release", c)
        assert names() == ["2.png", "1.png", "0.png"], kind


def test_grid_defaults_each_layout_starts_with_its_own_until_the_user_chooses_one(app):
    from photosheet.core.layouts import Brick, Masonry, NUp, Polaroid, Shapes
    assert (NUp().rows, NUp().cols) == (3, 3) and (Polaroid().rows, Polaroid().cols) == (3, 3)
    assert (Shapes().rows, Shapes().cols) == (3, 2) and Masonry().columns == 3
    assert (Brick().rows, Brick().columns) == (5, 5)
    w = MainWindow()
    assert (w.rows.value(), w.cols.value()) == (3, 3) and (w.layout_.rows, w.layout_.cols) == (3, 3)
    w.kind.setCurrentText("Brick")
    assert (w.rows.value(), w.cols.value()) == (5, 5) and (w.layout_.rows, w.layout_.columns) == (5, 5)
    w.kind.setCurrentText("Shapes")
    assert (w.layout_.rows, w.layout_.cols) == (3, 2) and (w.rows.value(), w.cols.value()) == (3, 2)
    w.kind.setCurrentText("N-up")
    assert (w.layout_.rows, w.layout_.cols) == (3, 3)
    w.kind.setCurrentText("Carousel")                                              # a layout with no grid leaves the boxes alone
    w.kind.setCurrentText("Brick")
    assert (w.layout_.rows, w.layout_.columns) == (5, 5)
    w2 = MainWindow()
    w2.rows.setValue(4)                                                           # the user picks a grid: it is kept everywhere
    w2.kind.setCurrentText("Brick")
    assert (w2.rows.value(), w2.layout_.rows) == (4, 4) and w2.layout_.columns == 3
    w2.kind.setCurrentText("Polaroid")
    assert w2.layout_.rows == 4


def test_crop_all_images_to_fill_is_on_from_the_start_and_plain_drag_then_repositions(app, tmp_path):
    from PySide6.QtCore import QPointF
    w = MainWindow()
    assert w.crop.isChecked()
    for i in range(3):
        Image.new("RGB", (400, 100), (i * 80, 0, 0)).save(tmp_path / f"{i}.png")
    w._add_files([str(tmp_path / f"{i}.png") for i in range(3)])
    assert all(it.crop for it in w.layout_.images)                                 # photos added now are cropped to their frame
    w.kind.setCurrentText("Polaroid")
    assert all(it.crop for it in w.layout_.images) and w.crop.isChecked()          # and it stays on across layouts
    w.rows.setValue(1)
    w.cols.setValue(3)
    w.resize(1100, 800)
    w.show()
    import time
    t = time.time() + 5
    while w.preview.pixmap().isNull() and time.time() < t:
        app.processEvents()
    slots = dict(w._slots_now())
    a = w._slot_rect(slots[0]).center()
    names = lambda: [i.path[-5:] for i in w.layout_.images]
    _mouse(app, w, "press", a)
    _mouse(app, w, "move", a + QPointF(8, 0))
    _mouse(app, w, "move", a + QPointF(30, 0))
    assert w._pan is not None                                                      # crop on: a plain drag moves the photo in its frame
    _mouse(app, w, "release", a + QPointF(30, 0))
    assert names() == ["0.png", "1.png", "2.png"]
    c = w._slot_rect(slots[2]).center()
    shift = Qt.KeyboardModifier.ShiftModifier                                      # Shift swaps instead
    _mouse(app, w, "press", w._slot_rect(slots[0]).center(), shift)
    _mouse(app, w, "move", (a + c) / 2, shift)
    _mouse(app, w, "move", c, shift)
    _mouse(app, w, "release", c, shift)
    assert names() == ["2.png", "1.png", "0.png"]
    w.crop.setChecked(False)
    assert not any(it.crop for it in w.layout_.images)                             # unticking still turns it off for every photo



# ---- the Layout and Images panels fit short windows: pinned part + scrolling part ----

def _short_window(app, tmp_path, kind="Passport", n=3):
    w = _win_with_images(tmp_path, n)
    w.show()
    w.resize(1100, w.minimumSizeHint().height())          # as short as the window will go
    w.kind.setCurrentText(kind)
    for _ in range(10):
        app.processEvents()
    return w


def test_window_can_shrink_far_below_what_the_panels_used_to_need(app, tmp_path):
    w = _short_window(app, tmp_path)
    assert w.minimumSizeHint().height() <= 300                   # it was 416 before the panels scrolled (offscreen font)
    assert w.layout_dock.widget().minimumSizeHint().height() <= 260 and w.image_dock.widget().minimumSizeHint().height() <= 260
    assert w.height() == w.minimumSizeHint().height() or abs(w.height() - w.minimumSizeHint().height()) < 4


def test_layout_panel_keeps_page_settings_in_view_and_scrolls_the_options(app, tmp_path):
    from PySide6.QtCore import QPoint
    w = _short_window(app, tmp_path)
    pane, bar = w.layout_pane, w.layout_pane.area.verticalScrollBar()
    assert bar.maximum() > 0                                         # too short for every option: it scrolls
    dock = w.layout_dock.widget()
    for pinned in (w.kind, w.page_size, w.orient, w.margin):          # the page settings never scroll away
        top = pinned.mapTo(dock, QPoint(0, 0)).y()
        assert 0 <= top and top + pinned.height() <= dock.height()
    vp = pane.area.viewport()
    inside = lambda ctl: 0 <= ctl.mapTo(vp, QPoint(0, 0)).y() and ctl.mapTo(vp, QPoint(0, 0)).y() + ctl.height() <= vp.height()
    assert not inside(w.crop)                                         # the last option starts out of view...
    bar.setValue(bar.maximum())
    assert inside(w.crop)                                             # ...and scrolling reaches it
    assert w.kind.isVisible()                                         # the pinned controls are still there at the end
    for ctl in (w.gutter, w.cell_w, w.cell_h, w.copies):              # every option of the layout can be scrolled to
        bar.setValue(0)
        ctl.setFocus()
        app.processEvents()


def test_edge_lines_show_only_where_there_is_more_to_scroll_to(app, tmp_path):
    w = _short_window(app, tmp_path)
    pane, bar = w.layout_pane, w.layout_pane.area.verticalScrollBar()
    bar.setValue(0)
    assert not pane.top.isVisibleTo(w) and pane.bottom.isVisibleTo(w)             # at the top: more below
    bar.setValue(bar.maximum() // 2)
    assert pane.top.isVisibleTo(w) and pane.bottom.isVisibleTo(w)                 # in the middle: more both ways
    bar.setValue(bar.maximum())
    assert pane.top.isVisibleTo(w) and not pane.bottom.isVisibleTo(w)             # at the end: more above only
    assert pane.top.height() == 1 and pane.bottom.height() == 1
    w.resize(1100, 1000)                                                          # plenty of room: nothing to scroll, no lines
    w.kind.setCurrentText("Single")
    for _ in range(10):
        app.processEvents()
    assert bar.maximum() == 0 and not pane.top.isVisibleTo(w) and not pane.bottom.isVisibleTo(w)


def test_keyboard_focus_scrolls_a_hidden_control_into_view(app, tmp_path):
    from PySide6.QtCore import QPoint
    w = _short_window(app, tmp_path)
    w.activateWindow()
    bar = w.layout_pane.area.verticalScrollBar()
    bar.setValue(0)
    last = w.copies if w.copies.isVisibleTo(w) else w.cell_h
    last.setFocus(Qt.FocusReason.TabFocusReason)                                   # Tab reached a control below the fold
    for _ in range(5):
        app.processEvents()
    vp = w.layout_pane.area.viewport()
    y = last.mapTo(vp, QPoint(0, 0)).y()
    assert bar.value() > 0 and 0 <= y and y + last.height() <= vp.height()


def test_switching_layout_returns_the_options_to_the_top(app, tmp_path):
    w = _short_window(app, tmp_path)
    bar = w.layout_pane.area.verticalScrollBar()
    bar.setValue(bar.maximum())
    assert bar.value() > 0
    w.kind.setCurrentText("Calendar")
    for _ in range(5):
        app.processEvents()
    assert bar.value() == 0                                                       # a different layout starts at its first option
    w.cal_year.setValue(2031)                                                     # editing an option must not jump the panel
    assert bar.value() == 0


def test_pinned_and_scrolling_rows_line_up_on_both_edges_with_and_without_a_scroll_bar(app, tmp_path):
    from PySide6.QtCore import QPoint
    for height_free in (True, False):
        w = _win_with_images(tmp_path, 2)
        w.show()
        w.resize(1100, 1000 if height_free else w.minimumSizeHint().height())
        w.kind.setCurrentText("Passport")
        for _ in range(10):
            app.processEvents()
        bar = w.layout_pane.area.verticalScrollBar()
        assert (bar.maximum() == 0) == height_free
        left = lambda c: c.mapTo(w, QPoint(0, 0)).x()
        right = lambda c: left(c) + c.width()
        assert left(w.kind) == left(w.gutter) == left(w.cell_w)                    # one column of fields
        assert right(w.kind) == right(w.gutter) == right(w.cell_w)                 # and they end together, scroll bar or not


def test_images_panel_keeps_the_list_and_buttons_and_scrolls_the_effect_controls(app, tmp_path):
    from PySide6.QtCore import QPoint
    w = _short_window(app, tmp_path, kind="N-up", n=4)
    w.list.setCurrentRow(0)
    for _ in range(5):
        app.processEvents()
    assert w.list.height() >= 90                                                   # the list never collapses
    bar = w.fx_pane.area.verticalScrollBar()
    assert bar.maximum() > 0                                                       # too short for six effect controls: they scroll
    bar.setValue(bar.maximum())
    vp = w.fx_pane.area.viewport()
    y = w.flipv.mapTo(vp, QPoint(0, 0)).y()
    assert 0 <= y and y + w.flipv.height() <= vp.height()                          # the last control can be reached
    dock = w.image_dock.widget()
    for ctl in (w.list,):
        assert ctl.mapTo(dock, QPoint(0, 0)).y() >= 0
    w.resize(1100, 1000)
    for _ in range(10):
        app.processEvents()
    assert bar.maximum() == 0 and not w.fx_pane.bottom.isVisibleTo(w)             # in a tall window everything shows, no bar


def test_panel_labels_keep_a_margin_from_the_panel_edge(app, tmp_path):
    from PySide6.QtCore import QPoint
    w = _win_with_images(tmp_path, 2)
    w.show()
    for _ in range(6):
        app.processEvents()
    dock = w.layout_dock.widget()
    pinned_label = w._pinned_form.labelForField(w.kind)
    option_label = w.layout_pane.content.layout().labelForField(w.gutter)
    for lab in (pinned_label, option_label):
        assert lab.mapTo(dock, QPoint(0, 0)).x() >= 6, lab.text()                     # not flush against the panel's edge
    assert pinned_label.mapTo(dock, QPoint(0, 0)).x() == option_label.mapTo(dock, QPoint(0, 0)).x()   # and both sections line up
    assert w.kind.mapTo(dock, QPoint(0, 0)).x() == w.gutter.mapTo(dock, QPoint(0, 0)).x()


def test_brick_starts_on_landscape_paper_other_layouts_on_portrait_and_a_choice_sticks(app, tmp_path):
    from photosheet.core import preset
    land = lambda w: w.layout_.page.width > w.layout_.page.height
    w = MainWindow()
    assert w.orient.currentText() == "Portrait" and not land(w)
    w.kind.setCurrentText("Brick")
    assert w.orient.currentText() == "Landscape" and land(w)                      # Brick's own start
    w.kind.setCurrentText("N-up")
    assert w.orient.currentText() == "Portrait" and not land(w)                   # and it goes back for everything else
    w.kind.setCurrentText("Brick")
    w.kind.setCurrentText("Timeline")
    assert not land(w)
    # the user picks an orientation: it is kept, whichever layout comes next
    w2 = MainWindow()
    w2.orient.setCurrentText("Landscape")
    w2.kind.setCurrentText("N-up")
    w2.kind.setCurrentText("Poster")
    assert land(w2)
    w3 = MainWindow()
    w3.kind.setCurrentText("Brick")
    assert land(w3)
    w3.orient.setCurrentText("Portrait")                                           # the user turns Brick to portrait on purpose
    assert not land(w3) and w3.layout_.page.height == 842
    w3.kind.setCurrentText("N-up")
    w3.kind.setCurrentText("Brick")
    assert not land(w3)                                                            # not forced back to landscape
    # a saved preset keeps its own orientation: loading does not apply any default
    f = str(tmp_path / "p.json")
    w4 = MainWindow()
    w4.kind.setCurrentText("Brick")
    w4.orient.setCurrentText("Portrait")
    preset.save(w4.layout_, f)
    w5 = MainWindow()
    w5.layout_ = preset.load(f)
    w5._sync_controls()
    assert w5.kind.currentText() == "Brick" and w5.orient.currentText() == "Portrait" and not land(w5)
    w5.cols.setValue(4)                                                            # editing afterwards keeps it
    assert not land(w5) and w5.layout_.columns == 4
    for kind in ("N-up", "Shapes", "Calendar", "Passport", "Collage"):             # no other layout starts sideways
        w6 = MainWindow()
        w6.kind.setCurrentText(kind)
        assert w6.orient.currentText() == "Portrait", kind


def test_collage_rotate_handle_turns_the_box_and_undoes(app, tmp_path):
    w = _collage_window(app, tmp_path)
    w.list.setCurrentRow(0)
    w._refresh_overlay()
    slot = dict(w._slots_now())[0]
    it = w.layout_.images[0]
    top, knob = w._rot_handle(it, slot)
    assert w._on_rot_handle(knob, it, slot)
    c = w._slot_rect(slot).center()
    end = c + QPointF(100, 0)                                    # straight right of the centre = 90 degrees
    _mouse(app, w, "press", knob)
    _mouse(app, w, "move", (knob + end) / 2)
    _mouse(app, w, "move", end)
    _mouse(app, w, "release", end)
    assert it.angle == 90
    w.undo_stack.undo()
    assert w.layout_.images[0].angle == 0
