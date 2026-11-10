"""Layout geometry: which slot each image gets, and how many pages. No rendering, no Qt."""
from __future__ import annotations

import calendar
import datetime
import math
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
    box: tuple | None = None    # Collage only: (x, y, w, h) in points; None = not placed yet


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

    def cards(self, page: int) -> list[Rect]:
        """White cards drawn under the photos (Polaroid), in points."""
        return []

    # Rendering hooks. shape "ellipse" crops every photo to a circle; the colours style captions and cards.
    shape = "rect"
    caption_color = (70, 70, 70)

    def shapes(self, page: int) -> list[tuple[str, Rect, tuple]]:
        """Decoration drawn under the photos: (kind, rect, rgb). Kinds: card (filled, outlined), rect, round,
        ellipse (filled) and frame (outline only)."""
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
    direction: str = "auto"     # auto (horizontal on a landscape page) | vertical | horizontal

    def __post_init__(self):
        if self.frames < 1 or self.strip_w <= 0 or self.strip_h <= 0:
            raise ValueError("frames and strip size must be positive")
        if self.frame_h <= 0 or self.strip_w - 2 * self.padding <= 0:
            raise ValueError("strip is too small for its frames and padding")
        if self.direction not in ("auto", "vertical", "horizontal"):
            raise ValueError(f"unknown strip direction {self.direction!r}")

    @property
    def horizontal(self) -> bool:
        """Strips run left to right (frames side by side) instead of top to bottom."""
        return self.direction == "horizontal" or (self.direction == "auto" and self.page.imageable_w > self.page.imageable_h)

    def _avail(self) -> tuple[float, float]:
        """The space strips are tiled in, as (across, along): the page's width and height, swapped if horizontal."""
        p = self.page
        return (p.imageable_h, p.imageable_w) if self.horizontal else (p.imageable_w, p.imageable_h)

    def _page_rect(self, x: float, y: float, w: float, h: float) -> Rect:
        """A rectangle in strip space (x across the strip, y along it, origin at the imageable corner) on the page."""
        p = self.page
        return Rect(p.left + y, p.top + x, h, w) if self.horizontal else Rect(p.left + x, p.top + y, w, h)

    @property
    def frame_h(self) -> float:
        return (self.strip_h - 2 * self.padding - (self.frames - 1) * self.frame_gap) / self.frames

    @property
    def cols(self) -> int:
        """Strips side by side across their width."""
        return max(1, int((self._avail()[0] + self.gutter) // (self.strip_w + self.gutter)))

    @property
    def rows(self) -> int:
        """Strips one after another along their length."""
        return max(1, int((self._avail()[1] + self.gutter) // (self.strip_h + self.gutter)))

    @property
    def per_page(self) -> int:
        return self.cols * self.rows * self.frames

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def _strip_xy(self, n: int) -> tuple[float, float]:
        """Where strip n starts, in strip space (see _page_rect)."""
        across, along = self._avail()
        x0 = (across - (self.cols * self.strip_w + (self.cols - 1) * self.gutter)) / 2
        y0 = (along - (self.rows * self.strip_h + (self.rows - 1) * self.gutter)) / 2
        r, c = divmod(n, self.cols)
        return x0 + c * (self.strip_w + self.gutter), y0 + r * (self.strip_h + self.gutter)

    def slots(self, page: int):
        start, out = page * self.per_page, []
        for i, img in enumerate(self.images[start:start + self.per_page]):
            n, k = divmod(i, self.frames)
            sx, sy = self._strip_xy(n)
            out.append((img, self._page_rect(sx + self.padding, sy + self.padding + k * (self.frame_h + self.frame_gap),
                                             self.strip_w - 2 * self.padding, self.frame_h)))
        return out

    def cut_guides(self, page: int):
        used = ceil_div(len(self.slots(page)), self.frames) if self.slots(page) else 0
        out = []
        for n in range(used):
            r = self._page_rect(*self._strip_xy(n), self.strip_w, self.strip_h)
            x, y, x1, y1 = r.x, r.y, r.x + r.w, r.y + r.h
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


@dataclass
class Polaroid(NUp):
    """Instant-photo prints: a grid of white cards, each with a thin border and a thicker bottom edge.

    Cards keep the real instant-film proportions (card_ratio, width / height) and are centred in their cell.
    The caption goes in the thick bottom edge.
    """
    border: float = 10.0
    bottom_ratio: float = 3.0     # bottom edge = border * bottom_ratio
    card_ratio: float = 88 / 107  # Polaroid 600 card: 88 x 107 mm

    def __post_init__(self):
        super().__post_init__()
        if self.border <= 0 or self.bottom_ratio < 1 or self.card_ratio <= 0:
            raise ValueError("border must be > 0, bottom_ratio >= 1 and card_ratio > 0")

    @property
    def _band(self) -> float:
        return 0.0   # captions live inside the card, not under the cell

    def _card(self, row: int, col: int) -> Rect:
        c = self.cell(row, col)
        w = min(c.w, c.h * self.card_ratio)
        h = w / self.card_ratio
        return Rect(c.x + (c.w - w) / 2, c.y + (c.h - h) / 2, w, h)

    def _photo(self, card: Rect) -> Rect:
        b = self.border
        return Rect(card.x + b, card.y + b, max(1.0, card.w - 2 * b), max(1.0, card.h - b - b * self.bottom_ratio))

    def slots(self, page: int):
        start = page * self.per_page
        return [(img, self._photo(self._card(*divmod(i, self.cols))))
                for i, img in enumerate(self.images[start:start + self.per_page])]

    def cards(self, page: int):
        start = page * self.per_page
        return [self._card(*divmod(i, self.cols)) for i in range(len(self.images[start:start + self.per_page]))]

    def captions(self, page: int):
        if self.caption == "none":
            return []
        start, out = page * self.per_page, []
        for i, img in enumerate(self.images[start:start + self.per_page]):
            card = self._card(*divmod(i, self.cols))
            n, name = start + i + 1, os.path.basename(img.path)
            text = {"filename": name, "number": str(n), "both": f"{n}  {name}"}[self.caption]
            edge = self.border * self.bottom_ratio
            h = min(edge, self.caption_pt * 1.8)
            out.append((text, Rect(card.x + self.border, card.y + card.h - edge + (edge - h) / 2,
                                   card.w - 2 * self.border, h)))
        return out

    def cut_guides(self, page: int):
        return [seg for card in self.cards(page) for seg in corner_ticks(card)] if self.marks else []


@dataclass
class Triptych(Layout):
    """One photo split into `panels` pieces with a gap between them, all on one page (like a canvas triptych).

    Each photo gets its own page. The pieces together keep the photo's shape, so nothing is cropped except
    what falls in the gaps.
    """
    panels: int = 3
    gap: float = 8.0
    vertical: bool = False   # stack the pieces top to bottom instead of left to right

    def __post_init__(self):
        if self.panels < 1 or self.gap < 0:
            raise ValueError("panels must be >= 1 and gap >= 0")

    @property
    def pages(self) -> int:
        return max(1, len(self.images))

    def panels_for(self, page: int) -> tuple[Rect, list[Rect]]:
        """(the whole picture's rectangle, the panel rectangles), in points."""
        p = self.page
        a = aspect(self.images[page]) if page < len(self.images) else 1.0
        iw, ih = p.imageable_w, p.imageable_h
        w, h = (ih * a, ih) if iw / ih > a else (iw, iw / a)
        g = Rect(p.left + (iw - w) / 2, p.top + (ih - h) / 2, w, h)
        n, gap = self.panels, self.gap
        if self.vertical:
            ph = max(1.0, (h - gap * (n - 1)) / n)
            return g, [Rect(g.x, g.y + i * (ph + gap), g.w, ph) for i in range(n)]
        pw = max(1.0, (w - gap * (n - 1)) / n)
        return g, [Rect(g.x + i * (pw + gap), g.y, pw, g.h) for i in range(n)]

    def slots(self, page: int):
        if page >= len(self.images):
            return []
        return [(self.images[page], r) for r in self.panels_for(page)[1]]


# Studio-style sheets: name -> (label, paper in points (portrait), [(width mm, height mm, count), ...])
RECIPES = {
    "5x7-4wallets": ("5×7 + 4 wallets", (576, 720), [(127, 177.8, 1), (63.5, 88.9, 4)]),
    "3x3.5x5": ("3 × 3½×5", (576, 720), [(88.9, 127, 3)]),
    "2x3.5x5-4x2x3": ("2 × 3½×5 + 4 × 2×3", (576, 720), [(88.9, 127, 2), (50.8, 76.2, 4)]),
    "2x4x6": ("2 × 4×6", (576, 720), [(101.6, 152.4, 2)]),
    "6wallets": ("6 wallets", (576, 720), [(63.5, 88.9, 6)]),
}


def _maxrects_once(sizes, w, h, order, mode, upright=None):
    free = [(0.0, 0.0, w, h)]
    out: list = [None] * len(sizes)
    for i in order:
        iw, ih = sizes[i]
        best = None
        for fx, fy, fw, fh in free:
            for rw, rh in (((iw, ih),) if i == upright else ((iw, ih), (ih, iw))):
                if rw <= fw + 1e-6 and rh <= fh + 1e-6:
                    if mode == "short":     # best short-side fit
                        score = (min(fw - rw, fh - rh), max(fw - rw, fh - rh))
                    elif mode == "area":    # best area fit
                        score = (fw * fh - rw * rh, min(fw - rw, fh - rh))
                    else:                   # bottom-left
                        score = (fy + rh, fx)
                    if best is None or score < best[0]:
                        best = (score, fx, fy, rw, rh)
        if best is None:
            continue
        _, x, y, rw, rh = best
        out[i] = (x, y, rw, rh)
        new = []
        for fx, fy, fw, fh in free:
            if x >= fx + fw or x + rw <= fx or y >= fy + fh or y + rh <= fy:
                new.append((fx, fy, fw, fh))
                continue
            if x > fx:
                new.append((fx, fy, x - fx, fh))
            if x + rw < fx + fw:
                new.append((x + rw, fy, fx + fw - x - rw, fh))
            if y > fy:
                new.append((fx, fy, fw, y - fy))
            if y + rh < fy + fh:
                new.append((fx, y + rh, fw, fy + fh - y - rh))
        new = list(dict.fromkeys(new))
        free = [r for r in new if not any(o != r and o[0] <= r[0] + 1e-6 and o[1] <= r[1] + 1e-6
                                          and o[0] + o[2] >= r[0] + r[2] - 1e-6 and o[1] + o[3] >= r[1] + r[3] - 1e-6
                                          for o in new)]
    return out


def _maxrects(sizes: list[tuple[float, float]], w: float, h: float) -> list[tuple[float, float, float, float] | None]:
    """Pack rectangles into w x h, rotation allowed. Returns (x, y, w, h) or None per input.

    Greedy packing can paint itself into a corner by a few points, so several orderings and fit rules are tried
    and the one that places the most (then uses the smallest area) wins.
    """
    n = range(len(sizes))
    orders = [sorted(n, key=lambda i: -sizes[i][0] * sizes[i][1]), sorted(n, key=lambda i: -max(sizes[i])), list(n)]
    best = None
    for order in orders:
        for mode in ("short", "area", "bl"):
            for upright in (None, order[0] if order else None):   # also try keeping the biggest print upright
                out = _maxrects_once(sizes, w, h, order, mode, upright)
                got = [r for r in out if r]
                extent = (max((x + rw for x, y, rw, rh in got), default=0)
                          * max((y + rh for x, y, rw, rh in got), default=0))
                key = (-len(got), extent)
                if best is None or key < best[0]:
                    best = (key, out)
    return best[1]


@dataclass
class Package(Layout):
    """A studio package sheet: prints of several sizes on one sheet, e.g. one 5x7 plus four wallets.

    `prints` is [(width mm, height mm, count), ...]. Each photo gets its own sheet with every print filled by
    that photo. Prints are packed to use the sheet well and may be turned sideways; the block is centred.
    """
    prints: list = field(default_factory=lambda: [tuple(x) for x in RECIPES["5x7-4wallets"][2]])
    gutter: float = 4.0

    def __post_init__(self):
        if not self.prints or any(w <= 0 or h <= 0 or n < 1 for w, h, n in self.prints):
            raise ValueError("prints needs (width, height, count) entries with positive sizes and counts")

    @property
    def pages(self) -> int:
        return max(1, len(self.images))

    def _placed(self) -> list[Rect | None]:
        p, g = self.page, self.gutter
        sizes = [(w * MM + g, h * MM + g) for w, h, n in self.prints for _ in range(int(n))]
        packed = _maxrects(sizes, p.imageable_w + g, p.imageable_h + g)
        got = [r for r in packed if r]
        if not got:
            return [None] * len(sizes)
        right, bottom = max(x + w for x, y, w, h in got) - g, max(y + h for x, y, w, h in got) - g
        dx, dy = (p.imageable_w - right) / 2, (p.imageable_h - bottom) / 2
        return [Rect(p.left + dx + r[0], p.top + dy + r[1], r[2] - g, r[3] - g) if r else None for r in packed]

    @property
    def missing(self) -> int:
        """How many prints of the recipe do not fit on this paper."""
        return sum(r is None for r in self._placed())

    @property
    def per_page(self) -> int:
        return sum(1 for r in self._placed() if r)

    def slots(self, page: int):
        if page >= len(self.images):
            return []
        return [(self.images[page], r) for r in self._placed() if r]


@dataclass
class Collage(Layout):
    """Free-form: every photo has its own position and size (ImageItem.box), later photos on top.

    One page. Photos without a box are placed automatically; Collage.autoarrange() re-flows all of them.
    """
    snap: bool = True    # the editor snaps moves and resizes to page edges, centres and other photos

    @property
    def pages(self) -> int:
        return 1

    def autoarrange(self) -> None:
        """Lay every photo out as whole, non-overlapping rows on the page."""
        if not self.images:
            return
        rh = 160.0
        while True:
            m = Mosaic(page=self.page, row_height=rh, gutter=8.0)
            m.images = self.images
            paged = m._paged()
            if len(paged) == 1 or rh <= 24:
                break
            rh *= 0.85
        by_id = {id(img): r for img, r in paged[0]}
        for img in self.images:
            r = by_id.get(id(img))
            if r:
                img.box = (r.x, r.y, r.w, r.h)

    def _place_new(self, item: ImageItem, n: int) -> None:
        p = self.page
        a = aspect(item)
        w = min(p.imageable_w * 0.4, p.imageable_h * 0.4 * a)
        h = w / a
        x = min(p.left + 14 * n, p.left + p.imageable_w - w)
        y = min(p.top + 14 * n, p.top + p.imageable_h - h)
        item.box = (x, y, w, h)

    def _ensure(self) -> None:
        missing = [i for i in self.images if i.box is None]
        if not missing:
            return
        if len(missing) == len(self.images):
            self.autoarrange()
        else:
            for n, it in enumerate(missing, start=len(self.images) - len(missing)):
                self._place_new(it, n)

    def clamp(self, box: tuple) -> tuple:
        """Keep a box inside the imageable area, shrinking it (same shape) if it is bigger than the area."""
        p = self.page
        x, y, w, h = box
        k = min(1.0, p.imageable_w / w, p.imageable_h / h)
        w, h = w * k, h * k
        return (min(max(x, p.left), p.left + p.imageable_w - w), min(max(y, p.top), p.top + p.imageable_h - h), w, h)

    def slots(self, page: int):
        self._ensure()
        return [(img, Rect(*self.clamp(img.box))) for img in self.images]

    def snap_targets(self, skip: int | None = None) -> tuple[list[float], list[float]]:
        """x and y positions a moving photo can snap to: page edges and centre, and other photos' edges and centres."""
        p = self.page
        xs = [p.left, p.left + p.imageable_w / 2, p.left + p.imageable_w]
        ys = [p.top, p.top + p.imageable_h / 2, p.top + p.imageable_h]
        for i, img in enumerate(self.images):
            if i != skip and img.box:
                x, y, w, h = img.box
                xs += [x, x + w / 2, x + w]
                ys += [y, y + h / 2, y + h]
        return xs, ys


def snap_delta(values: list[float], targets: list[float], tol: float) -> float | None:
    """Smallest shift (|d| <= tol) that puts one of `values` on one of `targets`; None if nothing is close."""
    best = None
    for v in values:
        for t in targets:
            d = t - v
            if abs(d) <= tol and (best is None or abs(d) < abs(best)):
                best = d
    return best


MAT_COLORS = {"white": (255, 255, 255), "black": (20, 20, 20), "cream": (247, 241, 225)}


@dataclass
class FramedPrint(Layout):
    """One photo per page matted like a framed print: a wide border, a thin line around the picture, a caption.

    The mat fills the imageable area; the photo sits inside it at its own shape (or fills the opening if cropped).
    """
    mat: float = 36.0
    color: str = "white"          # white | black | cream
    caption: str = "none"         # none | filename | number | both, under the photo
    caption_pt: float = 10.0

    def __post_init__(self):
        if self.mat < 0:
            raise ValueError("mat must be >= 0")
        if self.color not in MAT_COLORS:
            raise ValueError(f"unknown mat colour {self.color!r}")
        if self.caption not in ("none", "filename", "number", "both"):
            raise ValueError(f"unknown caption mode {self.caption!r}")

    @property
    def pages(self) -> int:
        return max(1, len(self.images))

    @property
    def caption_color(self) -> tuple:
        return (225, 225, 225) if self.color == "black" else (70, 70, 70)

    def _card(self) -> Rect:
        p = self.page
        return Rect(p.left, p.top, p.imageable_w, p.imageable_h)

    def _opening(self) -> Rect:
        c, m = self._card(), self.mat
        bottom = m + (self.caption_pt * 2.4 if self.caption != "none" else 0.0)
        return Rect(c.x + m, c.y + m, max(1.0, c.w - 2 * m), max(1.0, c.h - m - bottom))

    def _photo(self, page: int) -> Rect:
        o, img = self._opening(), self.images[page]
        if img.crop:
            return o
        a = aspect(img)
        w, h = (o.h * a, o.h) if o.w / o.h > a else (o.w, o.w / a)
        return Rect(o.x + (o.w - w) / 2, o.y + (o.h - h) / 2, w, h)

    def slots(self, page: int):
        return [(self.images[page], self._photo(page))] if page < len(self.images) else []

    def shapes(self, page: int):
        if page >= len(self.images):
            return []
        r = self._photo(page)
        line = (90, 90, 90) if self.color == "black" else (185, 185, 185)
        return [("card", self._card(), MAT_COLORS[self.color]), ("frame", Rect(r.x - 2, r.y - 2, r.w + 4, r.h + 4), line)]

    def captions(self, page: int):
        if self.caption == "none" or page >= len(self.images):
            return []
        c, n = self._card(), page + 1
        name = os.path.basename(self.images[page].path)
        text = {"filename": name, "number": str(n), "both": f"{n}  {name}"}[self.caption]
        return [(text, Rect(c.x + self.mat, c.y + c.h - self.mat - self.caption_pt * 2.1, c.w - 2 * self.mat,
                            self.caption_pt * 1.8))]


@dataclass
class Badges(Layout):
    """Round prints for button badges, stickers or coasters: circles packed on the page, optionally staggered.

    The photo is cropped to the circle. Common pin-back badge graphics: 25, 32, 38, 44, 58 and 75 mm.
    """
    diameter: float = 38 * MM
    gutter: float = 6.0
    staggered: bool = True     # offset every other row so the circles nest (fits about 15% more)
    shape = "ellipse"

    def __post_init__(self):
        if self.diameter <= 0 or self.gutter < 0:
            raise ValueError("diameter must be > 0 and gutter >= 0")

    def _positions(self) -> list[tuple[float, float]]:
        p, d, g = self.page, self.diameter, self.gutter
        pitch = d + g
        if p.imageable_w < d or p.imageable_h < d:
            return []
        row_pitch = pitch * math.sqrt(3) / 2 if self.staggered else pitch
        rows = int((p.imageable_h - d) // row_pitch + 1e-9) + 1
        even = int((p.imageable_w + g) // pitch)
        odd = int((p.imageable_w + g - pitch / 2) // pitch) if self.staggered else even
        counts = [even if r % 2 == 0 else odd for r in range(rows)]
        offs = [pitch / 2 if self.staggered and r % 2 else 0.0 for r in range(rows)]
        block_w = max((o + n * pitch - g for o, n in zip(offs, counts) if n), default=0.0)
        x0 = p.left + (p.imageable_w - block_w) / 2
        y0 = p.top + (p.imageable_h - ((rows - 1) * row_pitch + d)) / 2
        return [(x0 + offs[r] + c * pitch, y0 + r * row_pitch) for r in range(rows) for c in range(counts[r])]

    @property
    def per_page(self) -> int:
        return len(self._positions())

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page)) if self.per_page else 1

    def slots(self, page: int):
        pos = self._positions()
        start = page * len(pos)
        return [(img, Rect(x, y, self.diameter, self.diameter))
                for img, (x, y) in zip(self.images[start:start + len(pos)], pos)]

    def cut_guides(self, page: int):
        if not self.marks:
            return []
        out, r = [], self.diameter / 2
        for _, c in self.slots(page):
            cx, cy = c.x + r, c.y + r
            pts = [(cx + r * math.cos(2 * math.pi * i / 48), cy + r * math.sin(2 * math.pi * i / 48)) for i in range(49)]
            out += [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]
        return out


@dataclass
class Feature(Layout):
    """One large hero photo with a row (or column) of smaller photos next to it, repeated page after page.

    Each page takes 1 + thumbs photos in order: the first is the hero.
    """
    thumbs: int = 4
    side: bool = False         # thumbnails in a column beside the hero instead of a row beneath it
    hero_share: float = 0.7    # the hero's share of the page height (or width, when side)
    gutter: float = 6.0

    def __post_init__(self):
        if self.thumbs < 1 or not 0.2 <= self.hero_share <= 0.9 or self.gutter < 0:
            raise ValueError("thumbs must be >= 1, hero_share between 0.2 and 0.9, gutter >= 0")

    @property
    def per_page(self) -> int:
        return 1 + self.thumbs

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def _rects(self) -> list[Rect]:
        p, g, n = self.page, self.gutter, self.thumbs
        w, h = p.imageable_w, p.imageable_h
        if self.side:
            hw = (w - g) * self.hero_share
            tw, th = max(1.0, w - g - hw), max(1.0, (h - g * (n - 1)) / n)
            return [Rect(p.left, p.top, hw, h)] + [Rect(p.left + hw + g, p.top + i * (th + g), tw, th) for i in range(n)]
        hh = (h - g) * self.hero_share
        th, tw = max(1.0, h - g - hh), max(1.0, (w - g * (n - 1)) / n)
        return [Rect(p.left, p.top, w, hh)] + [Rect(p.left + i * (tw + g), p.top + hh + g, tw, th) for i in range(n)]

    def slots(self, page: int):
        start = page * self.per_page
        return list(zip(self.images[start:start + self.per_page], self._rects()))


@dataclass
class Filmstrip(Strip):
    """35 mm negative strips: black film with sprocket holes and 2:3 frames, several strips per page.

    The strip height follows from the frame count; if it would not fit the page, the film is scaled down.
    """
    frames: int = 6
    strip_w: float = 35 * MM

    def __post_init__(self):
        if self.frames < 1 or self.strip_w <= 0:
            raise ValueError("frames and strip width must be positive")
        h_max = self._avail()[1]                        # the length the film may use on this page
        for _ in range(2):   # the second pass applies the scale-down
            fw = self.strip_w * 0.686                   # 24 of 35 mm
            self.padding = (self.strip_w - fw) / 2
            self.frame_gap = self.strip_w * 0.057       # 2 mm
            self.strip_h = 2 * self.padding + self.frames * fw * 1.5 + (self.frames - 1) * self.frame_gap
            if self.strip_h <= h_max * (1 + 1e-9):
                break
            self.strip_w *= h_max / self.strip_h
        super().__post_init__()

    def cut_guides(self, page: int):
        return []   # the black film is its own outline

    def shapes(self, page: int):
        used = ceil_div(len(self.slots(page)), self.frames) if self.slots(page) else 0
        w = self.strip_w
        hole_w, hole_h, pitch, edge = w * 0.06, w * 0.08, w * 0.136, w * 0.05
        out: list = []
        for n in range(used):
            x, y = self._strip_xy(n)
            out.append(("rect", self._page_rect(x, y, w, self.strip_h), (22, 22, 22)))
            yy = y + pitch / 2
            while yy + hole_h <= y + self.strip_h - pitch / 4 + 1e-9:
                out.append(("round", self._page_rect(x + edge, yy, hole_w, hole_h), (255, 255, 255)))
                out.append(("round", self._page_rect(x + w - edge - hole_w, yy, hole_w, hole_h), (255, 255, 255)))
                yy += pitch
        return out


# Bento templates: name -> (label, columns, rows, [(col, row, colspan, rowspan), ...]). The first cell is the biggest.
BENTO = {
    "hero-left": ("Hero left (3)", 3, 2, [(0, 0, 2, 2), (2, 0, 1, 1), (2, 1, 1, 1)]),
    "hero-top": ("Hero top (3)", 2, 3, [(0, 0, 2, 2), (0, 2, 1, 1), (1, 2, 1, 1)]),
    "wide": ("Wide (4)", 4, 2, [(0, 0, 2, 2), (2, 0, 2, 1), (2, 1, 1, 1), (3, 1, 1, 1)]),
    "pinwheel": ("Pinwheel (5)", 3, 3, [(0, 0, 2, 1), (2, 0, 1, 2), (1, 2, 2, 1), (0, 1, 1, 2), (1, 1, 1, 1)]),
    "magazine": ("Magazine (5)", 4, 3, [(0, 0, 2, 3), (2, 0, 2, 1), (2, 1, 1, 2), (3, 1, 1, 1), (3, 2, 1, 1)]),
    "six-tiles": ("Six tiles (6)", 3, 3, [(0, 0, 2, 2), (2, 0, 1, 1), (2, 1, 1, 1), (0, 2, 1, 1), (1, 2, 1, 1), (2, 2, 1, 1)]),
}


@dataclass
class Bento(Layout):
    """Asymmetric tile grids (one big tile, several small ones) that fill the whole page, repeated per page.

    Each page takes as many photos as the template has tiles, in order; the first tile is the biggest.
    """
    template: str = "hero-left"
    gutter: float = 6.0

    def __post_init__(self):
        if self.template not in BENTO:
            raise ValueError(f"unknown bento template {self.template!r}")
        if self.gutter < 0:
            raise ValueError("gutter must be >= 0")

    @property
    def per_page(self) -> int:
        return len(BENTO[self.template][3])

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def _rects(self) -> list[Rect]:
        _, cols, rows, cells = BENTO[self.template]
        p, g = self.page, self.gutter
        cw, ch = (p.imageable_w - g * (cols - 1)) / cols, (p.imageable_h - g * (rows - 1)) / rows
        return [Rect(p.left + c * (cw + g), p.top + r * (ch + g), cs * cw + (cs - 1) * g, rs * ch + (rs - 1) * g)
                for c, r, cs, rs in cells]

    def slots(self, page: int):
        start = page * self.per_page
        return list(zip(self.images[start:start + self.per_page], self._rects()))


@dataclass
class Spiral(Layout):
    """Photos in a golden-ratio spiral: each takes `share` of what is left, turning a quarter each time.

    Each page takes `photos` photos in order; the last one fills the remaining space.
    """
    photos: int = 5
    share: float = 0.618
    clockwise: bool = True
    gutter: float = 6.0

    def __post_init__(self):
        if self.photos < 2 or not 0.3 <= self.share <= 0.8 or self.gutter < 0:
            raise ValueError("photos must be >= 2, share between 0.3 and 0.8, gutter >= 0")

    @property
    def per_page(self) -> int:
        return self.photos

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.photos))

    def _rects(self) -> list[Rect]:
        p, g = self.page, self.gutter
        x, y, w, h = p.left, p.top, p.imageable_w, p.imageable_h
        order = ("left", "top", "right", "bottom") if self.clockwise else ("left", "bottom", "right", "top")
        out = []
        for i in range(self.photos - 1):
            side = order[i % 4]
            if side in ("left", "right"):
                pw = w * self.share
                out.append(Rect(x if side == "left" else x + w - pw, y, pw, h))
                if side == "left":
                    x += pw + g
                w = max(1.0, w - pw - g)
            else:
                ph = h * self.share
                out.append(Rect(x, y if side == "top" else y + h - ph, w, ph))
                if side == "top":
                    y += ph + g
                h = max(1.0, h - ph - g)
        out.append(Rect(x, y, w, h))
        return out

    def slots(self, page: int):
        start = page * self.photos
        return list(zip(self.images[start:start + self.photos], self._rects()))


@dataclass
class Honeycomb(Layout):
    """Hexagon-shaped photos nested like a honeycomb. `size` is the width across the flat sides."""
    size: float = 40 * MM
    gutter: float = 6.0
    shape = "hexagon"

    def __post_init__(self):
        if self.size <= 0 or self.gutter < 0:
            raise ValueError("size must be > 0 and gutter >= 0")

    @property
    def cell_h(self) -> float:
        return self.size * 2 / math.sqrt(3)   # point to point

    def _positions(self) -> list[tuple[float, float]]:
        p, g, w, h = self.page, self.gutter, self.size, self.cell_h
        pitch = w + g
        if p.imageable_w < w or p.imageable_h < h:
            return []
        row_pitch = pitch * math.sqrt(3) / 2
        rows = int((p.imageable_h - h) // row_pitch + 1e-9) + 1
        even, odd = int((p.imageable_w + g) // pitch), int((p.imageable_w + g - pitch / 2) // pitch)
        counts = [even if r % 2 == 0 else odd for r in range(rows)]
        offs = [pitch / 2 if r % 2 else 0.0 for r in range(rows)]
        block_w = max((o + n * pitch - g for o, n in zip(offs, counts) if n), default=0.0)
        x0 = p.left + (p.imageable_w - block_w) / 2
        y0 = p.top + (p.imageable_h - ((rows - 1) * row_pitch + h)) / 2
        return [(x0 + offs[r] + c * pitch, y0 + r * row_pitch) for r in range(rows) for c in range(counts[r])]

    @property
    def per_page(self) -> int:
        return len(self._positions())

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page)) if self.per_page else 1

    def slots(self, page: int):
        pos = self._positions()
        start = page * len(pos)
        return [(img, Rect(x, y, self.size, self.cell_h)) for img, (x, y) in zip(self.images[start:start + len(pos)], pos)]

    def cut_guides(self, page: int):
        if not self.marks:
            return []
        out = []
        for _, r in self.slots(page):
            pts = [(r.x + r.w / 2, r.y), (r.x + r.w, r.y + r.h / 4), (r.x + r.w, r.y + 3 * r.h / 4),
                   (r.x + r.w / 2, r.y + r.h), (r.x, r.y + 3 * r.h / 4), (r.x, r.y + r.h / 4), (r.x + r.w / 2, r.y)]
            out += [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]
        return out


# Fixed English names: the calendar module follows the system language, which would change the sheet from
# one computer to the next (and the caption font has to be able to draw every letter).
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass
class Calendar(Layout):
    """A wall calendar: a photo above a month grid, one page per month.

    Month n uses photo n (the photos repeat if there are fewer than months). English month and day names (fixed, whatever the system language).
    """
    year: int = field(default_factory=lambda: datetime.date.today().year)
    month: int = 1             # the first month (1 = January)
    months: int = 12           # how many pages
    week_start: str = "monday"   # monday | sunday
    photo_share: float = 0.55  # the photo's share of the page height
    gutter: float = 8.0

    def __post_init__(self):
        if not 1 <= self.month <= 12 or not 1 <= self.months <= 24 or not 1 <= self.year <= 9999:
            raise ValueError("year 1-9999, month 1-12 and months 1-24 expected")
        if self.week_start not in ("monday", "sunday") or not 0.2 <= self.photo_share <= 0.8 or self.gutter < 0:
            raise ValueError("week_start monday|sunday, photo_share between 0.2 and 0.8, gutter >= 0")

    @property
    def pages(self) -> int:
        return self.months

    def month_of(self, page: int) -> tuple[int, int]:
        """(year, month) shown on a page."""
        m = self.month - 1 + page
        return self.year + m // 12, m % 12 + 1

    def _first(self) -> int:
        return 0 if self.week_start == "monday" else 6   # calendar module: Monday = 0, Sunday = 6

    def _geometry(self, page: int):
        p, g = self.page, self.gutter
        photo = Rect(p.left, p.top, p.imageable_w, p.imageable_h * self.photo_share)
        title = Rect(p.left, photo.y + photo.h + g, p.imageable_w, 30.0)
        head = Rect(p.left, title.y + title.h, p.imageable_w, 16.0)
        y, mo = self.month_of(page)
        weeks = calendar.Calendar(self._first()).monthdayscalendar(y, mo)
        top = head.y + head.h
        ch = max(8.0, (p.top + p.imageable_h - top) / len(weeks))
        cw = p.imageable_w / 7
        return photo, title, head, weeks, top, cw, ch

    def slots(self, page: int):
        if not self.images or page >= self.months:
            return []
        return [(self.images[page % len(self.images)], self._geometry(page)[0])]

    def shapes(self, page: int):
        if page >= self.months:
            return []
        _, _, _, weeks, top, cw, ch = self._geometry(page)
        left = self.page.left
        return [("frame", Rect(left + c * cw, top + r * ch, cw, ch), (205, 205, 205))
                for r in range(len(weeks)) for c in range(7)]

    def captions(self, page: int):
        if page >= self.months:
            return []
        y, mo = self.month_of(page)
        _, title, head, weeks, top, cw, ch = self._geometry(page)
        left, first = self.page.left, self._first()
        out = [(f"{MONTHS[mo - 1]} {y}", title, (40, 40, 40))]
        weekend = lambda col: (first + col) % 7 in (5, 6)
        for c in range(7):
            out.append((WEEKDAYS[(first + c) % 7], Rect(left + c * cw, head.y, cw, head.h),
                        (190, 45, 45) if weekend(c) else (90, 90, 90)))
        font = min(14.0, max(7.0, ch * 0.35))
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                if day:
                    cell = Rect(left + c * cw, top + r * ch, cw, ch)
                    out.append((str(day), Rect(cell.x, cell.y + (ch - font * 1.8) / 2, cw, font * 1.8),
                                (190, 45, 45) if weekend(c) else (50, 50, 50)))
        return out
