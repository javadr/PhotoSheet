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
