"""Turn a layout page into a PIL image at a given DPI. No Qt."""
from __future__ import annotations

import io
import math
from functools import lru_cache
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from dataclasses import replace

from PIL import Image, ImageCms, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from .effects import apply_effects
from .geometry import Rect, Rotation, fit
from .layouts import Carousel, Collage, ImageItem, Layout, Poster, Triptych

_SRGB = None


def _srgb():
    global _SRGB
    _SRGB = _SRGB or ImageCms.createProfile("sRGB")
    return _SRGB


_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_PIXELS = 100_000_000  # budget across cached images, roughly 300 MB of RGB


PROXY_SIDE = 2400  # longest side of the cached preview copy


def _key(item: ImageItem, printer_profile, proxy: bool) -> tuple:
    try:
        mtime = os.stat(item.path).st_mtime_ns
    except OSError:
        mtime = 0
    return (item.path, mtime, item.profile, item.intent, item.flip_h, item.flip_v, item.angle,
            repr(item.effects), printer_profile, proxy)


def uncached(items, printer_profile: str | None = None, proxy: bool = True) -> int:
    """How many of these photos load() would have to read from disk."""
    with _CACHE_LOCK:
        return len({_key(i, printer_profile, proxy) for i in items} - _CACHE.keys())


def load(item: ImageItem, printer_profile: str | None = None, proxy: bool = False) -> Image.Image:
    """Cached _load(). The returned image is shared: callers must not modify it in place.

    proxy=True returns a copy capped at PROXY_SIDE for fast previews.
    shortcut: a preview at more than ~PROXY_SIDE px per photo (huge zoom on a 1-up page)
    is slightly soft; raise PROXY_SIDE or render without fast=True if that matters.
    """
    key = _key(item, printer_profile, proxy)
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    im = _load(item, printer_profile, PROXY_SIDE if proxy else None)
    if im.width * im.height <= _CACHE_PIXELS:
        with _CACHE_LOCK:
            _CACHE[key] = im
            while len(_CACHE) > 1 and sum(i.width * i.height for i in _CACHE.values()) > _CACHE_PIXELS:
                _CACHE.popitem(last=False)
    return im


class Cancelled(Exception):
    """Raised inside render_page when its cancel() callback says stop."""


def _try_load(item, printer_profile, fast):
    try:
        load(item, printer_profile, fast)
    except Exception:
        pass  # the serial pass that follows raises it again, where it belongs


def _prefetch(items, printer_profile, fast, progress=None, cancel=None) -> None:
    """Decode and colour-manage the photos in parallel (Pillow and lcms release the GIL) so load() hits the cache.
    progress(done, total) is called from worker threads as each photo finishes."""
    todo = list({id(i): i for i in items}.values())
    done = [0]

    def one(item):
        if cancel and cancel():
            return   # queued photos are skipped; the ones already decoding finish
        _try_load(item, printer_profile, fast)
        done[0] += 1   # shortcut: unlocked counter, an off-by-one in a progress label is harmless
        if progress:
            progress(done[0], len(todo))

    if progress:
        progress(0, len(todo))
    if len(todo) > 1:
        with ThreadPoolExecutor(min(len(todo), os.cpu_count() or 2, 16 if fast else 4)) as ex:   # full-size photos are big: fewer at once
            list(ex.map(one, todo))
    elif todo:
        one(todo[0])


