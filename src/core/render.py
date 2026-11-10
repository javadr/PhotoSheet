"""Turn a layout page into a PIL image at a given DPI. No Qt."""
from __future__ import annotations

import io
import os
import threading
from collections import OrderedDict

from dataclasses import replace

from PIL import Image, ImageCms, ImageChops, ImageDraw, ImageFont, ImageOps

from .effects import apply_effects
from .geometry import Rect, Rotation, fit
from .layouts import Carousel, ImageItem, Layout, Poster, Triptych

_SRGB = None


def _srgb():
    global _SRGB
    _SRGB = _SRGB or ImageCms.createProfile("sRGB")
    return _SRGB


_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_PIXELS = 100_000_000  # budget across cached images, roughly 300 MB of RGB


PROXY_SIDE = 2400  # longest side of the cached preview copy


def load(item: ImageItem, printer_profile: str | None = None, proxy: bool = False) -> Image.Image:
    """Cached _load(). The returned image is shared: callers must not modify it in place.

    proxy=True returns a copy capped at PROXY_SIDE for fast previews.
    shortcut: a preview at more than ~PROXY_SIDE px per photo (huge zoom on a 1-up page)
    is slightly soft; raise PROXY_SIDE or render without fast=True if that matters.
    """
    try:
        mtime = os.stat(item.path).st_mtime_ns
    except OSError:
        mtime = 0  # _load raises the real error
    key = (item.path, mtime, item.profile, item.intent, item.flip_h, item.flip_v,
           repr(item.effects), printer_profile, proxy)
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    im = _load(item, printer_profile) if not proxy else load(item, printer_profile)
    if proxy and max(im.size) > PROXY_SIDE:
        im = im.copy()
        im.thumbnail((PROXY_SIDE, PROXY_SIDE), Image.BICUBIC, reducing_gap=2.0)
    if im.width * im.height <= _CACHE_PIXELS:
        with _CACHE_LOCK:
            _CACHE[key] = im
            while len(_CACHE) > 1 and sum(i.width * i.height for i in _CACHE.values()) > _CACHE_PIXELS:
                _CACHE.popitem(last=False)
    return im


def _load(item: ImageItem, printer_profile: str | None = None) -> Image.Image:
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


def _place(canvas: Image.Image, item: ImageItem, im: Image.Image, slot: Rect, s: float,
           fast: bool = False, ellipse: bool = False) -> None:
    f = fit(im.width, im.height, slot.scaled(s), item.crop, Rotation(item.rotation), item.hpan, item.vpan)
    if f.rotation:
        im = im.rotate(-f.rotation, expand=True)  # PIL rotates counter-clockwise
    size = (max(1, f.width), max(1, f.height))
    # fast (previews): box-reduce first, then bicubic. Final output keeps LANCZOS.
    im = im.resize(size, Image.BICUBIC, reducing_gap=2.0) if fast else im.resize(size, Image.LANCZOS)
    if item.crop:
        w, h = min(f.width, round(slot.w * s)), min(f.height, round(slot.h * s))
        im = im.crop((f.xoffset, f.yoffset, f.xoffset + w, f.yoffset + h))
    if item.mask:
        m = ImageOps.invert(Image.open(item.mask).convert("L").resize(im.size))
        im.putalpha(m)  # mask: white = hide, as the original inverted it
    if ellipse:   # round badge: keep only the circle (drawn 4x larger, then shrunk, for a smooth edge)
        w, h = im.size
        m = Image.new("L", (w * 4, h * 4), 0)
        ImageDraw.Draw(m).ellipse((0, 0, w * 4 - 1, h * 4 - 1), fill=255)
        m = m.resize((w, h), Image.LANCZOS)
        if im.mode == "RGBA":
            m = ImageChops.multiply(m, im.getchannel("A"))
        im = im.convert("RGB")
        im.putalpha(m)
    canvas.paste(im, (f.xpos, f.ypos), im if im.mode == "RGBA" else None)


def _caption(draw: ImageDraw.ImageDraw, text: str, band: Rect, s: float, color=(70, 70, 70)) -> None:
    font = ImageFont.load_default(size=max(6, round(band.h / 1.8 * s)))
    while len(text) > 1 and font.getlength(text) > band.w * s:  # shorten with an ellipsis to fit the cell
        text = text[:-2].rstrip() + "…"
    draw.text(((band.x + band.w / 2) * s, (band.y + band.h / 2) * s), text, fill=color, font=font, anchor="mm")


