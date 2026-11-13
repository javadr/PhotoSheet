import os
import sys
from pathlib import Path


def main() -> int:
    if any(a in ("-V", "--version") for a in sys.argv[1:]):   # answer without loading Qt or needing a display
        from .banner import print_banner
        print_banner()
        return 0
    if "--self-test" in sys.argv[1:]:   # used by the release builds: `photosheet --self-test [REPORT_FILE]`
        from .selftest import run
        rest = [a for a in sys.argv[1:] if a != "--self-test"]
        return run(rest[0] if rest else None)
    # Wayland doesn't let an app place its own window, so the saved position would be ignored. Prefer XWayland,
    # where it works; Qt tries wayland next if XWayland is missing. QT_QPA_PLATFORM=wayland opts out.
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb;wayland")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow, restore_text_size
    app = QApplication(sys.argv)
    app.setApplicationName("PhotoSheet")
    app.setApplicationDisplayName("PhotoSheet")
    # Must match the .desktop file name: Wayland/GNOME/KDE use it to pick the Alt+Tab icon and name.
    app.setDesktopFileName("photosheet")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "icons" / "photosheet.png")))
    restore_text_size()
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
