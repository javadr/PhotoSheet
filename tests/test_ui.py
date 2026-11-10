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
