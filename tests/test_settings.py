import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from photosheet import settings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_missing_file_gives_defaults_and_round_trip():
    assert settings.load() == settings.DEFAULTS
    p = settings.load()
    p["page"]["size"], p["view"]["font_pt"], p["layout"]["cut_marks"] = "Letter", 12.5, True
    settings.save(p)
    assert settings.load() == p
    assert not settings.path().with_suffix(".tmp").exists()


def test_broken_file_and_bad_values_fall_back_to_defaults():
    settings.path().parent.mkdir(parents=True)
    settings.path().write_text("this is = = not toml")
    assert settings.load() == settings.DEFAULTS
    settings.path().write_text('version = 9\nunknown = 1\n[page]\nmargin = "wide"\nsize = "A5"\n'
                               '[view]\nfont_pt = 11\nrulers = 1\n[layout]\nkind = true\n')
    p = settings.load()
    assert p["page"]["margin"] == 18 and p["page"]["size"] == "A5"           # wrong type skipped, good one kept
    assert p["view"]["font_pt"] == 11.0 and isinstance(p["view"]["font_pt"], float)   # int is fine for a float
    assert p["view"]["rulers"] is True and p["layout"]["kind"] == "N-up"      # 1 is not a bool, true is not a str
    assert "unknown" not in p


def test_window_saves_on_close_and_next_window_restores(app):
    from photosheet.ui.main_window import MainWindow
    w = MainWindow()
    w.kind.setCurrentText("Shapes")   # "Shapes" is also a group heading in the menu: must pick the layout
    w.page_size.setCurrentIndex(w.page_size.findData("Letter"))
    w.orient.setCurrentText("Landscape")
    w.margin.setValue(30)
    w.marks.setChecked(True)
    w.inches_act.setChecked(True)
    w.rulers_act.setChecked(False)
    w.prefs["paths"]["images_dir"] = "/photos"
    w.close()
    saved = settings.load()
    assert saved["layout"]["kind"] == "Shapes" and saved["page"] == {"size": "Letter", "orientation": "Landscape", "margin": 30}
    assert saved["paths"]["images_dir"] == "/photos"
    w2 = MainWindow()
    assert w2.kind.currentText() == "Shapes" and w2.layout_.__class__.__name__ == "Shapes"
    assert w2.page_size.currentData() == "Letter" and w2.orient.currentText() == "Landscape" and w2.margin.value() == 30
    assert w2.marks.isChecked() and w2.inches_act.isChecked() and not w2.rulers_act.isChecked()
    assert w2.hruler.inches and w2.hruler.isHidden()


def test_untouched_orientation_stays_automatic(app):
    from photosheet.ui.main_window import MainWindow
    w = MainWindow()
    w.close()
    assert settings.load()["page"]["orientation"] == "auto"
    w2 = MainWindow()
    w2.kind.setCurrentText("Brick")   # Brick starts on landscape unless the user picked an orientation
    assert w2.orient.currentText() == "Landscape"


def test_preferences_restore_defaults_then_ok_applies_and_saves(app, monkeypatch):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox
    from photosheet.ui.main_window import MainWindow
    w = MainWindow()
    w.margin.setValue(50)
    w.inches_act.setChecked(True)

    def exec_(dlg):
        dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.RestoreDefaults).click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", exec_)
    w.preferences()
    assert w.margin.value() == 18 and not w.inches_act.isChecked()
    assert settings.load()["page"]["margin"] == 18


def test_file_name_pattern():
    import datetime
    now = datetime.datetime(2026, 11, 13, 9, 5, 7)
    assert settings.file_name(settings.DEFAULTS["export"]["file_name"], now=now) == "photosheet-2026-11-13-01"
    assert settings.file_name("{layout} {page} x{photos}", "N-up", "A4", 12, now=now) == "N-up A4 x12"
    assert settings.file_name("trip/{date}: day?", now=now) == "trip-2026-11-13- day-"   # no Windows-illegal chars
    for bad in ("{nope}", "{date", "{0}", "{date.year}", "{date:%Y}", "", " . "):   # unusable: the default name
        assert settings.file_name(bad, now=now) == "photosheet-2026-11-13-01"


def test_export_suggests_name_from_pattern(app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    from photosheet.ui.main_window import MainWindow
    w = MainWindow()
    w.prefs["export"]["file_name"] = "{layout}-{page}"
    w.prefs["paths"]["export_dir"] = str(tmp_path)
    seen = {}

    def dialog(parent, title, path, *rest):
        seen["path"] = path
        return "", ""   # cancelled
    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog)
    w.export()
    assert seen["path"] == str(tmp_path / "N-up-A4.pdf")


def test_n_counts_past_files_that_exist(tmp_path):
    assert settings.free_path("photosheet-{n:03}", str(tmp_path), ".pdf") == str(tmp_path / "photosheet-001.pdf")
    for n in (1, 2, 4):
        (tmp_path / f"photosheet-{n:03}.pdf").touch()
    assert settings.free_path("photosheet-{n:03}", str(tmp_path), ".pdf") == str(tmp_path / "photosheet-003.pdf")   # fills the gap
    assert settings.free_path("photosheet-{n}", str(tmp_path), ".pdf") == str(tmp_path / "photosheet-1.pdf")       # different name
    (tmp_path / "fixed.pdf").touch()
    assert settings.free_path("fixed", str(tmp_path), ".pdf") == str(tmp_path / "fixed.pdf")   # no {n}: no endless loop
    assert settings.file_name("x{photos:02}", photos=5) == "x05"


def test_window_geometry_round_trip_and_bad_value(app):
    from photosheet.ui.main_window import MainWindow
    w = MainWindow()
    w.setGeometry(40, 30, 700, 500)   # inside the 800 × 600 offscreen screen: Qt shrinks a window to fit
    w.close()
    saved = settings.load()["window"]
    assert (saved["width"], saved["height"], saved["maximized"]) == (700, 500, False)
    w2 = MainWindow()
    assert (w2.x(), w2.y(), w2.width(), w2.height()) == (saved["x"], saved["y"], 700, 500)


def test_window_with_nothing_saved_gets_default_size(app):
    from photosheet.ui.main_window import MainWindow
    w = MainWindow()
    assert (w.width(), w.height()) == (1200, 800)


def test_on_screen_handles_several_monitors():
    from PySide6.QtCore import QRect
    from photosheet.ui.main_window import on_screen
    left, main = QRect(-1920, 0, 1920, 1080), QRect(0, 0, 2560, 1440)   # a monitor to the left of the main one
    screens = [main, left]
    r = QRect(-1500, 100, 1000, 700)
    assert on_screen(r, screens, main) == r                                       # on the left monitor: untouched
    assert on_screen(QRect(-1500, 100, 1000, 700), [main], main) == QRect(0, 100, 1000, 700)   # unplugged: main
    assert on_screen(QRect(2000, 1200, 1000, 700), screens, main) == QRect(1560, 740, 1000, 700)   # hanging off: pulled in
    assert on_screen(QRect(-1900, 0, 3000, 2000), screens, main) == QRect(-1920, 0, 1920, 1080)    # too big: shrunk
    assert on_screen(QRect(5000, 5000, 800, 600), screens, main) == QRect(1760, 840, 800, 600)     # nowhere: main
