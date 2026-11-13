"""`photosheet --self-test [REPORT]`: exercise the installed or bundled app end to end and exit 0 only if it all works.

Release builds run this on every operating system. It covers what a PyInstaller bundle usually gets wrong: missing
data files, the colour-management library, fonts, and Qt itself. It needs no display (Qt's offscreen platform)
and writes its report to a file as well, because a windowed Windows program has no console.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


def run(report: str | None = None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no display needed
    lines: list[str] = []
    state = {"ok": True}

    def check(name, fn):
        try:
            detail = fn()
            lines.append(f"ok    {name}" + (f" ({detail})" if detail else ""))
        except Exception as e:   # report every failure, not just the first
            state["ok"] = False
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")

    tmp = Path(tempfile.mkdtemp(prefix="photosheet-selftest-"))
    from . import settings
    settings.path = lambda: tmp / "settings.toml"   # the test window must not read or overwrite the user's settings
    photos: list[str] = []

    def make_photos():
        import io

        from PIL import Image, ImageCms
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        (tmp / "srgb.icc").write_bytes(icc)
        for i, colour in enumerate(((220, 70, 70), (70, 160, 90), (70, 110, 220))):
            p = tmp / f"photo{i}.png"
            Image.new("RGB", (240 + 40 * i, 180), colour).save(p, icc_profile=icc)   # a profile embedded in the file
            photos.append(str(p))
        return f"{len(photos)} images"

    def version():
        import photosheet
        return photosheet.__version__

    def data_files():
        import photosheet
        icons = Path(photosheet.__file__).parent / "icons"
        missing = [n for n in ("photosheet.png", "photosheet.ico", "photosheet.icns", "photosheet.svg") if not (icons / n).is_file()]
        if missing:
            raise FileNotFoundError(f"missing from {icons}: {', '.join(missing)}")

    def fonts():
        from PIL import ImageFont

        from .core.render import caption_font
        font = caption_font(24)
        if not isinstance(font, ImageFont.FreeTypeFont) or getattr(font, "path", None) is None:
            raise RuntimeError("no TrueType font found; accented captions would show empty boxes")
        if bytes(font.getmask("ä")) == bytes(font.getmask("͸")):
            raise RuntimeError(f"{font.path} cannot draw accented letters")
        return Path(font.path).name

    def colour_management():
        from .core.layouts import ImageItem
        from .core.render import load
        a = load(ImageItem(photos[0]))                                  # the profile embedded in the file
        b = load(ImageItem(photos[0], profile=str(tmp / "srgb.icc")))   # a profile assigned from disk
        c = load(ImageItem(photos[0], effects=[("Gamma", {"gamma": 1.4}), ("Temperature", {"change": 500}),
                                               ("UnsharpMask", {}), ("Desaturate", {})]))
        if not (a.size == b.size == c.size and a.mode == b.mode == "RGB"):
            raise RuntimeError("unexpected image sizes or modes")

    def layouts():
        from .core import preset
        from .core.geometry import PageExtent
        from .core.layouts import ImageItem
        from .core.render import render_page
        for key, cls in preset.TYPES.items():
            for w, h in ((595, 842), (842, 595)):
                page = PageExtent(w, h)
                page.set_margins(18, 18, 18, 18)
                lay = cls(page=page)
                for p in photos * 2:
                    lay.add(ImageItem(p, crop=True))
                im = render_page(lay, 0, dpi=40)
                if im.size != (round(w * 40 / 72), round(h * 40 / 72)):
                    raise RuntimeError(f"{key}: wrong page size {im.size}")
                if getattr(lay, "per_page", 1) and im.convert("L").getextrema()[0] > 250:   # an insert wider than the page prints nothing
                    raise RuntimeError(f"{key}: the page came out blank")
            path = tmp / f"{key}.json"
            preset.save(lay, str(path))
            if preset.load(str(path)) != lay:
                raise RuntimeError(f"{key}: preset did not round-trip")
        return f"{len(preset.TYPES)} layouts"

    def export_pdf():
        from .core.geometry import PageExtent
        from .core.layouts import ImageItem, NUp
        from .core.render import render_all
        lay = NUp(page=PageExtent(595, 842), rows=1, cols=1)
        for p in photos:
            lay.add(ImageItem(p))
        pages = render_all(lay, dpi=50)
        out = tmp / "out.pdf"
        pages[0].save(out, save_all=True, append_images=pages[1:], resolution=50)
        if not out.read_bytes().startswith(b"%PDF") or len(pages) != 3:
            raise RuntimeError("PDF export failed")

    def window():
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from .ui.main_window import KINDS, MainWindow
        app = QApplication.instance() or QApplication([])
        import photosheet
        if QIcon(str(Path(photosheet.__file__).parent / "icons" / "photosheet.png")).isNull():
            raise RuntimeError("the window icon did not load")
        w = MainWindow()
        w._add_files(photos)

        def pump(seconds, until=lambda: False):
            end = time.time() + seconds
            while time.time() < end and not until():
                app.processEvents()

        pump(20, lambda: not w.preview.pixmap().isNull())
        if w.preview.pixmap().isNull():
            raise RuntimeError("the preview never rendered")
        for name, cls in KINDS.items():          # every layout is selectable and keeps the photos
            w.kind.setCurrentText(name)
            if type(w.layout_) is not cls or len(w.layout_.images) != len(photos):
                raise RuntimeError(f"switching to {name!r} lost photos or picked the wrong layout")
            pump(0.05)
        pump(5)
        w.close()
        app.processEvents()
        return f"{len(KINDS)} layouts selectable"

    for name, fn in (("version", version), ("data files", data_files), ("fonts", fonts), ("sample photos", make_photos),
                     ("colour management and effects", colour_management), ("every layout renders", layouts),
                     ("PDF export", export_pdf), ("window and preview", window)):
        if name != "sample photos" and not photos and name not in ("version", "data files", "fonts"):
            lines.append(f"skip  {name} (no sample photos)")
            continue
        check(name, fn)

    shutil.rmtree(tmp, ignore_errors=True)
    text = "\n".join(lines) + f"\n{'SELF-TEST PASSED' if state['ok'] else 'SELF-TEST FAILED'}\n"
    if report:
        Path(report).write_text(text, encoding="utf-8")
    if sys.stdout is not None:   # a windowed Windows build has none
        print(text, end="")
    return 0 if state["ok"] else 1
