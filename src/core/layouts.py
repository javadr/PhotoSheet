"""Layout geometry: which slot each image gets, and how many pages. No rendering, no Qt."""
from __future__ import annotations

import calendar
import datetime
import math
import os
import random
from dataclasses import dataclass, field, replace
from functools import lru_cache

from .geometry import PageExtent, Rect, bbox, ceil_div


@dataclass
class ImageItem:
    path: str
    crop: bool = False
    rotation: int = -1          # Rotation enum value
    flip_h: bool = False
    flip_v: bool = False
    angle: float = 0.0          # free rotation in degrees, clockwise; corners turn transparent
    hpan: float = 0.5
    vpan: float = 0.5
    scale: float = 100.0        # Single layout only: percent of native size
    profile: str | None = None  # ICC file assigned to this image (else embedded, else sRGB)
    intent: int = 0             # ImageCms.Intent value
    mask: str | None = None     # border mask image; black areas hide the photo
    effects: list = field(default_factory=list)  # [(name, {params}), ...] in order
    box: tuple | None = None    # Collage only: (x, y, w, h) in points; None = not placed yet

    def turn(self, slot_w: float, slot_h: float) -> int:
        """Rotation enum for fitting. Auto is decided on the photo before the hand rotation, which then adds to it."""
        if self.crop and self.rotation == -1:
            return 0     # a cropped photo fills its slot upright
        if self.angle and self.rotation == -1:
            return 90 if (aspect(replace(self, angle=0)) > 1) != (slot_w > slot_h) else 0
        return self.rotation


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
    feather: float = field(default=0.0, kw_only=True)   # points of soft fade at each photo's edge, so overlaps blend

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
    auto_rotate = False  # False: photos fitted whole stay upright instead of turning to match their slot
    caption_color = (70, 70, 70)

    def shapes(self, page: int) -> list[tuple[str, Rect, tuple]]:
        """Decoration drawn under the photos: (kind, rect, rgb). Kinds: card (filled, outlined), rect, round,
        ellipse (filled) and frame (outline only)."""
        return []

    def outlines(self, page: int) -> list[list[tuple[float, float]]] | None:
        """Polygons (in points) the photos are cut to, one per slot in slot order. None = plain rectangles."""
        return None


@dataclass
class NUp(Layout):
    rows: int = 3
    cols: int = 3
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

    Pinwheel: each wedge slides under the next one and lies over the previous one. Painted in order, the
    last seam would invert, so wedge 0 is painted once more, trimmed to its own sector (see layers()).
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
                poly = wedge_polygon(cx, cy, r, a0, a1 + self.overlap, self.inner_radius)
                out.append((self.images[i], poly, bbox(poly)))
        return out

    def layers(self) -> list[tuple[int, list[tuple[float, float]]]]:
        """(wedge index, polygon to paint) bottom to top."""
        from .geometry import segment_angles, wedge_polygon
        w = self.wedges()
        out = [(i, poly) for i, (_, poly, _) in enumerate(w)]
        if len(w) > 2:   # shortcut: with 2 wedges both seams are the same pair, so one must stay inverted
            p = self.page
            a0, a1 = segment_angles(self.segments, self.angle_offset)[0]
            out.append((0, wedge_polygon(p.left + p.imageable_w / 2, p.top + p.imageable_h / 2,
                                         min(p.imageable_w, p.imageable_h) / 2, a0, a1, self.inner_radius)))
        return out

    def slots(self, page: int):
        return [(img, box) for img, _, box in self.wedges()]


