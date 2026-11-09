"""Page and fitting maths. All units are points (1/72 inch). No Qt here."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum


class Rotation(IntEnum):
    AUTO = -1
    NONE = 0
    R90 = 90
    R180 = 180
    R270 = 270


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    def scaled(self, s: float) -> "Rect":
        return Rect(self.x * s, self.y * s, self.w * s, self.h * s)


@dataclass
class Fit:
    """Result of fitting a source into a target slot.

    width/height: source size after scaling (and rotation).
    xpos/ypos: placement of the visible part inside the slot.
    xoffset/yoffset: crop origin inside the scaled image (crop mode only).
    """
    width: int
    height: int
    xpos: int
    ypos: int
    xoffset: int
    yoffset: int
    rotation: int
    scale: float


def _oriented(src_w: float, src_h: float, target: Rect, rotation: Rotation) -> tuple[float, float, Rotation]:
    """Source size as drawn (after any rotation) and the rotation actually used."""
    if rotation == Rotation.AUTO:
        rotation = Rotation.R90 if (src_w > src_h) != (target.w > target.h) else Rotation.NONE
    if rotation in (Rotation.R90, Rotation.R270):
        src_w, src_h = src_h, src_w
    return src_w, src_h, rotation


def crop_overflow(src_w: float, src_h: float, target: Rect, rotation: int = Rotation.AUTO) -> tuple[float, float]:
    """How far (points, x and y) a cropped photo extends past its slot. 0 on an axis means nothing to pan."""
    w, h, _ = _oriented(src_w, src_h, target, Rotation(rotation))
    s = max(target.w / w, target.h / h)
    return max(0.0, w * s - target.w), max(0.0, h * s - target.h)


def fit(src_w: float, src_h: float, target: Rect, crop: bool = False,
        rotation: Rotation = Rotation.AUTO, hpan: float = 0.5, vpan: float = 0.5) -> Fit:
    """Fit a src_w x src_h image into target.

    crop=False: scale to fit inside, centred. crop=True: scale to cover, panned by
    hpan/vpan (0..1, 0.5 = centred). AUTO rotation turns the image 90 deg when its
    orientation differs from the slot's.
    shortcut: reimplemented from usage (original library source unavailable), so
    rounding may differ from PhotoPrint by a pixel; verify against real output.
    """
    src_w, src_h, rotation = _oriented(src_w, src_h, target, rotation)
    s = max(target.w / src_w, target.h / src_h) if crop else min(target.w / src_w, target.h / src_h)
    w, h = round(src_w * s), round(src_h * s)
    if crop:
        vis_w, vis_h = min(w, round(target.w)), min(h, round(target.h))
        return Fit(w, h, round(target.x), round(target.y),
                   round((w - vis_w) * hpan), round((h - vis_h) * vpan), int(rotation), s)
    return Fit(w, h, round(target.x + (target.w - w) / 2), round(target.y + (target.h - h) / 2),
               0, 0, int(rotation), s)


@dataclass
class PageExtent:
    width: float = 595.0   # A4 in points
    height: float = 842.0
    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0

    @property
    def imageable_w(self) -> float:
        return self.width - self.left - self.right

    @property
    def imageable_h(self) -> float:
        return self.height - self.top - self.bottom

    def set_margins(self, left: float, right: float, top: float, bottom: float) -> bool:
        """Reject margins that leave no imageable area (as the original did)."""
        if left + right >= self.width or top + bottom >= self.height:
            return False
        self.left, self.right, self.top, self.bottom = left, right, top, bottom
        return True


def segment_angles(segments: int, offset_deg: float = 0.0) -> list[tuple[float, float]]:
    """(start, end) angle of each carousel segment, degrees. Overlap/mask rendering is separate."""
    step = 360.0 / segments
    return [(offset_deg + i * step, offset_deg + (i + 1) * step) for i in range(segments)]


def ceil_div(a: float, b: float) -> int:
    return max(1, math.ceil(a / b - 1e-9))


def wedge_polygon(cx: float, cy: float, r: float, a0: float, a1: float,
                  inner: float = 0.0, steps: int = 32) -> list[tuple[float, float]]:
    """Polygon of a ring sector. Angles in degrees, 0 = up, clockwise."""
    def pt(rad, a):
        t = math.radians(a)
        return (cx + rad * math.sin(t), cy - rad * math.cos(t))
    arc = [a0 + (a1 - a0) * i / steps for i in range(steps + 1)]
    pts = [pt(r, a) for a in arc]
    pts += [pt(inner, a) for a in reversed(arc)] if inner > 0 else [(cx, cy)]
    return pts


def bbox(pts: list[tuple[float, float]]) -> Rect:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