def _load(item: ImageItem, printer_profile: str | None = None, max_side: int | None = None) -> Image.Image:
    """Open, orient, colour-manage to RGB, flip and apply effects. max_side shrinks first, so previews skip the heavy work."""
    im = Image.open(item.path)
    if max_side and max(im.size) > max_side:
        r = max_side / max(im.size)
        im.draft(None, (math.ceil(im.width * r), math.ceil(im.height * r)))   # JPEG decodes at 1/2, 1/4, 1/8 size for free
    im = ImageOps.exif_transpose(im)
    if max_side and max(im.size) > max_side and im.mode in ("RGB", "RGBA", "L", "LA", "CMYK"):
        im.thumbnail((max_side, max_side), Image.BICUBIC, reducing_gap=2.0)   # before the colour transform: far fewer pixels
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
    if item.angle:
        alpha = alpha or Image.new("L", im.size, 255)
        im = im.rotate(-item.angle, Image.BICUBIC, expand=True)  # PIL turns counter-clockwise
        alpha = alpha.rotate(-item.angle, Image.BICUBIC, expand=True)
    if alpha:
        im.putalpha(alpha)
    return im


def _unit_poly(shape: str):
    """Outline of a polygon-like shape in a unit box (0..1 each way), or None for ellipse and rounded."""
    if shape == "hexagon":
        return [(.5, 0), (1, .25), (1, .75), (.5, 1), (0, .75), (0, .25)]
    if shape == "diamond":
        return [(.5, 0), (1, .5), (.5, 1), (0, .5)]
    if shape == "triangle":
        return [(.5, 0), (1, 1), (0, 1)]
    if shape == "star":
        pts = [((1 if i % 2 == 0 else .382) * math.cos(math.radians(-90 + 36 * i)),
                (1 if i % 2 == 0 else .382) * math.sin(math.radians(-90 + 36 * i))) for i in range(10)]
    elif shape == "heart":
        pts = [(16 * math.sin(t) ** 3, -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)))
               for t in (2 * math.pi * i / 120 for i in range(120))]
    else:
        return None
    x0, x1, y0, y1 = min(x for x, _ in pts), max(x for x, _ in pts), min(y for _, y in pts), max(y for _, y in pts)
    return [((x - x0) / (x1 - x0), (y - y0) / (y1 - y0)) for x, y in pts]   # fill the whole box


def _mask(shape: str, w: int, h: int) -> Image.Image:
    """An 'L' mask of a shape, drawn 4x larger and shrunk for a smooth edge."""
    W, H = w * 4, h * 4
    m = Image.new("L", (W, H), 0)
    d, poly = ImageDraw.Draw(m), _unit_poly(shape)
    if poly:
        d.polygon([(x * (W - 1), y * (H - 1)) for x, y in poly], fill=255)
    elif shape == "rounded":
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=min(W, H) * 0.22, fill=255)
    else:
        d.ellipse((0, 0, W - 1, H - 1), fill=255)
    return m.resize((w, h), Image.LANCZOS)


def _fade(mask: Image.Image, radius: float) -> Image.Image:
    """Soften a mask's edge, including edges at the image border (padded first: a blur would otherwise keep them hard)."""
    if radius <= 0:
        return mask
    p = math.ceil(radius * 2)
    big = Image.new("L", (mask.width + 2 * p, mask.height + 2 * p), 0)
    big.paste(mask, (p, p))
    return big.filter(ImageFilter.GaussianBlur(radius)).crop((p, p, p + mask.width, p + mask.height))


def _place_cut(canvas: Image.Image, item: ImageItem, im: Image.Image, slot: Rect, poly: list, s: float, fast: bool,
               background, feather: float = 0.0) -> None:
    """A photo filling `slot`, cut to the polygon `poly` (page points) and pasted onto the canvas."""
    w, h = max(1, round(slot.w * s)), max(1, round(slot.h * s))
    tmp = Image.new("RGB", (w, h), background)
    _place(tmp, replace(item, crop=True, rotation=0, mask=None), im, Rect(0, 0, slot.w, slot.h), s, fast)
    m = Image.new("L", (w * 4, h * 4), 0)
    ImageDraw.Draw(m).polygon([((x - slot.x) * s * 4, (y - slot.y) * s * 4) for x, y in poly], fill=255)
    canvas.paste(tmp, (round(slot.x * s), round(slot.y * s)), _fade(m.resize((w, h), Image.LANCZOS), feather * s / 2))