MM = 72 / 25.4  # points per millimetre
# Standard pin-back button faces: (label, face mm, typical white margin mm). Faces are the usual trade sizes;
# the margin is a typical cut-circle allowance (5 mm up to 1.75", ~0.375" above), not a supplier's exact figure.
BADGE_SIZES = [(f'{mm} mm ({inch}")', mm, 5.0 if mm < 50 else 9.5) for inch, mm in
               [("1", 25), ("1.25", 32), ("1.5", 38), ("1.75", 44), ("2", 51), ("2.25", 58), ("2.5", 64), ("3", 76), ("3.5", 89), ("6", 152)]]


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
        t = math.radians(item.angle)
        c, s = abs(math.cos(t)), abs(math.sin(t))
        return (w * c + h * s) / (w * s + h * c)
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
            if r:   # r bounds the turned photo; the box is the photo's own shape, centred in it
                t = math.radians(img.angle)
                c, sn = abs(math.cos(t)), abs(math.sin(t))
                a0 = aspect(replace(img, angle=0))
                w = r.w / (c + sn / a0)
                h = w / a0
                img.box = (r.x + (r.w - w) / 2, r.y + (r.h - h) / 2, w, h)

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

    def clamp(self, box: tuple, angle: float = 0.0) -> tuple:
        """Keep the box, as turned by `angle`, inside the imageable area, shrinking it (same shape) if it is bigger."""
        p = self.page
        x, y, w, h = box
        t = math.radians(angle)
        c, sn = abs(math.cos(t)), abs(math.sin(t))
        bw, bh = w * c + h * sn, w * sn + h * c   # bounding box of the turned box, same centre
        k = min(1.0, p.imageable_w / bw, p.imageable_h / bh)
        cx = min(max(x + w / 2, p.left + bw * k / 2), p.left + p.imageable_w - bw * k / 2)
        cy = min(max(y + h / 2, p.top + bh * k / 2), p.top + p.imageable_h - bh * k / 2)
        return (cx - w * k / 2, cy - h * k / 2, w * k, h * k)

    def slots(self, page: int):
        self._ensure()
        return [(img, Rect(*self.clamp(img.box, img.angle))) for img in self.images]

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

    The photo is cropped to the circle. Standard sizes are listed in BADGE_SIZES; 25 mm (1") is the default.
    """
    diameter: float = 25 * MM
    gutter: float = 2.0
    staggered: bool = True     # offset every other row so the circles nest (fits about 15% more)
    bleed: float = 0.0         # pin-back margin: white paper around the badge face to fold behind it; the cut circle sits at its edge
    fill_margin: bool = True   # run the photo out to the cut circle instead of leaving the margin white (trims the photo's edges)
    shape = "ellipse"

    def __post_init__(self):
        if self.diameter <= 0 or self.gutter < 0 or self.bleed < 0:
            raise ValueError("diameter must be > 0, gutter and bleed >= 0")

    def _positions(self) -> list[tuple[float, float]]:
        p, d, g = self.page, self.diameter + 2 * self.bleed, self.gutter
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
        c = self.diameter + 2 * self.bleed
        m, d = (0.0, c) if self.fill_margin else (self.bleed, self.diameter)
        return [(img, Rect(x + m, y + m, d, d))
                for img, (x, y) in zip(self.images[start:start + len(pos)], pos)]

    def cut_guides(self, page: int):
        if not self.marks:
            return []
        out, c = [], self.diameter + 2 * self.bleed
        for _, f in self.slots(page):
            cx, cy = f.x + f.w / 2, f.y + f.h / 2
            for r in {c / 2, self.diameter / 2} if self.fill_margin else {c / 2}:   # cut line (+ the badge face when the photo runs past it)
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


def _clip_x(poly: list, x: float, keep_right: bool) -> list:
    """A convex polygon cut at a vertical line, keeping the side to the right (or left) of it."""
    inside = (lambda q: q[0] >= x) if keep_right else (lambda q: q[0] <= x)
    out = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        if inside(a):
            out.append(a)
        if inside(a) != inside(b):
            out.append((x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])))
    return out


@dataclass
class Honeycomb(Layout):
    """Hexagon-shaped photos nested like a honeycomb. `size` is the width across the flat sides."""
    size: float = 40 * MM
    gutter: float = 6.0
    fill: bool = False   # widen the cells to span the page edge to edge and stretch them to reach the top and bottom
    half_cells: bool = False   # also show the partial cells that poke in from the sides, cut off at the page edge
    shape = "hexagon"

    def __post_init__(self):
        if self.size <= 0 or self.gutter < 0:
            raise ValueError("size must be > 0 and gutter >= 0")

    @property
    def cell_h(self) -> float:
        return self.size * 2 / math.sqrt(3)   # point to point

    def _grid(self) -> tuple[list[tuple[float, float]], float, float]:
        """Cell origins and the cell's (width, height); the height is stretched when `fill` is on."""
        p, g, w, h = self.page, self.gutter, self.size, self.cell_h
        pitch = w + g
        if p.imageable_w < w or p.imageable_h < h:
            return [], w, h
        if self.fill:
            w = (p.imageable_w - g * (int((p.imageable_w + g) // pitch) - 1)) / int((p.imageable_w + g) // pitch)
            pitch, h = w + g, w * 2 / math.sqrt(3)
        row_pitch = pitch * math.sqrt(3) / 2
        rows = int((p.imageable_h - h) // row_pitch + 1e-9) + 1
        even, odd = int((p.imageable_w + g) / pitch + 1e-9), int((p.imageable_w + g - pitch / 2) / pitch + 1e-9)
        counts = [even if r % 2 == 0 else odd for r in range(rows)]
        offs = [pitch / 2 if r % 2 else 0.0 for r in range(rows)]
        block_w = max((o + n * pitch - g for o, n in zip(offs, counts) if n), default=0.0)
        x0 = p.left + (p.imageable_w - block_w) / 2
        if self.fill:
            k = p.imageable_h / ((rows - 1) * row_pitch + h)
            h, row_pitch = h * k, row_pitch * k
        y0 = p.top + (p.imageable_h - ((rows - 1) * row_pitch + h)) / 2
        return [(x0 + offs[r] + c * pitch, y0 + r * row_pitch) for r in range(rows) for c in range(counts[r])], w, h

    def _positions(self) -> list[tuple[float, float]]:
        return self._grid()[0]

    @property
    def per_page(self) -> int:
        return len(self._cells())

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page)) if self.per_page else 1

    def _cells(self) -> list[tuple[Rect, list | None]]:
        """Every cell's rectangle and, with half_cells, its outline (a hexagon clipped to the page edges)."""
        pos, w, h = self._grid()
        if not self.half_cells or not pos:
            return [(Rect(x, y, w, h), None) for x, y in pos]
        p, pitch = self.page, w + self.gutter
        left, right = p.left, p.left + p.imageable_w
        out = []
        for y in sorted({y for _, y in pos}):
            first = min(x for x, yy in pos if yy == y)
            k = -math.ceil((first - left) / pitch) - 1
            while first + k * pitch < right:
                x = first + k * pitch
                k += 1
                hexa = [(x + w / 2, y), (x + w, y + h / 4), (x + w, y + 3 * h / 4), (x + w / 2, y + h),
                        (x, y + 3 * h / 4), (x, y + h / 4)]
                poly = _clip_x(_clip_x(hexa, left, True), right, False)
                if poly and max(q[0] for q in poly) - min(q[0] for q in poly) >= 0.2 * w:
                    out.append((bbox(poly), poly))
        return out

    def slots(self, page: int):
        cells = self._cells()
        start = page * len(cells)
        return [(img, r) for img, (r, _) in zip(self.images[start:start + len(cells)], cells)]

    def outlines(self, page: int):
        cells = self._cells()
        return [poly for _, poly in cells] if self.half_cells and cells else None

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


SHAPE_NAMES = ("heart", "star", "diamond", "triangle", "rounded", "circle")


@dataclass
class Masonry(Layout):
    """Columns of equal width with photos of their own height, each placed in the shortest column.

    Nothing is cropped. When the next photo does not fit the page, a new page starts.
    """
    columns: int = 3
    gutter: float = 6.0

    def __post_init__(self):
        if self.columns < 1 or self.gutter < 0:
            raise ValueError("columns must be >= 1 and gutter >= 0")

    def _paged(self) -> list[list[tuple[ImageItem, Rect]]]:
        p, g, n = self.page, self.gutter, self.columns
        cw, ih = (p.imageable_w - g * (n - 1)) / n, p.imageable_h
        pages: list[list] = [[]]
        heights = [0.0] * n
        for img in self.images:
            h = min(cw / aspect(img), ih)
            col = min(range(n), key=lambda c: heights[c])
            if heights[col] + h > ih + 1e-9 and pages[-1]:
                pages.append([])
                heights = [0.0] * n
                col = 0
            pages[-1].append((img, Rect(p.left + col * (cw + g), p.top + heights[col], cw, h)))
            heights[col] += h + g
        return pages

    @property
    def pages(self) -> int:
        return len(self._paged())

    def slots(self, page: int):
        paged = self._paged()
        return paged[page] if page < len(paged) else []


@dataclass
class Shapes(Layout):
    """A grid of photos cropped to a shape: heart, star, diamond, triangle, rounded square or circle.

    Cells are squares, as large as the grid allows, centred in their slot of the grid.
    """
    rows: int = 3
    cols: int = 2
    gutter: float = 8.0
    kind: str = "heart"

    def __post_init__(self):
        if self.rows < 1 or self.cols < 1 or self.gutter < 0:
            raise ValueError("rows and cols must be >= 1 and gutter >= 0")
        if self.kind not in SHAPE_NAMES:
            raise ValueError(f"unknown shape {self.kind!r}")

    @property
    def shape(self) -> str:
        return "ellipse" if self.kind == "circle" else self.kind

    @property
    def per_page(self) -> int:
        return self.rows * self.cols

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def slots(self, page: int):
        p, g = self.page, self.gutter
        cw, ch = (p.imageable_w - g * (self.cols - 1)) / self.cols, (p.imageable_h - g * (self.rows - 1)) / self.rows
        side = max(1.0, min(cw, ch))
        x0 = p.left + (p.imageable_w - (self.cols * side + (self.cols - 1) * g)) / 2
        y0 = p.top + (p.imageable_h - (self.rows * side + (self.rows - 1) * g)) / 2
        start, out = page * self.per_page, []
        for i, img in enumerate(self.images[start:start + self.per_page]):
            r, c = divmod(i, self.cols)
            out.append((img, Rect(x0 + c * (side + g), y0 + r * (side + g), side, side)))
        return out


def _circle_segments(cx: float, cy: float, r: float, n: int = 48) -> list[tuple[float, float, float, float]]:
    pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]


@dataclass
class Ring(Layout):
    """A wreath: `photos` round photos around a circle, and optionally a bigger one in the middle.

    The ring photos are as big as they can be while still fitting the page, with the gutter between neighbours.
    The centre photo is only added when it is at least as big as the ring photos (6 or more around the ring).
    Each page takes the centre photo first (when there is one), then the ring photos in clockwise order from the top.
    """
    photos: int = 8
    center: bool = True
    gutter: float = 6.0
    shape = "ellipse"

    def __post_init__(self):
        if not 3 <= self.photos <= 36 or self.gutter < 0:
            raise ValueError("photos must be between 3 and 36 and gutter >= 0")

    def _circles(self) -> list[Rect]:
        p, g, n = self.page, self.gutter, self.photos
        rmax = min(p.imageable_w, p.imageable_h) / 2
        s = math.sin(math.pi / n)
        d = max(1.0, (2 * rmax * s - g) / (1 + s))
        radius = rmax - d / 2
        cx, cy = p.left + p.imageable_w / 2, p.top + p.imageable_h / 2
        ring = [Rect(cx + radius * math.sin(2 * math.pi * i / n) - d / 2,
                     cy - radius * math.cos(2 * math.pi * i / n) - d / 2, d, d) for i in range(n)]
        dc = 2 * (radius - d / 2 - g)
        mid = [Rect(cx - dc / 2, cy - dc / 2, dc, dc)] if self.center and dc >= d else []   # never smaller than the ring
        return mid + ring

    @property
    def center_left_out(self) -> bool:
        """The centre photo is switched on but would be smaller than the ring photos (fewer than 6 around it)."""
        return self.center and len(self._circles()) == self.photos

    @property
    def per_page(self) -> int:
        return len(self._circles())

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def slots(self, page: int):
        rects = self._circles()
        start = page * len(rects)
        return list(zip(self.images[start:start + len(rects)], rects))

    def cut_guides(self, page: int):
        if not self.marks:
            return []
        return [seg for _, r in self.slots(page) for seg in _circle_segments(r.x + r.w / 2, r.y + r.h / 2, r.w / 2)]


@dataclass
class Scatter(Layout):
    """A casual pile of prints: each photo on a white card, tilted a little, with a soft shadow.

    Positions and tilts are random but repeatable: the same `seed` always gives the same pile.
    Later photos lie on top. Every photo keeps its own shape (nothing is cropped).
    """
    photos: int = 6
    size: float = 0.45     # a photo's longest side, as a share of the page's shorter side
    tilt: float = 12.0     # largest tilt either way, in degrees
    border: float = 8.0
    seed: int = 1
    even: bool = False     # one photo per area of the page (a jittered grid), so the pile fills the whole page instead of clumping
    spread: float = 1.0    # how much of the page the photos roam over: 0 piles them in the middle, 1 uses all of it

    def __post_init__(self):
        if self.photos < 1 or not 0.15 <= self.size <= 0.9 or not 0 <= self.tilt <= 45 or self.border < 0 or not 0 <= self.spread <= 1:
            raise ValueError("photos >= 1, size between 0.15 and 0.9, tilt between 0 and 45, border >= 0, spread between 0 and 1")

    @property
    def per_page(self) -> int:
        return self.photos

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.photos))

    def pile(self, page: int) -> list[tuple[ImageItem, Rect, Rect, float]]:
        """(photo, its rectangle, its card, tilt in degrees counter-clockwise), bottom of the pile first."""
        p, b = self.page, self.border
        iw, ih = p.imageable_w, p.imageable_h
        rng = random.Random(self.seed * 1009 + page)
        start, out = page * self.photos, []
        chunk = self.images[start:start + self.photos]
        cells = []
        if self.even and chunk:
            cols = max(1, min(len(chunk), round(math.sqrt(len(chunk) * iw / ih))))
            rows = ceil_div(len(chunk), cols)
            cells = [(c, r) for r in range(rows) for c in range(cols)]
            random.Random(self.seed * 1009 + page + 7).shuffle(cells)
            cols_rows = (cols, rows)
        for k, item in enumerate(chunk):
            ang, rx, ry = rng.uniform(-self.tilt, self.tilt), rng.random(), rng.random()   # the same 3 draws for every photo
            if cells:   # the photo's own random draw only moves it inside its cell
                rx, ry = (cells[k][0] + rx) / cols_rows[0], (cells[k][1] + ry) / cols_rows[1]
            rx, ry = .5 + (rx - .5) * self.spread, .5 + (ry - .5) * self.spread          # pull towards the centre
            a, longest = aspect(item), self.size * min(iw, ih)
            pw, ph = (longest, longest / a) if a >= 1 else (longest * a, longest)
            cw, ch = pw + 2 * b, ph + 3 * b                     # a thicker bottom edge, like an instant print
            t = math.radians(abs(ang))
            hw, hh = (cw * math.cos(t) + ch * math.sin(t)) / 2, (cw * math.sin(t) + ch * math.cos(t)) / 2
            cx = p.left + hw + rx * (iw - 2 * hw) if iw > 2 * hw else p.left + iw / 2
            cy = p.top + hh + ry * (ih - 2 * hh) if ih > 2 * hh else p.top + ih / 2
            card = Rect(cx - cw / 2, cy - ch / 2, cw, ch)
            out.append((item, Rect(card.x + b, card.y + b, pw, ph), card, ang))
        return out

    def slots(self, page: int):
        return [(item, photo) for item, photo, _, _ in self.pile(page)]


