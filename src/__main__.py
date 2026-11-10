import sys
from pathlib import Path


def main() -> int:
    if any(a in ("-V", "--version") for a in sys.argv[1:]):   # answer without loading Qt or needing a display
        from .banner import print_banner
        print_banner()
        return 0
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
    w.resize(1200, 800)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
