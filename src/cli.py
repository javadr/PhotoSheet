"""photosheet-cli: batch render images onto sheets and save as PDF or PNG."""
from __future__ import annotations

import argparse
import datetime
import sys

from .core.geometry import PageExtent
from .core.layouts import (BENTO, MM, RECIPES, SHAPE_NAMES, Badges, Bento, Calendar, Carousel, Collage, Feature, Filmstrip,
                           FixedSize, FramedPrint, Honeycomb, ImageItem, Masonry, Mosaic, NUp, Package, Passport,
                           Polaroid, Poster, Ring, Scatter, Shapes, Single, Spiral, Strip, Triptych)
from .core.render import render_all

PAGES = {"a4": (595, 842), "letter": (612, 792), "a5": (420, 595), "4x6": (288, 432), "5x7": (360, 504),
         "8x10": (576, 720)}


def build(args) -> NUp | Single | Poster:
    w, h = PAGES[args.page]
    if args.landscape:
        w, h = h, w
    page = PageExtent(w, h)
    page.set_margins(*(args.margin,) * 4)
    if args.layout == "nup":
        lay = NUp(page=page, rows=args.rows, cols=args.cols, caption=args.caption, marks=args.marks)
    elif args.layout == "single":
        lay = Single(page=page)
    elif args.layout == "fixed":
        lay = FixedSize(page=page, cell_w=args.cell_w * MM, cell_h=args.cell_h * MM, marks=args.marks)
    elif args.layout == "passport":
        lay = Passport(page=page, cell_w=args.cell_w * MM, cell_h=args.cell_h * MM, copies=args.copies,
                       hgutter=args.gutter, vgutter=args.gutter, marks=args.marks)
    elif args.layout == "polaroid":
        lay = Polaroid(page=page, rows=args.rows, cols=args.cols, caption=args.caption, marks=args.marks,
                       border=args.border)
    elif args.layout == "triptych":
        lay = Triptych(page=page, panels=args.panels, gap=args.gutter, vertical=args.vertical)
    elif args.layout == "package":
        lay = Package(page=page, prints=[tuple(x) for x in RECIPES[args.recipe][2]], gutter=args.gutter,
                      marks=args.marks)
    elif args.layout == "collage":
        lay = Collage(page=page)
    elif args.layout == "framed":
        lay = FramedPrint(page=page, mat=args.mat, color=args.mat_color, caption=args.caption)
    elif args.layout == "badges":
        lay = Badges(page=page, diameter=args.diameter * MM, gutter=args.gutter, staggered=not args.square,
                     marks=args.marks)
    elif args.layout == "feature":
        lay = Feature(page=page, thumbs=args.thumbs, side=args.side, hero_share=args.hero_share, gutter=args.gutter)
    elif args.layout == "filmstrip":
        lay = Filmstrip(page=page, frames=args.frames or 6, strip_w=(args.strip_w or 35) * MM, direction=args.direction)
    elif args.layout == "bento":
        lay = Bento(page=page, template=args.template, gutter=args.gutter)
    elif args.layout == "spiral":
        lay = Spiral(page=page, photos=args.spiral_photos, share=args.share, clockwise=not args.ccw, gutter=args.gutter)
    elif args.layout == "honeycomb":
        lay = Honeycomb(page=page, size=args.hex_size * MM, gutter=args.gutter, marks=args.marks)
    elif args.layout == "calendar":
        lay = Calendar(page=page, year=args.year, month=args.month, months=args.months, week_start=args.week_start,
                       photo_share=args.photo_share, gutter=args.gutter)
    elif args.layout == "masonry":
        lay = Masonry(page=page, columns=args.cols, gutter=args.gutter)
    elif args.layout == "shapes":
        lay = Shapes(page=page, rows=args.rows, cols=args.cols, gutter=args.gutter, kind=args.shape)
    elif args.layout == "ring":
        lay = Ring(page=page, photos=args.ring_photos, center=not args.no_center, gutter=args.gutter, marks=args.marks)
    elif args.layout == "scatter":
        lay = Scatter(page=page, photos=args.scatter_photos, size=args.scatter_size / 100, tilt=args.tilt,
                      border=args.border, seed=args.seed)
    elif args.layout == "strip":
        lay = Strip(page=page, frames=args.frames or 4, strip_w=(args.strip_w or 51) * MM, strip_h=args.strip_h * MM,
                    direction=args.direction)
    elif args.layout == "mosaic":
        lay = Mosaic(page=page, row_height=args.row_height * MM)
    elif args.layout == "carousel":
        lay = Carousel(page=page, segments=args.segments)
    else:
        lay = Poster(page=page, poster_w=args.poster_w * 72, poster_h=args.poster_h * 72)
    for path in args.images:
        for _ in range(args.repeat):
            lay.add(ImageItem(path, crop=args.crop))
    return lay