@lru_cache(maxsize=2048)
def _taken(path: str, mtime: int) -> datetime.datetime | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            ex = im.getexif()
            raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)   # DateTimeOriginal, else DateTime
        return datetime.datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S") if raw else None
    except Exception:   # unreadable file, no EXIF, odd date text: simply "no date"
        return None


def taken(item: ImageItem) -> datetime.datetime | None:
    """When the photo was taken, from its EXIF data; None if it has none."""
    try:
        return _taken(item.path, os.stat(item.path).st_mtime_ns)
    except OSError:
        return None


def date_text(item: ImageItem) -> str:
    """'12 Mar 2024' from the photo's EXIF date, else the file name without its extension."""
    d = taken(item)
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}" if d else os.path.splitext(os.path.basename(item.path))[0]


def date_sort_key(item: ImageItem) -> datetime.datetime:
    """For sorting: the EXIF date, else the file's modification time, else the very end."""
    d = taken(item)
    if d:
        return d
    try:
        return datetime.datetime.fromtimestamp(os.stat(item.path).st_mtime)
    except OSError:
        return datetime.datetime.max


@dataclass
class Slant(Layout):
    """Diagonal panels: tall parallelograms side by side, each photo cut to its slanted shape.

    A positive slant leans the panels to the right, a negative one to the left. Panels fill the page height and
    keep the gutter between them. Each page takes `panels` photos in order.
    """
    panels: int = 5
    slant: float = 12.0     # degrees from vertical, -40 to 40
    gutter: float = 6.0

    def __post_init__(self):
        if not 2 <= self.panels <= 12 or not -40 <= self.slant <= 40 or self.gutter < 0:
            raise ValueError("panels must be 2-12, slant -40 to 40 and gutter >= 0")

    @property
    def per_page(self) -> int:
        return self.panels

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.panels))

    def _dx(self) -> float:
        """How far the panels lean across the page: at most 60% of the page width, so the panels never shrink to slivers."""
        p = self.page
        return min(p.imageable_h * math.tan(math.radians(abs(self.slant))), 0.6 * p.imageable_w)

    @property
    def effective_slant(self) -> float:
        """The lean actually used, in degrees. Less than |slant| when the page is too narrow for it."""
        return math.copysign(math.degrees(math.atan(self._dx() / self.page.imageable_h)), self.slant)

    @property
    def slant_limited(self) -> bool:
        return abs(self.effective_slant) < abs(self.slant) - 1e-6

    def _polys(self) -> list[list[tuple[float, float]]]:
        p, g, n = self.page, self.gutter, self.panels
        dx = self._dx()
        w = max(1.0, (p.imageable_w - dx - (n - 1) * g) / n)
        top, bottom = (dx, 0.0) if self.slant >= 0 else (0.0, dx)   # how far each end is shifted right
        out = []
        for i in range(n):
            x = p.left + i * (w + g)
            out.append([(x + top, p.top), (x + top + w, p.top), (x + bottom + w, p.top + p.imageable_h),
                        (x + bottom, p.top + p.imageable_h)])
        return out

    def _chunk(self, page: int):
        return self.images[page * self.panels:(page + 1) * self.panels]

    def outlines(self, page: int):
        return self._polys()[:len(self._chunk(page))]

    def slots(self, page: int):
        out = []
        for item, poly in zip(self._chunk(page), self._polys()):
            xs, ys = [x for x, _ in poly], [y for _, y in poly]
            out.append((item, Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))))
        return out


