"""Save/load a layout (settings + image list) as JSON. Fresh format; the old .preset files are not read."""
from __future__ import annotations

import json
from dataclasses import asdict, fields

from .geometry import PageExtent
from .layouts import Carousel, ImageItem, Layout, NUp, Poster, Single

TYPES = {"nup": NUp, "single": Single, "poster": Poster, "carousel": Carousel}


def kind(layout: Layout) -> str:
    return next(k for k, c in TYPES.items() if type(layout) is c)


def to_dict(layout: Layout) -> dict:
    d = asdict(layout)
    d["type"] = kind(layout)
    return d


def from_dict(d: dict) -> Layout:
    d = dict(d)
    try:
        cls = TYPES[d.pop("type")]
    except KeyError as e:
        raise ValueError(f"unknown or missing layout type: {e}") from None
    known = {f.name for f in fields(cls)}
    kw = {k: v for k, v in d.items() if k in known and k not in ("page", "images")}
    page = PageExtent(**d.get("page", {}))
    lay = cls(page=page, **kw)
    lay.images = [ImageItem(**i) for i in d.get("images", [])]
    for i in lay.images:  # JSON turns effect tuples into lists
        i.effects = [(n, p) for n, p in i.effects]
    return lay


def save(layout: Layout, path: str) -> None:
    with open(path, "w") as f:
        json.dump(to_dict(layout), f, indent=2)


def load(path: str) -> Layout:
    with open(path) as f:
        return from_dict(json.load(f))
