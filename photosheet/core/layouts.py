"""Layout geometry: which slot each image gets, and how many pages. No rendering, no Qt."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from .geometry import PageExtent, Rect, ceil_div


@dataclass
class ImageItem:
    path: str
    crop: bool = False
    rotation: int = -1          # Rotation enum value
    flip_h: bool = False
    flip_v: bool = False
    hpan: float = 0.5
    vpan: float = 0.5
    scale: float = 100.0        # Single layout only: percent of native size
    profile: str | None = None  # ICC file assigned to this image (else embedded, else sRGB)
    intent: int = 0             # ImageCms.Intent value
    mask: str | None = None     # border mask image; black areas hide the photo
    effects: list = field(default_factory=list)  # [(name, {params}), ...] in order


@dataclass
class Layout:
    page: PageExtent = field(default_factory=PageExtent)
    images: list[ImageItem] = field(default_factory=list)

    def add(self, item: ImageItem) -> None:
        self.images.append(item)

    @property
    def pages(self) -> int:
        return max(1, len(self.images))

    def slots(self, page: int) -> list[tuple[ImageItem, Rect]]:
        raise NotImplementedError


@dataclass
class NUp(Layout):
    rows: int = 2
    cols: int = 2
    hgutter: float = 8.0
    vgutter: float = 8.0

    def __post_init__(self):
        if self.rows < 1 or self.cols < 1:
            raise ValueError("rows and cols must be >= 1")

    @property
    def per_page(self) -> int:
        return self.rows * self.cols

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def cell(self, row: int, col: int) -> Rect:
        p = self.page
        cw = (p.imageable_w - (self.cols - 1) * self.hgutter) / self.cols
        ch = (p.imageable_h - (self.rows - 1) * self.vgutter) / self.rows
        return Rect(p.left + col * (cw + self.hgutter), p.top + row * (ch + self.vgutter), cw, ch)

    def slots(self, page: int):
        start = page * self.per_page
        return [(img, self.cell(*divmod(i, self.cols)))
                for i, img in enumerate(self.images[start:start + self.per_page])]


@dataclass
class Single(Layout):
    """One image per page, centred in the imageable area at item.scale percent."""

    def fit_scale(self, src_w: float, src_h: float) -> float:
        """Largest scale (percent, max 100) at which the image fits the imageable area."""
        iw, ih = self.page.imageable_w, self.page.imageable_h
        return min(100.0, 100 * iw / src_w, 100 * ih / src_h)

    def slots(self, page: int):
        img = self.images[page]
        p = self.page
        return [(img, Rect(p.left, p.top, p.imageable_w, p.imageable_h))]


@dataclass
class Poster(Layout):
    """One image tiled over htiles x vtiles pages with overlap. Sizes in points."""
    poster_w: float = 1000.0
    poster_h: float = 1000.0
    hoverlap: float = 8.0
    voverlap: float = 8.0

    @property
    def htiles(self) -> int:
        iw = self.page.imageable_w
        return ceil_div(self.poster_w - self.hoverlap, iw - self.hoverlap)

    @property
    def vtiles(self) -> int:
        ih = self.page.imageable_h
        return ceil_div(self.poster_h - self.voverlap, ih - self.voverlap)

    @property
    def pages(self) -> int:
        return max(1, len(self.images)) * self.htiles * self.vtiles

    def tile_window(self, tile: int) -> tuple[int, int, Rect]:
        """(image index, tile index, window into the poster, in poster coordinates)."""
        n = self.htiles * self.vtiles
        idx, t = divmod(tile, n)
        vt, ht = divmod(t, self.htiles)
        iw, ih = self.page.imageable_w, self.page.imageable_h
        x = ht * (iw - self.hoverlap)
        y = vt * (ih - self.voverlap)
        return idx, t, Rect(x, y, min(iw, self.poster_w - x), min(ih, self.poster_h - y))

    def slots(self, page: int):
        idx, _, _ = self.tile_window(page)
        p = self.page
        return [(self.images[idx], Rect(p.left, p.top, self.poster_w, self.poster_h))]


@dataclass
class Carousel(Layout):
    """Circular montage: one wedge per image, one page. Overlap/angles in degrees, sizes in points.

    Even wedges are drawn first, odd wedges on top, so the overlap shows the odd image.
    """
    segments: int = 4
    overlap: float = 30.0
    angle_offset: float = 0.0
    inner_radius: float = 0.0

    def add(self, item: ImageItem) -> None:
        super().add(item)
        self.segments = max(self.segments, len(self.images))  # grows like the original

    @property
    def pages(self) -> int:
        return 1

    def wedges(self) -> list[tuple[ImageItem, list[tuple[float, float]], Rect]]:
        from .geometry import bbox, segment_angles, wedge_polygon
        p = self.page
        cx, cy = p.left + p.imageable_w / 2, p.top + p.imageable_h / 2
        r = min(p.imageable_w, p.imageable_h) / 2
        out = []
        for i, (a0, a1) in enumerate(segment_angles(self.segments, self.angle_offset)):
            if i < len(self.images):
                poly = wedge_polygon(cx, cy, r, a0 - self.overlap / 2, a1 + self.overlap / 2, self.inner_radius)
                out.append((self.images[i], poly, bbox(poly)))
        return out

    def slots(self, page: int):
        return [(img, box) for img, _, box in self.wedges()]


MM = 72 / 25.4  # points per millimetre


@lru_cache(maxsize=512)
def _size(path: str, mtime: int) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        if im.getexif().get(274, 1) in (5, 6, 7, 8):  # EXIF-rotated: the render swaps too
            w, h = h, w
    return w, h


def aspect(item: ImageItem) -> float:
    """Width / height of the image as it will be drawn. Unreadable files count as square so layout never crashes."""
    try:
        w, h = _size(item.path, os.stat(item.path).st_mtime_ns)
        return w / h
    except Exception:
        return 1.0


@dataclass
class FixedSize(Layout):
    """Prints of one fixed size (passport, 4x6, wallet...) packed to fill the page, block centred.

    shortcut: a cell larger than the imageable area still gets one column/row and runs off the page;
    the UI keeps cells within the page, add a check here if the CLI needs it.
    """
    cell_w: float = 35 * MM
    cell_h: float = 45 * MM
    hgutter: float = 6.0
    vgutter: float = 6.0

    def __post_init__(self):
        if self.cell_w <= 0 or self.cell_h <= 0:
            raise ValueError("cell size must be > 0")

    @property
    def cols(self) -> int:
        return max(1, int((self.page.imageable_w + self.hgutter) // (self.cell_w + self.hgutter)))

    @property
    def rows(self) -> int:
        return max(1, int((self.page.imageable_h + self.vgutter) // (self.cell_h + self.vgutter)))

    @property
    def per_page(self) -> int:
        return self.rows * self.cols

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def slots(self, page: int):
        p = self.page
        x0 = p.left + (p.imageable_w - (self.cols * self.cell_w + (self.cols - 1) * self.hgutter)) / 2
        y0 = p.top + (p.imageable_h - (self.rows * self.cell_h + (self.rows - 1) * self.vgutter)) / 2
        start = page * self.per_page
        out = []
        for i, img in enumerate(self.images[start:start + self.per_page]):
            r, c = divmod(i, self.cols)
            out.append((img, Rect(x0 + c * (self.cell_w + self.hgutter), y0 + r * (self.cell_h + self.vgutter),
                                  self.cell_w, self.cell_h)))
        return out


@dataclass
class Mosaic(Layout):
    """Justified rows: every photo keeps its shape (no cropping), rows fill the page width.

    The last row is not stretched. Rows that don't fit the page start a new one.
    """
    row_height: float = 150.0
    gutter: float = 6.0

    def __post_init__(self):
        if self.row_height <= 0:
            raise ValueError("row_height must be > 0")

    def _paged(self) -> list[list[tuple[ImageItem, Rect]]]:
        p, g = self.page, self.gutter
        iw, ih = p.imageable_w, p.imageable_h
        rows, cur, asum = [], [], 0.0
        for img in self.images:
            a = aspect(img)
            cur.append((img, a))
            asum += a
            if asum * self.row_height + g * (len(cur) - 1) >= iw:  # full: stretch to the exact width
                rows.append((cur, True))
                cur, asum = [], 0.0
        if cur:
            rows.append((cur, False))
        pages: list[list] = [[]]
        y = 0.0
        for row, full in rows:
            asum = sum(a for _, a in row)
            h = (iw - g * (len(row) - 1)) / asum if full else self.row_height
            h = min(h, ih)
            if y + h > ih and pages[-1]:
                pages.append([])
                y = 0.0
            x = p.left
            for img, a in row:
                pages[-1].append((img, Rect(x, p.top + y, a * h, h)))
                x += a * h + g
            y += h + g
        return pages

    @property
    def pages(self) -> int:
        return len(self._paged())

    def slots(self, page: int):
        paged = self._paged()
        return paged[page] if page < len(paged) else []