@dataclass
class Fan(Layout):
    """Cards fanned out like a hand of playing cards, pivoting at the bottom, the last on top.

    Every card has the same shape and its photo is cropped to it. The whole fan is scaled to fill the page.
    """
    photos: int = 5
    spread: float = 60.0    # angle from the first card to the last, in degrees
    border: float = 0.06    # white border, as a share of the card's width

    def __post_init__(self):
        if not 1 <= self.photos <= 12 or not 10 <= self.spread <= 120 or not 0.02 <= self.border <= 0.15:
            raise ValueError("photos 1-12, spread 10-120 degrees, border between 2% and 15%")

    @property
    def per_page(self) -> int:
        return self.photos

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.photos))

    def pile(self, page: int) -> list[tuple[ImageItem, Rect, Rect, float]]:
        """(photo, its rectangle, its card, tilt in degrees counter-clockwise), bottom of the pile first."""
        chunk = self.images[page * self.photos:(page + 1) * self.photos]
        n = len(chunk)
        if not n:
            return []
        p = self.page
        angles = [0.0] if n == 1 else [-self.spread / 2 + i * self.spread / (n - 1) for i in range(n)]   # + = clockwise
        cw, ch, radius = 0.72, 1.0, 0.45
        bw = self.border * cw
        centres, corners = [], []
        for a in angles:
            t = math.radians(a)
            cx, cy = radius * math.sin(t), -radius * math.cos(t)
            centres.append((cx, cy))
            for dx, dy in ((-cw / 2, -ch / 2), (cw / 2, -ch / 2), (cw / 2, ch / 2), (-cw / 2, ch / 2)):
                corners.append((cx + dx * math.cos(t) - dy * math.sin(t), cy + dx * math.sin(t) + dy * math.cos(t)))
        x0, x1 = min(x for x, _ in corners), max(x for x, _ in corners)
        y0, y1 = min(y for _, y in corners), max(y for _, y in corners)
        sc = min(p.imageable_w / (x1 - x0), p.imageable_h / (y1 - y0))
        ox = p.left + p.imageable_w / 2 - (x0 + x1) / 2 * sc
        oy = p.top + p.imageable_h / 2 - (y0 + y1) / 2 * sc
        out = []
        for item, (cx, cy), a in zip(chunk, centres, angles):
            card = Rect(ox + cx * sc - cw * sc / 2, oy + cy * sc - ch * sc / 2, cw * sc, ch * sc)
            photo = Rect(card.x + bw * sc, card.y + bw * sc, (cw - 2 * bw) * sc, (ch - 3 * bw) * sc)
            out.append((item, photo, card, -a))
        return out

    def slots(self, page: int):
        return [(item, photo) for item, photo, _, _ in self.pile(page)]


