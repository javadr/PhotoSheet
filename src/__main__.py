import os
import sys
from pathlib import Path


def _register_windows() -> None:
    """Per-user .phs association for the portable exe, so a double-click opens it. Re-run when the exe moves."""
    import winreg
    cmd = f'"{sys.executable}" "%1"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\PhotoSheet.Preset\shell\open\command") as k:
            if winreg.QueryValue(k, "") == cmd:
                return
    except OSError:
        pass
    for key, val in ((r".phs", "PhotoSheet.Preset"), (r"PhotoSheet.Preset", "PhotoSheet preset"),
                     (r"PhotoSheet.Preset\DefaultIcon", f"{sys.executable},0"),
                     (r"PhotoSheet.Preset\shell\open\command", cmd)):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{key}") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, val)
    import ctypes
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED: refresh Explorer


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
    from PySide6.QtCore import QEvent

    class App(QApplication):
        def event(self, e):   # macOS hands a double-clicked file over as an event, not in argv
            if e.type() == QEvent.Type.FileOpen and getattr(self, "win", None):
                self.win.load_preset(e.file())
                return True
            return super().event(e)

    app = App(sys.argv)
    app.setApplicationName("PhotoSheet")
    app.setApplicationDisplayName("PhotoSheet")
    # Must match the .desktop file name: Wayland/GNOME/KDE use it to pick the Alt+Tab icon and name.
    app.setDesktopFileName("photosheet")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "icons" / "photosheet.png")))
    restore_text_size()
    w = MainWindow()
    app.win = w
    w.show()
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            _register_windows()
        except OSError:
            pass   # association is a convenience; never block startup
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):   # a double-clicked .phs preset arrives as the path
        w.load_preset(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