def _place(canvas: Image.Image, item: ImageItem, im: Image.Image, slot: Rect, s: float,
           fast: bool = False, shape: str | None = None) -> None:
    f = fit(im.width, im.height, slot.scaled(s), item.crop, Rotation(item.turn(slot.w, slot.h)), item.hpan, item.vpan)
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
    if shape:   # keep only the circle / hexagon / heart / ...
        m = _mask(shape, *im.size)
        if im.mode == "RGBA":
            m = ImageChops.multiply(m, im.getchannel("A"))
        im = im.convert("RGB")
        im.putalpha(m)
    canvas.paste(im, (f.xpos, f.ypos), im if im.mode == "RGBA" else None)


# Pillow's built-in font has no accented letters (it draws "ä" as an empty box). Pillow finds these by file name in the
# system font folders of Linux, macOS and Windows; the first one installed wins.
FONT_NAMES = ("DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf",
              "Helvetica.ttc", "SegoeUI.ttf")


@lru_cache(maxsize=64)
def caption_font(size: int):
    for name in FONT_NAMES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)   # last resort: ASCII only


def _caption(draw: ImageDraw.ImageDraw, text: str, band: Rect, s: float, color=(70, 70, 70)) -> None:
    font = caption_font(max(6, round(band.h / 1.8 * s)))
    while len(text) > 1 and font.getlength(text) > band.w * s:  # shorten with an ellipsis to fit the cell
        text = text[:-2].rstrip() + "…"
    draw.text(((band.x + band.w / 2) * s, (band.y + band.h / 2) * s), text, fill=color, font=font, anchor="mm")