@dataclass
class Brick(Layout):
    """Brickwork that fills the page edge to edge.

    Even rows have `columns` whole tiles. Every other row is shifted by half a tile: it starts and ends with a
    half-width tile, with `columns - 1` whole tiles between, so it reaches both edges of the page too. A photo in a
    half tile is cropped to that half. Photos fill the tiles row by row.
    """
    columns: int = 5
    rows: int = 5
    gutter: float = 6.0
    auto_rotate = False        # a narrow half tile would turn every landscape photo on its side
    half_cells: bool = True    # the half tiles at both ends of the shifted rows; off leaves that space white

    def __post_init__(self):
        if self.columns < 2 or self.rows < 1 or self.gutter < 0:
            raise ValueError("columns must be >= 2, rows >= 1 and gutter >= 0")

    @property
    def tile_w(self) -> float:
        return (self.page.imageable_w - self.gutter * (self.columns - 1)) / self.columns

    def _rects(self) -> list[Rect]:
        p, g, c, r = self.page, self.gutter, self.columns, self.rows
        tw, th = (p.imageable_w - g * (c - 1)) / c, (p.imageable_h - g * (r - 1)) / r
        half = max(1.0, (tw - g) / 2)           # keeps the gutter the same width beside a half tile
        out = []
        for row in range(r):
            y = p.top + row * (th + g)
            if row % 2 == 0:
                out += [Rect(p.left + col * (tw + g), y, tw, th) for col in range(c)]
            else:
                shift = (tw + g) / 2
                if self.half_cells:
                    out.append(Rect(p.left, y, half, th))
                out += [Rect(p.left + shift + col * (tw + g), y, tw, th) for col in range(c - 1)]
                if self.half_cells:
                    out.append(Rect(p.left + p.imageable_w - half, y, half, th))
        return out

    @property
    def per_page(self) -> int:
        return len(self._rects())

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def slots(self, page: int):
        rects = self._rects()
        return list(zip(self.images[page * len(rects):(page + 1) * len(rects)], rects))