def _draw_shape(draw: ImageDraw.ImageDraw, kind: str, r: Rect, rgb: tuple, s: float) -> None:
    box = (r.x * s, r.y * s, (r.x + r.w) * s - 1, (r.y + r.h) * s - 1)
    if kind == "card":
        draw.rectangle(box, fill=rgb, outline=(205, 205, 205), width=max(1, round(0.5 * s)))
    elif kind == "rect":
        draw.rectangle(box, fill=rgb)
    elif kind == "round":
        draw.rounded_rectangle(box, radius=min(r.w, r.h) * 0.3 * s, fill=rgb)
    elif kind == "ellipse":
        draw.ellipse(box, fill=rgb)
    elif kind == "frame":
        draw.rectangle(box, outline=rgb, width=max(1, round(0.6 * s)))


def render_page(layout: Layout, page: int, dpi: int = 150, printer_profile: str | None = None,
                background=(255, 255, 255), fast: bool = False) -> Image.Image:
    s = dpi / 72
    p = layout.page
    canvas = Image.new("RGB", (round(p.width * s), round(p.height * s)), background)
    if not layout.images:
        return canvas
    if isinstance(layout, Poster):
        idx, _, win = layout.tile_window(page)
        item = layout.images[idx]
        poster = Image.new("RGB", (round(layout.poster_w * s), round(layout.poster_h * s)), background)
        _place(poster, item, load(item, printer_profile, fast), Rect(0, 0, layout.poster_w, layout.poster_h), s, fast)
        tile = poster.crop((round(win.x * s), round(win.y * s),
                            round((win.x + win.w) * s), round((win.y + win.h) * s)))
        canvas.paste(tile, (round(p.left * s), round(p.top * s)))
        return canvas
    if isinstance(layout, Triptych):
        if page >= len(layout.images):
            return canvas
        item = layout.images[page]
        group, panels = layout.panels_for(page)
        pic = Image.new("RGB", (max(1, round(group.w * s)), max(1, round(group.h * s))), background)
        _place(pic, item, load(item, printer_profile, fast), Rect(0, 0, group.w, group.h), s, fast)
        for r in panels:   # the gaps between panels stay paper-coloured
            box = (round((r.x - group.x) * s), round((r.y - group.y) * s),
                   round((r.x - group.x + r.w) * s), round((r.y - group.y + r.h) * s))
            canvas.paste(pic.crop(box), (round(r.x * s), round(r.y * s)))
        return canvas
    if isinstance(layout, Carousel):
        wedges = layout.wedges()
        for i in [*range(0, len(wedges), 2), *range(1, len(wedges), 2)]:  # evens, then odds on top
            item, poly, box = wedges[i]
            tmp = Image.new("RGB", canvas.size, background)
            _place(tmp, replace(item, crop=True, rotation=0, mask=None), load(item, printer_profile, fast), box, s, fast)
            mask = Image.new("L", canvas.size, 0)
            ImageDraw.Draw(mask).polygon([(x * s, y * s) for x, y in poly], fill=255)
            canvas.paste(tmp, mask=mask)
        return canvas
    draw = ImageDraw.Draw(canvas)
    round_photos = layout.shape == "ellipse"

    def guides():
        for x0, y0, x1, y1 in layout.cut_guides(page):
            draw.line((x0 * s, y0 * s, x1 * s, y1 * s), fill=(140, 140, 140), width=max(1, round(0.4 * s)))

    if not round_photos:
        guides()   # under the photos, so they never cover an image
    for kind, rect, rgb in layout.shapes(page):
        _draw_shape(draw, kind, rect, rgb, s)
    for card in layout.cards(page):   # white cards sit under the photos
        draw.rectangle((card.x * s, card.y * s, (card.x + card.w) * s - 1, (card.y + card.h) * s - 1),
                       fill=(255, 255, 255), outline=(205, 205, 205), width=max(1, round(0.5 * s)))
    for item, slot in layout.slots(page):
        if round_photos:
            item = replace(item, crop=True)   # a circle is always filled
        _place(canvas, item, load(item, printer_profile, fast), slot, s, fast, ellipse=round_photos)
    if round_photos:
        guides()   # a cut circle must show on top of the photo's edge
    for text, band in layout.captions(page):
        _caption(draw, text, band, s, layout.caption_color)
    return canvas


def render_all(layout: Layout, **kw) -> list[Image.Image]:
    return [render_page(layout, i, **kw) for i in range(layout.pages)]
