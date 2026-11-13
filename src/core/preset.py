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
MAX_PT = 14400        # 200 in, the largest page PDF allows
MAX_POSTER = 100 * MAX_PT   # ~500 m: far past any real poster, still a bounded page count


def kind(layout: Layout) -> str:
    return next(k for k, c in TYPES.items() if type(layout) is c)


def to_dict(layout: Layout) -> dict:
    d = asdict(layout)
    d["type"] = kind(layout)
    return d


def _known(cls, d: dict) -> dict:
    """Only the keys this version knows, so a file from a newer version still opens."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def from_dict(d: dict) -> Layout:
    d = dict(d)
    try:
        cls = TYPES[d.pop("type")]
    except KeyError as e:
        raise ValueError(f"unknown or missing layout type: {e}") from None
    kw = {k: v for k, v in _known(cls, d).items() if k not in ("page", "images")}
    page = PageExtent(**_known(PageExtent, d.get("page", {})))
    # A preset may come from anyone: a huge page or poster would make rendering allocate without bound (MemoryError / OOM).
    if not (0 < page.width <= MAX_PT and 0 < page.height <= MAX_PT):
        raise ValueError(f"page size must be between 0 and {MAX_PT} pt")
    lay = cls(page=page, **kw)
    if isinstance(lay, Poster) and not (0 < lay.poster_w <= MAX_POSTER and 0 < lay.poster_h <= MAX_POSTER):
        raise ValueError(f"poster size must be between 0 and {MAX_POSTER} pt")
    lay.images = [ImageItem(**_known(ImageItem, i)) for i in d.get("images", [])]
    for i in lay.images:   # JSON turns tuples into lists
        i.box = tuple(i.box) if i.box else None
    if isinstance(lay, Package):
        lay.prints = [tuple(x) for x in lay.prints]
    for i in lay.images:  # JSON turns effect tuples into lists
        i.effects = [(n, p) for n, p in i.effects]
    return lay


def save(layout: Layout, path: str) -> None:
    text = json.dumps(to_dict(layout), indent=2)   # built first: an error here must not empty the existing file
    with open(path, "w") as f:
        f.write(text)


def load(path: str) -> Layout:
    with open(path) as f:
        return from_dict(json.load(f))