@dataclass
class Timeline(Layout):
    """A line with photos alternating on either side, each with its date: a family or trip timeline.

    Runs top to bottom on a portrait page and left to right on a landscape page. Dates come from the photos'
    EXIF data (the file name is shown if a photo has none). Photos appear in list order: use Edit > Sort Photos
    by Date to put them in time order.
    """
    auto_rotate = False   # a photo is never turned on its side to suit its slot
    photos: int = 6
    dates: bool = True
    gutter: float = 8.0
    caption_pt: float = 9.0

    def __post_init__(self):
        if not 1 <= self.photos <= 12 or self.gutter < 0:
            raise ValueError("photos must be 1-12 and gutter >= 0")

    @property
    def per_page(self) -> int:
        return self.photos

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.photos))

    def _cells(self, page: int):
        """[(item, cell rect, along-the-line position of its dot)] plus helpers, in page coordinates."""
        p = self.page
        chunk = self.images[page * self.photos:(page + 1) * self.photos]
        m = len(chunk)
        horiz = p.imageable_w > p.imageable_h
        length, width = (p.imageable_w, p.imageable_h) if horiz else (p.imageable_h, p.imageable_w)
        g, cross = self.gutter, width / 2 - self.gutter - 6
        cu = min(length, 1.4 * cross) if m < 2 else min((2 * length - (m - 1) * g) / (m + 1), 1.4 * cross)
        step = 0.0 if m < 2 else (length - cu) / (m - 1)

        def box(u0, v0, du, dv):
            return Rect(p.left + u0, p.top + v0, du, dv) if horiz else Rect(p.left + v0, p.top + u0, dv, du)

        out = []
        for i, item in enumerate(chunk):
            uc = length / 2 if m < 2 else cu / 2 + i * step
            v0 = width / 2 - 6 - g - cross if i % 2 == 0 else width / 2 + 6 + g
            out.append((item, box(uc - cu / 2, v0, cu, cross), uc))
        return out, box, width, length

    @property
    def _band(self) -> float:
        return self.caption_pt * 1.8 if self.dates else 0.0

    def slots(self, page: int):
        return [(item, Rect(c.x, c.y, c.w, max(1.0, c.h - self._band))) for item, c, _ in self._cells(page)[0]]

    def captions(self, page: int):
        if not self.dates:
            return []
        return [(date_text(item), Rect(c.x, c.y + c.h - self._band, c.w, self._band)) for item, c, _ in self._cells(page)[0]]

    def shapes(self, page: int):
        cells, box, width, length = self._cells(page)
        if not cells:
            return []
        mid, g = width / 2, self.gutter
        us = [u for _, _, u in cells]
        line, dot = (150, 155, 170), (60, 100, 200)
        out = [("rect", box(min(us) - 14, mid - 1, max(us) - min(us) + 28, 2), line)]
        for i, (_, _, u) in enumerate(cells):
            v0 = mid - 6 - g if i % 2 == 0 else mid
            out.append(("rect", box(u - 0.5, v0, 1, g + 6), line))                   # the tick from the line to its photo
        out += [("ellipse", box(u - 5, mid - 5, 10, 10), dot) for u in us]
        return out


