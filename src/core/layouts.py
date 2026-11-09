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


def corner_ticks(r: Rect, length: float = 5.0, gap: float = 1.0) -> list[tuple[float, float, float, float]]:
    """Eight short crop-mark segments (x0, y0, x1, y1) just outside a rectangle's corners."""
    x0, y0, x1, y1 = r.x, r.y, r.x + r.w, r.y + r.h
    out = []
    for x, dx in ((x0, -1), (x1, 1)):
        for y, dy in ((y0, -1), (y1, 1)):
            out.append((x + dx * gap, y, x + dx * (gap + length), y))
            out.append((x, y + dy * gap, x, y + dy * (gap + length)))
    return out


@dataclass
class Layout:
    page: PageExtent = field(default_factory=PageExtent)
    images: list[ImageItem] = field(default_factory=list)
    marks: bool = False   # draw crop marks at each photo's corners (layouts that support it)

    def add(self, item: ImageItem) -> None:
        self.images.append(item)

    @property
    def pages(self) -> int:
        return max(1, len(self.images))

    def slots(self, page: int) -> list[tuple[ImageItem, Rect]]:
        raise NotImplementedError

    def cut_guides(self, page: int) -> list[tuple[float, float, float, float]]:
        """Line segments drawn under the photos, in points."""
        return [seg for _, r in self.slots(page) for seg in corner_ticks(r)] if self.marks else []

    def captions(self, page: int) -> list[tuple[str, Rect]]:
        """(text, band) pairs drawn over the page, in points."""
        return []


@dataclass
class NUp(Layout):
    rows: int = 2
    cols: int = 2
    hgutter: float = 8.0
    vgutter: float = 8.0
    caption: str = "none"      # none | filename | number | both: a contact-sheet line under each photo
    caption_pt: float = 8.0

    def __post_init__(self):
        if self.rows < 1 or self.cols < 1:
            raise ValueError("rows and cols must be >= 1")
        if self.caption not in ("none", "filename", "number", "both"):
            raise ValueError(f"unknown caption mode {self.caption!r}")

    @property
    def _band(self) -> float:
        return self.caption_pt * 1.8 if self.caption != "none" else 0.0

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
        out = []
        for i, img in enumerate(self.images[start:start + self.per_page]):
            c = self.cell(*divmod(i, self.cols))
            out.append((img, Rect(c.x, c.y, c.w, max(1.0, c.h - self._band))))
        return out

    def captions(self, page: int):
        if self.caption == "none":
            return []
        start, out = page * self.per_page, []
        for i, img in enumerate(self.images[start:start + self.per_page]):
            c, n, name = self.cell(*divmod(i, self.cols)), start + i + 1, os.path.basename(img.path)
            text = {"filename": name, "number": str(n), "both": f"{n}  {name}"}[self.caption]
            out.append((text, Rect(c.x, c.y + c.h - self._band, c.w, self._band)))
        return out


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

    def _items(self) -> list[ImageItem]:
        return self.images

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
        return max(1, ceil_div(len(self._items()), self.per_page))

    def slots(self, page: int):
        p = self.page
        x0 = p.left + (p.imageable_w - (self.cols * self.cell_w + (self.cols - 1) * self.hgutter)) / 2
        y0 = p.top + (p.imageable_h - (self.rows * self.cell_h + (self.rows - 1) * self.vgutter)) / 2
        start = page * self.per_page
        out = []
        for i, img in enumerate(self._items()[start:start + self.per_page]):
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


@dataclass
class Strip(Layout):
    """Photo-booth strips: `frames` photos stacked in each strip, strips tiled over the page.

    Photos fill strips in order (strip 1 first). Each strip is outlined so it can be cut out.
    """
    frames: int = 4
    strip_w: float = 51 * MM    # 2 in
    strip_h: float = 152 * MM   # 6 in
    padding: float = 6.0
    frame_gap: float = 4.0
    gutter: float = 8.0         # space between strips on the page

    def __post_init__(self):
        if self.frames < 1 or self.strip_w <= 0 or self.strip_h <= 0:
            raise ValueError("frames and strip size must be positive")
        if self.frame_h <= 0 or self.strip_w - 2 * self.padding <= 0:
            raise ValueError("strip is too small for its frames and padding")

    @property
    def frame_h(self) -> float:
        return (self.strip_h - 2 * self.padding - (self.frames - 1) * self.frame_gap) / self.frames

    @property
    def cols(self) -> int:
        return max(1, int((self.page.imageable_w + self.gutter) // (self.strip_w + self.gutter)))

    @property
    def rows(self) -> int:
        return max(1, int((self.page.imageable_h + self.gutter) // (self.strip_h + self.gutter)))

    @property
    def per_page(self) -> int:
        return self.cols * self.rows * self.frames

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def _strip_xy(self, n: int) -> tuple[float, float]:
        p = self.page
        x0 = p.left + (p.imageable_w - (self.cols * self.strip_w + (self.cols - 1) * self.gutter)) / 2
        y0 = p.top + (p.imageable_h - (self.rows * self.strip_h + (self.rows - 1) * self.gutter)) / 2
        r, c = divmod(n, self.cols)
        return x0 + c * (self.strip_w + self.gutter), y0 + r * (self.strip_h + self.gutter)

    def slots(self, page: int):
        start, out = page * self.per_page, []
        for i, img in enumerate(self.images[start:start + self.per_page]):
            n, k = divmod(i, self.frames)
            sx, sy = self._strip_xy(n)
            out.append((img, Rect(sx + self.padding, sy + self.padding + k * (self.frame_h + self.frame_gap),
                                  self.strip_w - 2 * self.padding, self.frame_h)))
        return out

    def cut_guides(self, page: int):
        used = ceil_div(len(self.slots(page)), self.frames) if self.slots(page) else 0
        out = []
        for n in range(used):
            x, y = self._strip_xy(n)
            x1, y1 = x + self.strip_w, y + self.strip_h
            out += [(x, y, x1, y), (x1, y, x1, y1), (x1, y1, x, y1), (x, y1, x, y)]
        return out


@dataclass
class Passport(FixedSize):
    """Passport/ID prints the way a photo shop sells them: copies of each photo filling the sheet.

    copies > 0 repeats every photo that many times. copies == 0 ("fill sheet") shares one sheet equally
    between the photos, so a single photo fills the whole page. The same ImageItem appears in several slots.
    """
    copies: int = 0

    def __post_init__(self):
        super().__post_init__()
        if self.copies < 0:
            raise ValueError("copies must be >= 0")

    def _items(self) -> list[ImageItem]:
        if not self.images:
            return []
        n = self.copies or max(1, self.per_page // len(self.images))
        return [img for img in self.images for _ in range(n)]