def _draw_shape(draw: ImageDraw.ImageDraw, kind: str, r: Rect, rgb: tuple, s: float) -> None:
    x0, y0 = r.x * s, r.y * s
    box = (x0, y0, max(x0, (r.x + r.w) * s - 1), max(y0, (r.y + r.h) * s - 1))   # hairlines stay at least one pixel
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
                background=(255, 255, 255), fast: bool = False, progress=None, cancel=None, placing=None) -> Image.Image:
    s = dpi / 72

    def placed(k, n):   # placing(done, total) after each photo is drawn
        if placing:
            placing(k, n)

    def check():
        if cancel and cancel():
            raise Cancelled

    check()
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
    if hasattr(layout, "pile"):   # Scatter and Fan: tilted cards with a shadow
        pile = layout.pile(page)
        _prefetch([c[0] for c in pile], printer_profile, fast, progress, cancel)
        check()
        for k, (item, photo, card, angle) in enumerate(pile):
            check()
            placed(k, len(pile))
            w, h = max(1, round(card.w * s)), max(1, round(card.h * s))
            c = Image.new("RGB", (w, h), (255, 255, 255))
            _place(c, replace(item, crop=True, rotation=0), load(item, printer_profile, fast),
                   Rect(photo.x - card.x, photo.y - card.y, photo.w, photo.h), s, fast)
            c = c.convert("RGBA")
            c.putalpha(Image.new("L", (w, h), 255))
            c = c.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(255, 255, 255, 0))   # white, so a soft edge has no dark fringe
            x, y = round((card.x + card.w / 2) * s - c.width / 2), round((card.y + card.h / 2) * s - c.height / 2)
            if not layout.feather:   # a soft-edged card blends into its neighbours, so it casts no shadow
                pad, off = round(8 * s), round(2 * s)   # room for the blur, so the shadow is not cut off at the card's box
                sa = Image.new("L", (c.width + 2 * pad, c.height + 2 * pad), 0)
                sa.paste(c.getchannel("A").point(lambda v: v * 90 // 255), (pad, pad))
                sa = sa.filter(ImageFilter.GaussianBlur(max(1, 2.5 * s)))
                canvas.paste((0, 0, 0), (x - pad + off, y - pad + 2 * off), sa)
            else:
                c.putalpha(_fade(c.getchannel("A"), layout.feather * s / 2))
            canvas.paste(c, (x, y), c)
        return canvas
    if isinstance(layout, Carousel):
        wedges = layout.wedges()
        _prefetch([w[0] for w in wedges], printer_profile, fast, progress, cancel)
        check()
        for k, i in enumerate([*range(0, len(wedges), 2), *range(1, len(wedges), 2)]):  # evens, then odds on top
            check()
            placed(k, len(wedges))
            item, poly, box = wedges[i]
            tmp = Image.new("RGB", canvas.size, background)
            _place(tmp, replace(item, crop=True, rotation=0, mask=None), load(item, printer_profile, fast), box, s, fast)
            mask = Image.new("L", canvas.size, 0)
            ImageDraw.Draw(mask).polygon([(x * s, y * s) for x, y in poly], fill=255)
            canvas.paste(tmp, mask=_fade(mask, layout.feather * s / 2))
        return canvas
    draw = ImageDraw.Draw(canvas)
    round_photos = layout.shape != "rect"

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
    if isinstance(layout, Collage):   # the box keeps the photo's shape; the whole box turns about its centre
        slots = [(replace(i, angle=0), r, i.angle) for i, r in layout.slots(page)]
        _prefetch([i for i, _, _ in slots], printer_profile, fast, progress, cancel)
        for k, (item, r, angle) in enumerate(slots):
            check()
            placed(k, len(slots))
            w, h = max(1, round(r.w * s)), max(1, round(r.h * s))
            tmp = Image.new("RGBA", (w, h), (255, 255, 255, 0))
            _place(tmp, item, load(item, printer_profile, fast), Rect(0, 0, r.w, r.h), s, fast)
            if angle:
                tmp = tmp.rotate(-angle, Image.BICUBIC, expand=True, fillcolor=(255, 255, 255, 0))   # PIL turns counter-clockwise
            tmp.putalpha(_fade(tmp.getchannel("A"), layout.feather * s / 2))
            canvas.paste(tmp, (round((r.x + r.w / 2) * s - tmp.width / 2), round((r.y + r.h / 2) * s - tmp.height / 2)), tmp)
        return canvas
    polys = layout.outlines(page)
    slots = layout.slots(page)
    _prefetch([i for i, _ in slots], printer_profile, fast, progress, cancel)
    check()
    for k, (item, slot) in enumerate(slots):
        check()
        placed(k, len(slots))
        if polys is not None and k < len(polys):
            _place_cut(canvas, item, load(item, printer_profile, fast), slot, polys[k], s, fast, background, layout.feather)
            continue
        if round_photos:
            item = replace(item, crop=True)   # a circle is always filled
        if layout.feather:   # draw on a transparent tile, soften its edge, then lay it over what is already there
            w, h = max(1, round(slot.w * s)), max(1, round(slot.h * s))
            tile = Image.new("RGBA", (w, h), (255, 255, 255, 0))   # white, not black: a soft edge must not pick up a dark fringe
            _place(tile, item, load(item, printer_profile, fast), Rect(0, 0, slot.w, slot.h), s, fast,
                   shape=layout.shape if round_photos else None)
            tile.putalpha(_fade(tile.getchannel("A"), layout.feather * s / 2))
            canvas.paste(tile, (round(slot.x * s), round(slot.y * s)), tile)
            continue
        _place(canvas, item, load(item, printer_profile, fast), slot, s, fast, shape=layout.shape if round_photos else None)
    if round_photos:
        guides()   # a cut circle must show on top of the photo's edge
    for text, band, *color in layout.captions(page):   # a layout may colour single captions (calendar weekends)
        _caption(draw, text, band, s, color[0] if color else layout.caption_color)
    return canvas


def render_all(layout: Layout, **kw) -> list[Image.Image]:
    return [render_page(layout, i, **kw) for i in range(layout.pages)]
