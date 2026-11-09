"""photosheet-cli: batch render images onto sheets and save as PDF or PNG."""
from __future__ import annotations

import argparse
import sys

from .core.geometry import PageExtent
from .core.layouts import MM, Carousel, FixedSize, ImageItem, Mosaic, NUp, Passport, Poster, Single, Strip
from .core.render import render_all

PAGES = {"a4": (595, 842), "letter": (612, 792), "a5": (420, 595), "4x6": (288, 432), "5x7": (360, 504)}


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
    elif args.layout == "strip":
        lay = Strip(page=page, frames=args.frames, strip_w=args.strip_w * MM, strip_h=args.strip_h * MM)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="photosheet-cli")
    ap.add_argument("images", nargs="+")
    ap.add_argument("-o", "--out", required=True, help=".pdf (multipage) or .png (page number appended)")
    ap.add_argument("--layout", choices=["nup", "single", "poster", "carousel", "fixed", "mosaic", "strip", "passport"], default="nup")
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--cell-w", type=float, default=35, help="fixed layout cell width, mm (default: passport)")
    ap.add_argument("--cell-h", type=float, default=45, help="fixed layout cell height, mm")
    ap.add_argument("--row-height", type=float, default=50, help="mosaic target row height, mm")
    ap.add_argument("--caption", choices=["none", "filename", "number", "both"], default="none",
                    help="nup: contact-sheet caption under each photo")
    ap.add_argument("--marks", action="store_true", help="nup/fixed: crop marks at photo corners")
    ap.add_argument("--frames", type=int, default=4, help="strip: photos per strip")
    ap.add_argument("--strip-w", type=float, default=51, help="strip width, mm")
    ap.add_argument("--strip-h", type=float, default=152, help="strip height, mm")
    ap.add_argument("--landscape", action="store_true", help="turn the paper sideways (e.g. 4x6 for 4 x 2 photos)")
    ap.add_argument("--copies", type=int, default=0, help="passport: copies of each photo, 0 = fill the sheet")
    ap.add_argument("--gutter", type=float, default=4, help="passport: gap between photos, points")
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
