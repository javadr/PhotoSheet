"""Save/load a layout (settings + image list) as JSON. Fresh format; the old .preset files are not read."""
from __future__ import annotations

import json
from dataclasses import asdict, fields

from .geometry import PageExtent
from .layouts import (Badges, Bento, Calendar, Carousel, Collage, Feature, Filmstrip, FixedSize, FramedPrint, Honeycomb,
                      Brick, Checker, Diamond, Pyramid, Fan, ImageItem, Layout, Masonry, Mosaic, NUp, Package, Passport, Polaroid, Poster, Ring,
                      Scatter, Shapes, Single, Slant, Spiral, Stairs, Strip, Timeline, Triptych)

TYPES = {"nup": NUp, "single": Single, "poster": Poster, "carousel": Carousel,
         "fixed": FixedSize, "mosaic": Mosaic, "strip": Strip, "passport": Passport,
         "polaroid": Polaroid, "triptych": Triptych, "package": Package, "collage": Collage,
         "framed": FramedPrint, "badges": Badges, "feature": Feature, "filmstrip": Filmstrip,
         "bento": Bento, "spiral": Spiral, "honeycomb": Honeycomb, "calendar": Calendar,
         "masonry": Masonry, "shapes": Shapes, "ring": Ring, "scatter": Scatter,
         "slant": Slant, "fan": Fan, "timeline": Timeline, "brick": Brick, "checker": Checker, "stairs": Stairs,
         "pyramid": Pyramid, "diamond": Diamond}


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
    for i in lay.images:   # JSON turns tuples into lists
        i.box = tuple(i.box) if i.box else None
    if isinstance(lay, Package):
        lay.prints = [tuple(x) for x in lay.prints]
    for i in lay.images:  # JSON turns effect tuples into lists
        i.effects = [(n, p) for n, p in i.effects]
    return lay


def save(layout: Layout, path: str) -> None:
    with open(path, "w") as f:
        json.dump(to_dict(layout), f, indent=2)


def load(path: str) -> Layout:
    with open(path) as f:
        return from_dict(json.load(f))
