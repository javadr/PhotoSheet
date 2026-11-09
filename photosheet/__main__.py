import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow, restore_text_size


def main() -> int:
    app = QApplication(sys.argv)
    restore_text_size()
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