@dataclass
class Checker(Layout):
    """A chessboard: photos on every other square, the rest filled with a pale tint. `first_empty` swaps them."""
    columns: int = 4
    rows: int = 4
    gutter: float = 0.0
    first_empty: bool = False   # start the pattern on the second square: the first cell is a tint

    def __post_init__(self):
        if self.columns < 1 or self.rows < 1 or self.gutter < 0:
            raise ValueError("columns and rows must be >= 1 and gutter >= 0")

    def _cells(self, parity: int) -> list[Rect]:
        p, g, c, r = self.page, self.gutter, self.columns, self.rows
        tw, th = (p.imageable_w - g * (c - 1)) / c, (p.imageable_h - g * (r - 1)) / r
        return [Rect(p.left + col * (tw + g), p.top + row * (th + g), tw, th)
                for row in range(r) for col in range(c) if (row + col) % 2 == parity ^ self.first_empty]

    @property
    def per_page(self) -> int:
        return len(self._cells(0))

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def slots(self, page: int):
        rects = self._cells(0)
        return list(zip(self.images[page * len(rects):(page + 1) * len(rects)], rects))

    def shapes(self, page: int):
        return [("rect", r, (232, 232, 236)) for r in self._cells(1)]


@dataclass
class Stairs(Layout):
    """Photos stepping down the page from top left to bottom right (or bottom left to top right if `rising`), each overlapping the one before it by 35%."""
    photos: int = 5
    rising: bool = False   # climb from bottom left to top right instead

    def __post_init__(self):
        if not 1 <= self.photos <= 12:
            raise ValueError("photos must be 1-12")

    @property
    def per_page(self) -> int:
        return self.photos

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.photos))

    def slots(self, page: int):
        p, n = self.page, self.photos
        k = 1 + 0.65 * (n - 1)
        w, h = p.imageable_w / k, p.imageable_h / k
        return [(item, Rect(p.left + i * 0.65 * w, p.top + ((n - 1 - i) if self.rising else i) * 0.65 * h, w, h))
                for i, item in enumerate(self.images[page * n:(page + 1) * n])]


