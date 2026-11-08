"""Turn a layout page into a PIL image at a given DPI. No Qt."""
from __future__ import annotations

import io

from dataclasses import replace

from PIL import Image, ImageCms, ImageDraw, ImageOps

from .effects import apply_effects
from .geometry import Rect, Rotation, fit
from .layouts import Carousel, ImageItem, Layout, Poster

_SRGB = None


def _srgb():
    global _SRGB
    _SRGB = _SRGB or ImageCms.createProfile("sRGB")
    return _SRGB


def load(item: ImageItem, printer_profile: str | None = None) -> Image.Image:
    """Open, orient, colour-manage to RGB, flip and apply effects."""
    im = Image.open(item.path)
    im = ImageOps.exif_transpose(im)
    embedded = im.info.get("icc_profile")
    src = (ImageCms.getOpenProfile(item.profile) if item.profile
           else ImageCms.ImageCmsProfile(io.BytesIO(embedded)) if embedded else _srgb())
    if im.mode in ("1", "L", "LA", "P", "PA", "I;16"):
        im = im.convert("RGBA" if "A" in im.mode or im.mode == "PA" else "RGB")
    elif im.mode not in ("RGB", "RGBA", "CMYK"):
        im = im.convert("RGB")
    alpha = im.getchannel("A") if im.mode == "RGBA" else None
    if alpha:
        im = im.convert("RGB")
    dst = ImageCms.getOpenProfile(printer_profile) if printer_profile else _srgb()
    if im.mode == "CMYK" and not (item.profile or embedded):
        im = im.convert("RGB")  # shortcut: no CMYK profile to go on, naive conversion
    else:
        im = ImageCms.profileToProfile(im, src, dst, renderingIntent=item.intent, outputMode="RGB")
    if item.flip_h:
        im = ImageOps.mirror(im)
    if item.flip_v:
        im = ImageOps.flip(im)
    im = apply_effects(im, item.effects)
    if alpha:
        im.putalpha(alpha)
    return im


def _place(canvas: Image.Image, item: ImageItem, im: Image.Image, slot: Rect, s: float) -> None:
    f = fit(im.width, im.height, slot.scaled(s), item.crop, Rotation(item.rotation), item.hpan, item.vpan)
    if f.rotation:
        im = im.rotate(-f.rotation, expand=True)  # PIL rotates counter-clockwise
    im = im.resize((max(1, f.width), max(1, f.height)), Image.LANCZOS)
    if item.crop:
        w, h = min(f.width, round(slot.w * s)), min(f.height, round(slot.h * s))
        im = im.crop((f.xoffset, f.yoffset, f.xoffset + w, f.yoffset + h))
    if item.mask:
        m = ImageOps.invert(Image.open(item.mask).convert("L").resize(im.size))
        im.putalpha(m)  # mask: white = hide, as the original inverted it
    canvas.paste(im, (f.xpos, f.ypos), im if im.mode == "RGBA" else None)


def render_page(layout: Layout, page: int, dpi: int = 150, printer_profile: str | None = None,
                background=(255, 255, 255)) -> Image.Image:
    s = dpi / 72
    p = layout.page
    canvas = Image.new("RGB", (round(p.width * s), round(p.height * s)), background)
    if not layout.images:
        return canvas
    if isinstance(layout, Poster):
        idx, _, win = layout.tile_window(page)
        item = layout.images[idx]
        poster = Image.new("RGB", (round(layout.poster_w * s), round(layout.poster_h * s)), background)
        _place(poster, item, load(item, printer_profile), Rect(0, 0, layout.poster_w, layout.poster_h), s)
        tile = poster.crop((round(win.x * s), round(win.y * s),
                            round((win.x + win.w) * s), round((win.y + win.h) * s)))
        canvas.paste(tile, (round(p.left * s), round(p.top * s)))
        return canvas
    if isinstance(layout, Carousel):
        wedges = layout.wedges()
        for i in [*range(0, len(wedges), 2), *range(1, len(wedges), 2)]:  # evens, then odds on top
            item, poly, box = wedges[i]
            tmp = Image.new("RGB", canvas.size, background)
            _place(tmp, replace(item, crop=True, rotation=0, mask=None), load(item, printer_profile), box, s)
            mask = Image.new("L", canvas.size, 0)
            ImageDraw.Draw(mask).polygon([(x * s, y * s) for x, y in poly], fill=255)
            canvas.paste(tmp, mask=mask)
        return canvas
    for item, slot in layout.slots(page):
        _place(canvas, item, load(item, printer_profile), slot, s)
    return canvas


def render_all(layout: Layout, **kw) -> list[Image.Image]:
    return [render_page(layout, i, **kw) for i in range(layout.pages)]
