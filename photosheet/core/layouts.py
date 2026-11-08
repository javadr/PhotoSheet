"""Layout geometry: which slot each image gets, and how many pages. No rendering, no Qt."""
from __future__ import annotations

from dataclasses import dataclass, field

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