class _ShowVersion(argparse.Action):
    """--version: print the banner and exit. Runs before argparse checks the required arguments."""

    def __init__(self, option_strings, dest, **kw):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, help="show the version and exit")

    def __call__(self, parser, namespace, values, option_string=None):
        from .banner import print_banner
        print_banner()
        parser.exit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="photosheet-cli")
    ap.add_argument("-V", "--version", action=_ShowVersion)
    ap.add_argument("images", nargs="+")
    ap.add_argument("-o", "--out", required=True, help=".pdf (multipage) or .png (page number appended)")
    ap.add_argument("--layout", choices=["nup", "single", "poster", "carousel", "fixed", "mosaic", "strip", "passport", "polaroid", "triptych", "package",
                                                       "collage", "framed", "badges", "feature", "filmstrip", "bento", "spiral",
                                                       "honeycomb", "calendar", "masonry", "shapes", "ring", "scatter"], default="nup")
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--cell-w", type=float, default=35, help="fixed layout cell width, mm (default: passport)")
    ap.add_argument("--cell-h", type=float, default=45, help="fixed layout cell height, mm")
    ap.add_argument("--row-height", type=float, default=50, help="mosaic target row height, mm")
    ap.add_argument("--caption", choices=["none", "filename", "number", "both"], default="none",
                    help="nup: contact-sheet caption under each photo")
    ap.add_argument("--marks", action="store_true", help="nup/fixed: crop marks at photo corners")
    ap.add_argument("--frames", type=int, help="strip / filmstrip: photos per strip (default 4 / 6)")
    ap.add_argument("--strip-w", type=float, help="strip / filmstrip width, mm (default 51 / 35)")
    ap.add_argument("--strip-h", type=float, default=152, help="strip length, mm")
    ap.add_argument("--direction", choices=["auto", "vertical", "horizontal"], default="auto",
                    help="strip / filmstrip: which way the strips run (auto = horizontal on a landscape page)")
    ap.add_argument("--border", type=float, default=10, help="polaroid: border width, points")
    ap.add_argument("--panels", type=int, default=3, help="triptych: number of panels")
    ap.add_argument("--vertical", action="store_true", help="triptych: stack the panels top to bottom")
    ap.add_argument("--recipe", choices=list(RECIPES), default="5x7-4wallets", help="package: which sheet to fill")
    ap.add_argument("--mat", type=float, default=36, help="framed: mat width, points")
    ap.add_argument("--mat-color", choices=["white", "black", "cream"], default="white", help="framed: mat colour")
    ap.add_argument("--diameter", type=float, default=38, help="badges: diameter, mm (25, 32, 38, 44, 58, 75 are common)")
    ap.add_argument("--square", action="store_true", help="badges: square grid instead of staggered rows")
    ap.add_argument("--thumbs", type=int, default=4, help="feature: number of small photos next to the hero")
    ap.add_argument("--side", action="store_true", help="feature: small photos in a column beside the hero")
    ap.add_argument("--hero-share", type=float, default=0.7, help="feature: hero's share of the page, 0.2 to 0.9")
    ap.add_argument("--template", choices=list(BENTO), default="hero-left", help="bento: which tile arrangement")
    ap.add_argument("--spiral-photos", type=int, default=5, help="spiral: photos per page")
    ap.add_argument("--share", type=float, default=0.618, help="spiral: share each photo takes of what is left")
    ap.add_argument("--ccw", action="store_true", help="spiral: turn counter-clockwise")
    ap.add_argument("--hex-size", type=float, default=40, help="honeycomb: cell width across the flats, mm")
    ap.add_argument("--year", type=int, default=datetime.date.today().year, help="calendar: year")
    ap.add_argument("--month", type=int, default=1, help="calendar: first month (1-12)")
    ap.add_argument("--months", type=int, default=12, help="calendar: how many months (pages)")
    ap.add_argument("--week-start", choices=["monday", "sunday"], default="monday", help="calendar: first weekday")
    ap.add_argument("--photo-share", type=float, default=0.55, help="calendar: the photo's share of the page height")
    ap.add_argument("--shape", choices=list(SHAPE_NAMES), default="heart", help="shapes: what to crop the photos to")
    ap.add_argument("--ring-photos", type=int, default=8, help="ring: photos around the circle (3-36)")
    ap.add_argument("--no-center", action="store_true", help="ring: leave the middle empty")
    ap.add_argument("--scatter-photos", type=int, default=6, help="scatter: photos per page")
    ap.add_argument("--scatter-size", type=float, default=45, help="scatter: photo size, percent of the page's shorter side")
    ap.add_argument("--tilt", type=float, default=12, help="scatter: largest tilt either way, degrees")
    ap.add_argument("--seed", type=int, default=1, help="scatter: random seed (same seed = same pile)")
    ap.add_argument("--landscape", action="store_true", help="turn the paper sideways (e.g. 4x6 for 4 x 2 photos)")
    ap.add_argument("--copies", type=int, default=0, help="passport: copies of each photo, 0 = fill the sheet")
    ap.add_argument("--gutter", type=float, default=4, help="gap between photos / panels / prints, points")
    ap.add_argument("--page", choices=list(PAGES), default="a4")
    ap.add_argument("--margin", type=float, default=18, help="points")
    ap.add_argument("--poster-w", type=float, default=16, help="inches")
    ap.add_argument("--poster-h", type=float, default=12, help="inches")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--crop", action="store_true")
    ap.add_argument("--printer-profile")
    args = ap.parse_args(argv)
    pages = render_all(build(args), dpi=args.dpi, printer_profile=args.printer_profile)
    if args.out.lower().endswith(".pdf"):
        pages[0].save(args.out, save_all=True, append_images=pages[1:], resolution=args.dpi)
    else:
        stem, dot, ext = args.out.rpartition(".")
        for i, im in enumerate(pages):
            im.save(f"{stem}-{i + 1}{dot}{ext}" if len(pages) > 1 else args.out, dpi=(args.dpi,) * 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
