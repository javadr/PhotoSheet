"""User preferences in settings.toml, in the OS's per-user config folder (see path())."""
from __future__ import annotations

import copy
import datetime
import os
import sys
import tomllib
from pathlib import Path

import tomli_w

# Every known setting and its default. A value in the file is used only when it has the same type as here.
DEFAULTS = {
    "page": {"size": "A4", "orientation": "auto", "margin": 18},   # orientation: auto | Portrait | Landscape; margin in pt
    "layout": {"kind": "N-up", "caption": "None", "cut_marks": False, "crop_to_fill": True},
    "view": {"font_pt": 0.0, "rulers": True, "ruler_units": "mm"},   # font_pt 0: system size
    "export": {"pdf_quality": "standard",   # print quality | standard | small file
               "file_name": "photosheet-{date}-{n:02}"},   # see NAME_FIELDS
    "paths": {"images_dir": "", "presets_dir": "", "export_dir": ""},
    # Position of the window's top-left corner (can be negative on a monitor left of or above the main one) and its
    # size, in desktop pixels. width 0: nothing saved yet, so the default size wherever the desktop puts it.
    "window": {"x": 0, "y": 0, "width": 0, "height": 0, "maximized": False},
}


# Examples, for help text. {n} and {photos} are numbers, so {n:03} pads to 001.
NAME_FIELDS = {"date": "20261113", "time": "174502", "layout": "N-up", "page": "A4", "photos": "12",
               "n": "1, 2, 3… first number not yet used", "n:03": "001, 002…"}
_BAD_CHARS = str.maketrans({c: "-" for c in '<>:"/\\|?*'})   # not allowed in Windows file names


def file_name(pattern: str, layout: str = "", page: str = "", photos: int = 0, n: int = 1, now=None) -> str:
    """The file name (no extension) for a naming pattern such as "photosheet-{date}-{n:02}".
    A pattern that can't be filled (unknown field, stray brace) or comes out empty falls back to the default."""
    now = now or datetime.datetime.now()
    fields = {"date": now.strftime("%Y%m%d"), "time": now.strftime("%H%M%S"),   # no colons: Windows forbids them
              "layout": layout, "page": page, "photos": photos, "n": n}
    try:
        name = pattern.format_map(fields)
    except (KeyError, ValueError, IndexError, AttributeError):
        name = ""
    name = name.translate(_BAD_CHARS).strip(" .")   # Windows also rejects trailing dots and spaces
    return name or DEFAULTS["export"]["file_name"].format_map(fields)


def free_path(pattern: str, folder: str, ext: str, now=None, **fields) -> str:
    """Folder + name + ext, with {n} the lowest number from 1 whose file does not exist yet."""
    now = now or datetime.datetime.now()   # one moment for every try, so only {n} changes between them
    for n in range(1, 100_000):
        name = file_name(pattern, n=n, now=now, **fields)
        p = os.path.join(folder, name + ext)
        if not os.path.exists(p) or file_name(pattern, n=n + 1, now=now, **fields) == name:   # free, or no {n} to bump
            return p
    return p


def path() -> Path:
    """Linux ~/.config/PhotoSheet, macOS ~/Library/Preferences/PhotoSheet, Windows %LOCALAPPDATA%\\PhotoSheet."""
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
    return Path(base) / "PhotoSheet" / "settings.toml"


def _same_type(v, default) -> bool:
    if isinstance(default, bool) or isinstance(v, bool):
        return type(v) is type(default)
    return isinstance(v, type(default)) or (isinstance(default, float) and isinstance(v, int))


def load() -> dict:
    """The saved settings over the defaults. A missing or broken file, or a bad value, falls back to the default."""
    out = copy.deepcopy(DEFAULTS)
    try:
        with open(path(), "rb") as f:
            saved = tomllib.load(f)
    except FileNotFoundError:
        return out
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"PhotoSheet: ignoring {path()}: {e}", file=sys.stderr)
        return out
    for sec, keys in out.items():
        got = saved.get(sec)
        if isinstance(got, dict):
            for k, default in keys.items():
                if k in got and _same_type(got[k], default):
                    keys[k] = type(default)(got[k])
    return out


def save(prefs: dict) -> None:
    """Write atomically: a crash mid-save leaves the old file, never a half-written one."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(tomli_w.dumps({"version": 1, **prefs}).encode())
    os.replace(tmp, p)