@dataclass
class Pyramid(Layout):
    """Rows of 1, 2, 3 ... photos stacked into a triangle, each row centred. A Diamond widens, then narrows again."""
    rows: int = 4
    gutter: float = 6.0

    def __post_init__(self):
        if self.rows < 1 or self.gutter < 0:
            raise ValueError("rows must be >= 1 and gutter >= 0")

    def _counts(self) -> list[int]:
        return list(range(1, self.rows + 1))

    def _rects(self) -> list[Rect]:
        p, g, counts = self.page, self.gutter, self._counts()
        n = max(counts)
        tw, th = (p.imageable_w - g * (n - 1)) / n, (p.imageable_h - g * (self.rows - 1)) / self.rows
        return [Rect(p.left + (p.imageable_w - c * tw - (c - 1) * g) / 2 + k * (tw + g), p.top + row * (th + g), tw, th)
                for row, c in enumerate(counts) for k in range(c)]

    @property
    def per_page(self) -> int:
        return sum(self._counts())

    @property
    def pages(self) -> int:
        return max(1, ceil_div(len(self.images), self.per_page))

    def slots(self, page: int):
        rects, n = self._rects(), self.per_page
        return list(zip(self.images[page * n:(page + 1) * n], rects))


@dataclass
class Diamond(Pyramid):
    """Rows of 1, 2 ... up to the middle row, then back down to 1: a diamond of photos."""
    def _counts(self) -> list[int]:
        return [min(i, self.rows - 1 - i) + 1 for i in range(self.rows)]
