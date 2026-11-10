import pytest
from PIL import Image

from photosheet import cli
from photosheet.core.effects import apply_effects
from photosheet.core.geometry import PageExtent
from photosheet.core.layouts import ImageItem, NUp, Poster
from photosheet.core.render import render_all, render_page


@pytest.fixture
def red(tmp_path):
    p = tmp_path / "red.png"
    Image.new("RGB", (200, 100), (255, 0, 0)).save(p)
    return str(p)


def test_nup_places_image_in_cell(red):
    n = NUp(page=PageExtent(100, 100), rows=1, cols=2, hgutter=0, vgutter=0)
    n.add(ImageItem(red, rotation=0))
    im = render_page(n, 0, dpi=72)
    assert im.size == (100, 100)
    assert im.getpixel((25, 50)) == (255, 0, 0)       # inside first cell
    assert im.getpixel((75, 50)) == (255, 255, 255)   # second cell empty


def test_crop_fills_cell(red):
    n = NUp(page=PageExtent(100, 100), rows=1, cols=1)
    n.add(ImageItem(red, crop=True, rotation=0))
    im = render_page(n, 0, dpi=72)
    assert im.getpixel((2, 2)) == (255, 0, 0)


def test_poster_tiles_cover_whole_image(red):
    p = Poster(page=PageExtent(100, 100), poster_w=250, poster_h=100, hoverlap=10, voverlap=10)
    p.add(ImageItem(red, crop=True, rotation=0))
    pages = render_all(p, dpi=72)
    assert len(pages) == 3
    assert pages[2].getpixel((10, 50)) == (255, 0, 0)


def test_effects_and_unknown_effect(red):
    im = Image.open(red)
    assert apply_effects(im, [("Desaturate", {})]).getpixel((0, 0))[0] == \
        apply_effects(im, [("Desaturate", {})]).getpixel((0, 0))[1]
    with pytest.raises(ValueError):
        apply_effects(im, [("Nope", {})])


def test_cli_writes_multipage_pdf(red, tmp_path):
    out = tmp_path / "o.pdf"
    assert cli.main([red, "-o", str(out), "--rows", "1", "--cols", "1", "--repeat", "2", "--dpi", "50"]) == 0
    assert out.read_bytes().startswith(b"%PDF")


def test_carousel_renders_wedges_and_leaves_corners_blank(red):
    from photosheet.core.layouts import Carousel
    c = Carousel(page=PageExtent(200, 200), segments=2, overlap=0)
    c.add(ImageItem(red))
    c.add(ImageItem(red))
    im = render_page(c, 0, dpi=72)
    assert im.getpixel((100, 40)) == (255, 0, 0)       # inside the disc
    assert im.getpixel((2, 2)) == (255, 255, 255)      # outside the circle


def test_load_is_cached_and_invalidated_by_file_change_or_effect(red):
    import os
    from photosheet.core import render
    item = ImageItem(red)
    a = render.load(item)
    assert render.load(item) is a                              # cache hit
    assert render.load(ImageItem(red, effects=[("Gamma", {"gamma": 1.5})])) is not a
    st = os.stat(red)
    Image.new("RGB", (200, 100), (0, 255, 0)).save(red)
    os.utime(red, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert render.load(item).getpixel((0, 0)) == (0, 255, 0)   # edited file is reloaded


def test_fast_preview_matches_full_quality_on_flat_colour(red):
    n = NUp(page=PageExtent(100, 100), rows=1, cols=1)
    n.add(ImageItem(red, rotation=0))
    assert render_page(n, 0, 72, fast=True).getpixel((50, 50)) == render_page(n, 0, 72).getpixel((50, 50))


def test_proxy_is_capped_and_full_load_untouched(tmp_path):
    from photosheet.core import render
    big = tmp_path / "big.png"
    Image.new("RGB", (5000, 2500), (10, 20, 30)).save(big)
    item = ImageItem(str(big))
    assert max(render.load(item, proxy=True).size) == render.PROXY_SIDE
    assert render.load(item).size == (5000, 2500)


def test_fixed_and_mosaic_render_and_cli(red, tmp_path):
    from photosheet.core.layouts import FixedSize, Mosaic
    f = FixedSize(page=PageExtent(200, 200), cell_w=80, cell_h=80, hgutter=10, vgutter=10)
    f.add(ImageItem(red, crop=True, rotation=0))
    assert render_page(f, 0, dpi=72).getpixel((50, 50)) == (255, 0, 0)    # inside first cell (centred block)
    m = Mosaic(page=PageExtent(200, 200), row_height=60, gutter=0)
    m.add(ImageItem(red))
    assert render_page(m, 0, dpi=72).getpixel((30, 15)) == (255, 0, 0)
    for lay in ("fixed", "mosaic", "carousel", "poster", "single"):
        out = tmp_path / f"{lay}.pdf"
        assert cli.main([red, "-o", str(out), "--layout", lay, "--dpi", "50"]) == 0
        assert out.read_bytes().startswith(b"%PDF")


def test_marks_captions_and_strip_render(red, tmp_path):
    from photosheet.core.layouts import FixedSize, Strip
    f = FixedSize(page=PageExtent(200, 200), cell_w=80, cell_h=80, hgutter=20, vgutter=20, marks=True)
    f.add(ImageItem(red, crop=True, rotation=0))
    im = render_page(f, 0, dpi=72)
    assert im.getpixel((6, 10)) != (255, 255, 255)        # a tick left of the first cell's top-left corner
    assert im.getpixel((50, 50)) == (255, 0, 0)           # the photo itself is not covered
    n = NUp(page=PageExtent(200, 200), rows=1, cols=1, caption="both", caption_pt=14)
    n.add(ImageItem(red, rotation=0))
    cap = render_page(n, 0, dpi=72)
    assert any(cap.getpixel((x, y)) != (255, 255, 255) for x in range(60, 140) for y in range(170, 200))  # text drawn
    s = Strip(page=PageExtent(200, 300), frames=2, strip_w=100, strip_h=250, padding=10, frame_gap=10)
    for _ in range(2):
        s.add(ImageItem(red, crop=True, rotation=0))
    assert render_page(s, 0, dpi=72).getpixel((100, 70)) == (255, 0, 0)
    for args in (["--layout", "nup", "--caption", "both", "--marks"], ["--layout", "strip", "--frames", "2"],
                 ["--layout", "fixed", "--marks"]):
        out = tmp_path / "o.pdf"
        assert cli.main([red, red, "-o", str(out), "--dpi", "50", *args]) == 0 and out.stat().st_size > 0


def test_cli_passport_4x6_landscape_renders_eight_copies(red, tmp_path):
    out = tmp_path / "passport.png"
    assert cli.main([red, "-o", str(out), "--layout", "passport", "--page", "4x6", "--landscape",
                     "--margin", "4", "--crop", "--dpi", "72"]) == 0
    im = Image.open(out)
    assert im.size == (432, 288)
    red_blocks = sum(1 for x in (60, 165, 270, 375) for y in (70, 215) if im.getpixel((x, y)) == (255, 0, 0))
    assert red_blocks == 8                                  # one photo copied into all 4 x 2 slots


def test_pan_picks_which_part_of_a_cropped_photo_is_printed(tmp_path):
    p = tmp_path / "half.png"
    im = Image.new("RGB", (200, 100), (255, 0, 0))
    im.paste((0, 0, 255), (100, 0, 200, 100))                 # left half red, right half blue
    im.save(p)
    n = NUp(page=PageExtent(100, 100), rows=1, cols=1)
    n.add(ImageItem(str(p), crop=True, rotation=0, hpan=0.0))
    assert render_page(n, 0, dpi=72).getpixel((50, 50)) == (255, 0, 0)       # pan left: the red side
    n.images[0].hpan = 1.0
    assert render_page(n, 0, dpi=72).getpixel((50, 50)) == (0, 0, 255)       # pan right: the blue side


def test_version_flag_prints_banner_and_exits_without_other_arguments(capsys):
    import pytest as _pytest
    from photosheet import __version__
    for flag in ("--version", "-V"):
        with _pytest.raises(SystemExit) as e:
            cli.main([flag])                       # no images, no -o: must still work
        assert e.value.code == 0
        out = capsys.readouterr().out
        assert f"v{__version__}" in out and "Print many photos per sheet" in out
        assert "\x1b[" not in out                  # captured output is not a terminal: no colour codes


def test_banner_is_coloured_on_a_terminal_and_plain_otherwise():
    import io

    from rich.console import Console

    from photosheet import __version__
    from photosheet.banner import ART, print_banner
    tty = io.StringIO()
    print_banner(Console(file=tty, force_terminal=True, color_system="truecolor", width=120))
    out = tty.getvalue()
    assert "\x1b[1;38;2;0;200;255m" in out and "\x1b[1;38;2;255;90;200m" in out   # gradient: cyan start, magenta end
    plain = io.StringIO()
    print_banner(Console(file=plain, force_terminal=False, width=120))
    text = plain.getvalue()
    assert "\x1b[" not in text and f"v{__version__}" in text
    assert all(line.rstrip() in text for line in ART.strip("\n").splitlines())     # the whole art is there
    assert max(len(line) for line in ART.strip("\n").splitlines()) < 80            # fits a normal terminal


def test_polaroid_draws_a_white_card_with_a_thick_bottom_and_the_photo_inside(red):
    from photosheet.core.layouts import Polaroid
    pg = PageExtent(220, 260)
    p = Polaroid(page=pg, rows=1, cols=1, border=10, bottom_ratio=3, caption="both", caption_pt=9)
    p.add(ImageItem(red, crop=True, rotation=0))
    im = render_page(p, 0, dpi=72)
    card, = p.cards(0)
    (_, photo), = p.slots(0)
    assert im.getpixel((int(photo.x + photo.w / 2), int(photo.y + photo.h / 2))) == (255, 0, 0)    # photo in the middle
    assert im.getpixel((int(card.x + 4), int(card.y + 4))) == (255, 255, 255)                      # white border
    edge = im.crop((int(card.x + 12), int(photo.y + photo.h + 2), int(card.x + card.w - 12), int(card.y + card.h - 2)))
    assert edge.getextrema()[0][0] < 200                                                    # caption text drawn in the bottom edge
    assert im.getpixel((int(card.x), int(card.y + card.h / 2))) != (255, 255, 255)                  # hairline outline


def test_triptych_leaves_gaps_and_each_panel_shows_its_part(tmp_path):
    from photosheet.core.layouts import Triptych
    f = tmp_path / "three.png"
    im = Image.new("RGB", (300, 100), (255, 0, 0))
    im.paste((0, 255, 0), (100, 0, 200, 100))
    im.paste((0, 0, 255), (200, 0, 300, 100))
    im.save(f)
    t = Triptych(page=PageExtent(300, 200), panels=3, gap=10)
    t.add(ImageItem(str(f), rotation=0))
    out = render_page(t, 0, dpi=72)
    group, panels = t.panels_for(0)
    cy = int(group.y + group.h / 2)
    centres = [int(r.x + r.w / 2) for r in panels]
    assert [out.getpixel((x, cy)) for x in centres] == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]   # each panel its own third
    gap_x = int(panels[0].x + panels[0].w + 5)
    assert out.getpixel((gap_x, cy)) == (255, 255, 255)                                             # the gap is paper


def test_package_renders_every_print_and_collage_respects_boxes_and_order(red, tmp_path):
    from photosheet.core.layouts import Collage, Package
    pk = Package(page=PageExtent(576, 720), prints=[(127, 177.8, 1), (63.5, 88.9, 4)])
    pk.add(ImageItem(red, crop=True, rotation=0))
    im = render_page(pk, 0, dpi=72)
    for _, r in pk.slots(0):
        assert im.getpixel((int(r.x + r.w / 2), int(r.y + r.h / 2))) == (255, 0, 0)
    blue = tmp_path / "blue.png"
    Image.new("RGB", (100, 100), (0, 0, 255)).save(blue)
    c = Collage(page=PageExtent(300, 300))
    a = ImageItem(red, box=(20, 20, 160, 160), crop=True, rotation=0)
    b = ImageItem(str(blue), box=(100, 100, 160, 160), crop=True, rotation=0)
    c.add(a)
    c.add(b)
    out = render_page(c, 0, dpi=72)
    assert out.getpixel((60, 60)) == (255, 0, 0) and out.getpixel((200, 200)) == (0, 0, 255)
    assert out.getpixel((130, 130)) == (0, 0, 255)                          # overlap: the later photo is on top
    assert out.getpixel((10, 10)) == (255, 255, 255)                        # outside every box is paper
    c.images.reverse()
    assert render_page(c, 0, dpi=72).getpixel((130, 130)) == (255, 0, 0)    # reorder = bring to front


def test_cli_new_layouts_and_presets_roundtrip(red, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Collage, Package, Polaroid, Triptych
    for args in (["--layout", "polaroid", "--rows", "2", "--cols", "2", "--caption", "number"],
                 ["--layout", "triptych", "--panels", "3"], ["--layout", "package", "--page", "8x10"],
                 ["--layout", "package", "--page", "8x10", "--recipe", "2x4x6"], ["--layout", "collage"]):
        out = tmp_path / "o.pdf"
        assert cli.main([red, red, "-o", str(out), "--dpi", "40", *args]) == 0 and out.stat().st_size > 0
    c = Collage(page=PageExtent(300, 300), snap=False)
    c.add(ImageItem(red, box=(10, 20, 30, 40)))
    pk = Package(page=PageExtent(576, 720), prints=[(63.5, 88.9, 2)], gutter=6)
    for lay in (c, pk, Polaroid(page=PageExtent(300, 300), border=7), Triptych(page=PageExtent(300, 300), panels=5, vertical=True)):
        f = str(tmp_path / "p.json")
        preset.save(lay, f)
        back = preset.load(f)
        assert type(back) is type(lay) and back == lay                       # tuples/lists survive the JSON round trip


def test_banner_does_not_crash_when_there_is_no_stdout(monkeypatch):
    import sys

    from photosheet.banner import print_banner
    monkeypatch.setattr(sys, "stdout", None)     # what a windowed Windows executable has
    print_banner()                                # must simply do nothing


def test_badges_are_cropped_to_circles_with_cut_rings(red):
    from photosheet.core.layouts import Badges
    pg = PageExtent(300, 300)
    pg.set_margins(10, 10, 10, 10)
    b = Badges(page=pg, diameter=100, gutter=10, staggered=False, marks=True)
    b.add(ImageItem(red))
    im = render_page(b, 0, dpi=72)
    (_, r), = b.slots(0)[:1]
    cx, cy = int(r.x + r.w / 2), int(r.y + r.h / 2)
    assert im.getpixel((cx, cy)) == (255, 0, 0)                                                     # photo in the middle
    assert im.getpixel((int(r.x + 2), int(r.y + 2))) == (255, 255, 255)                            # square corner cut away
    assert im.getpixel((int(r.x + 2), cy)) != (255, 255, 255)                                       # but the circle's edge is inside
    import math
    ring = [im.getpixel((int(cx + 50 * math.cos(a / 10)), int(cy + 50 * math.sin(a / 10)))) for a in range(63)]
    assert any(p != (255, 0, 0) and p != (255, 255, 255) for p in ring)                            # the grey cut ring is drawn


def test_framed_print_feature_and_filmstrip_render(red, tmp_path):
    from photosheet.core.layouts import Feature, Filmstrip, FramedPrint
    blue = tmp_path / "blue.png"
    Image.new("RGB", (200, 100), (0, 0, 255)).save(blue)
    fpg = PageExtent(300, 400)
    fpg.set_margins(15, 15, 15, 15)
    f = FramedPrint(page=fpg, mat=50, color="black", caption="number")
    f.add(ImageItem(red, rotation=0))
    im = render_page(f, 0, dpi=72)
    (_, ph), = f.slots(0)
    assert im.getpixel((int(ph.x + ph.w / 2), int(ph.y + ph.h / 2))) == (255, 0, 0)               # the photo
    assert im.getpixel((int(f._card().x + 20), int(f._card().y + 20))) == (20, 20, 20)             # black mat
    assert im.getpixel((5, 5)) == (255, 255, 255)                                                   # page margin is paper
    ft = Feature(page=PageExtent(300, 400), thumbs=2, hero_share=0.6, gutter=6)
    ft.add(ImageItem(red, crop=True, rotation=0))
    ft.add(ImageItem(str(blue), crop=True, rotation=0))
    out = render_page(ft, 0, dpi=72)
    hero, thumb = ft.slots(0)[0][1], ft.slots(0)[1][1]
    assert out.getpixel((int(hero.x + hero.w / 2), int(hero.y + hero.h / 2))) == (255, 0, 0)
    assert out.getpixel((int(thumb.x + thumb.w / 2), int(thumb.y + thumb.h / 2))) == (0, 0, 255)
    pg = PageExtent(400, 500)
    pg.set_margins(10, 10, 10, 10)
    fs = Filmstrip(page=pg, frames=3)
    fs.add(ImageItem(red, crop=True, rotation=0))
    film = render_page(fs, 0, dpi=72)
    strip = next(r for k, r, _ in fs.shapes(0) if k == "rect")
    hole = next(r for k, r, _ in fs.shapes(0) if k == "round")
    (_, fr), = fs.slots(0)[:1]
    assert film.getpixel((int(strip.x + 1), int(strip.y + strip.h / 2))) == (22, 22, 22)           # black film
    assert film.getpixel((int(hole.x + hole.w / 2), int(hole.y + hole.h / 2))) == (255, 255, 255)  # sprocket hole shows paper
    assert film.getpixel((int(fr.x + fr.w / 2), int(fr.y + fr.h / 2))) == (255, 0, 0)              # the frame


def test_cli_and_presets_for_the_last_four_layouts(red, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Badges, Feature, Filmstrip, FramedPrint
    for args in (["--layout", "framed", "--mat-color", "cream", "--caption", "filename"],
                 ["--layout", "badges", "--diameter", "32", "--marks"], ["--layout", "badges", "--square"],
                 ["--layout", "feature", "--thumbs", "2", "--side"], ["--layout", "filmstrip"],
                 ["--layout", "filmstrip", "--frames", "4", "--strip-w", "30"], ["--layout", "strip"]):
        out = tmp_path / "o.pdf"
        assert cli.main([red, red, red, "-o", str(out), "--dpi", "40", *args]) == 0 and out.stat().st_size > 0
    for lay in (FramedPrint(page=PageExtent(300, 300), mat=20, color="black", caption="number"),
                Badges(page=PageExtent(300, 300), diameter=90, staggered=False, marks=True),
                Feature(page=PageExtent(300, 300), thumbs=5, side=True, hero_share=0.5),
                Filmstrip(page=PageExtent(595, 842), frames=8)):
        f = str(tmp_path / "p.json")
        preset.save(lay, f)
        back = preset.load(f)
        assert type(back) is type(lay) and back == lay


def test_horizontal_strips_render_wide_black_film_and_frames_in_a_row(red):
    from photosheet.core.layouts import Filmstrip, Strip
    pg = PageExtent(500, 300)
    pg.set_margins(10, 10, 10, 10)
    fs = Filmstrip(page=pg, frames=3)
    fs.add(ImageItem(red, crop=True, rotation=0))
    fs.add(ImageItem(red, crop=True, rotation=0))
    im = render_page(fs, 0, dpi=72)
    strip = next(r for k, r, _ in fs.shapes(0) if k == "rect")
    assert strip.w > strip.h and im.getpixel((int(strip.x + strip.w / 2), int(strip.y + 1))) == (22, 22, 22)
    a, b = fs.slots(0)[0][1], fs.slots(0)[1][1]
    assert a.y == b.y and b.x > a.x                                                     # frames in a row
    assert im.getpixel((int(a.x + a.w / 2), int(a.y + a.h / 2))) == (255, 0, 0)
    booth = Strip(page=pg, frames=3, strip_w=60, strip_h=240, padding=6, frame_gap=4)
    booth.add(ImageItem(red, crop=True, rotation=0))
    out = render_page(booth, 0, dpi=72)
    f0 = booth.slots(0)[0][1]
    assert out.getpixel((int(f0.x + f0.w / 2), int(f0.y + f0.h / 2))) == (255, 0, 0)
    for args in (["--layout", "filmstrip", "--landscape"], ["--layout", "strip", "--landscape"],
                 ["--layout", "filmstrip", "--direction", "vertical", "--landscape"],
                 ["--layout", "strip", "--direction", "horizontal"]):
        pdf = red + ".pdf"
        assert cli.main([red, red, "-o", pdf, "--dpi", "40", *args]) == 0


def test_honeycomb_photos_are_hexagons_and_calendar_draws_text_and_grid(red):
    from photosheet.core.layouts import Calendar, Honeycomb
    pg = PageExtent(300, 300)
    pg.set_margins(10, 10, 10, 10)
    h = Honeycomb(page=pg, size=100, gutter=10, marks=True)
    h.add(ImageItem(red))
    im = render_page(h, 0, dpi=72)
    (_, r), = h.slots(0)[:1]
    cx, cy = int(r.x + r.w / 2), int(r.y + r.h / 2)
    assert im.getpixel((cx, cy)) == (255, 0, 0)                                       # photo in the middle
    assert im.getpixel((int(r.x + 3), int(r.y + 3))) == (255, 255, 255)                # the box's corner is cut off
    assert im.getpixel((int(r.x + r.w - 3), int(r.y + r.h / 2))) != (255, 255, 255)    # but the flat side is filled
    assert im.getpixel((cx, int(r.y + 4))) != (255, 255, 255)                          # and so is the top point
    pgc = PageExtent(500, 700)
    pgc.set_margins(20, 20, 20, 20)
    cal = Calendar(page=pgc, year=2024, month=2, photo_share=0.5)
    cal.add(ImageItem(red, crop=True, rotation=0))
    out = render_page(cal, 0, dpi=72)
    photo = cal.slots(0)[0][1]
    assert out.getpixel((int(photo.x + photo.w / 2), int(photo.y + photo.h / 2))) == (255, 0, 0)
    title = cal.captions(0)[0][1]
    ink = lambda box: out.crop(tuple(int(v) for v in box)).getextrema()
    assert ink((title.x, title.y, title.x + title.w, title.y + title.h))[0][0] < 100   # month title drawn in dark ink
    _, _, _, weeks, top, cw, ch = cal._geometry(0)
    sat = next(r for t, r, col in cal.captions(0) if t == "Sat")
    sat_ink = out.crop((int(sat.x), int(sat.y), int(sat.x + sat.w), int(sat.y + sat.h))).getextrema()
    assert sat_ink[0][0] > 150 and sat_ink[1][0] < 120                                  # weekend names are red (bright R, dark G)
    assert out.getpixel((int(pgc.left + 3 * cw), int(top + 2 * ch))) != (255, 255, 255)   # a grid line crosses here


def test_cli_for_bento_spiral_honeycomb_and_calendar_and_presets(red, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Bento, Calendar, Honeycomb, Spiral
    for args in (["--layout", "bento", "--template", "pinwheel"], ["--layout", "bento"],
                 ["--layout", "spiral", "--spiral-photos", "4", "--ccw"], ["--layout", "honeycomb", "--hex-size", "30", "--marks"],
                 ["--layout", "calendar", "--year", "2027", "--month", "11", "--months", "3", "--week-start", "sunday"]):
        out = tmp_path / "o.pdf"
        assert cli.main([red, red, red, "-o", str(out), "--dpi", "40", *args]) == 0 and out.stat().st_size > 0
    for lay in (Bento(page=PageExtent(300, 300), template="magazine", gutter=3),
                Spiral(page=PageExtent(300, 300), photos=7, share=0.55, clockwise=False),
                Honeycomb(page=PageExtent(300, 300), size=70, marks=True),
                Calendar(page=PageExtent(595, 842), year=2030, month=6, months=7, week_start="sunday", photo_share=0.4)):
        f = str(tmp_path / "p.json")
        preset.save(lay, f)
        back = preset.load(f)
        assert type(back) is type(lay) and back == lay


def test_captions_can_draw_accented_letters_and_calendar_names_ignore_the_system_language(monkeypatch):
    import calendar

    from photosheet.core import render
    from photosheet.core.layouts import Calendar, PageExtent as PE
    font = render.caption_font(24)
    if getattr(font, "path", None) is None:
        import pytest as _pytest
        _pytest.skip("no TrueType font installed here; Pillow's built-in font cannot draw accents")
    box = bytes(font.getmask("͸"))                                    # an unassigned code point draws the 'missing' box
    for ch in "äöüéñçßÅ":
        assert bytes(font.getmask(ch)) != box, ch                          # a real glyph, not the box
    german = [""] + ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                     "November", "Dezember"]
    monkeypatch.setattr(calendar, "month_name", german)                    # what a German desktop reports
    monkeypatch.setattr(calendar, "day_abbr", ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"])
    cal = Calendar(page=PE(595, 842), year=2027, month=3, months=1)
    caps = cal.captions(0)
    assert caps[0][0] == "March 2027" and [t for t, _, _ in caps[1:8]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def test_every_shape_masks_the_photo_to_its_outline(red):
    from photosheet.core.layouts import SHAPE_NAMES, Shapes
    probes = {   # (x, y) as a share of the cell: (inside, outside)
        "heart": ((.5, .5), (.5, .02)), "star": ((.5, .5), (.05, .05)), "diamond": ((.5, .5), (.08, .08)),
        "triangle": ((.5, .75), (.08, .08)), "rounded": ((.5, .5), (.0, .0)), "circle": ((.5, .5), (.04, .04)),
    }
    assert set(probes) == set(SHAPE_NAMES)
    pg = PageExtent(240, 240)
    for kind, (inside, outside) in probes.items():
        sh = Shapes(page=pg, rows=1, cols=1, gutter=0, kind=kind)
        sh.add(ImageItem(red))
        im = render_page(sh, 0, dpi=72)
        (_, r), = sh.slots(0)
        at = lambda f: im.getpixel((int(r.x + f[0] * r.w), int(r.y + f[1] * r.h)))
        assert at(inside) == (255, 0, 0), kind                                         # photo shows inside the shape
        assert at(outside) == (255, 255, 255), kind                                    # and is cut away outside it


def test_heart_has_a_notch_at_the_top_centre_and_two_lobes():
    from photosheet.core.render import _mask
    m = _mask("heart", 200, 200)
    assert m.getpixel((100, 8)) < 60                                                   # the dip between the lobes
    assert m.getpixel((50, 25)) > 200 and m.getpixel((150, 25)) > 200                  # the two lobes are filled
    assert m.getpixel((100, 190)) > 150                                                # the point at the bottom
    star = _mask("star", 200, 200)
    assert star.getpixel((100, 100)) > 200 and star.getpixel((100, 5)) > 100                                    # middle and the top tip
    assert any(star.getpixel((x, 196)) > 100 for x in range(30, 48)) and any(star.getpixel((x, 196)) > 100 for x in range(152, 170))   # both bottom tips reach the box
    assert star.getpixel((15, 190)) == 0 and star.getpixel((100, 190)) == 0                                     # but the gaps between them are empty


def test_scatter_draws_cards_with_shadows_on_the_page(red, tmp_path):
    from photosheet.core.layouts import Scatter
    pg = PageExtent(500, 500)
    pg.set_margins(10, 10, 10, 10)
    sc = Scatter(page=pg, photos=3, size=0.5, tilt=15, border=12, seed=3)
    for _ in range(3):
        sc.add(ImageItem(red))
    im = render_page(sc, 0, dpi=72)
    pile = sc.pile(0)
    top_item, top_photo, top_card, _ = pile[-1]
    assert im.getpixel((int(top_photo.x + top_photo.w / 2), int(top_photo.y + top_photo.h / 2))) == (255, 0, 0)   # photo
    px = im.getpixel((int(top_card.x + top_card.w / 2), int(top_card.y + top_card.h - 4)))
    assert px != (255, 0, 0) and min(px) > 200                                                                      # white bottom edge
    assert im.getpixel((2, 2)) == (255, 255, 255)                                                                   # page corner untouched
    gray = sum(1 for x in range(0, 500, 3) for y in range(0, 500, 3) if im.getpixel((x, y))[0] == im.getpixel((x, y))[1] != 255 and im.getpixel((x, y))[0] < 250)
    assert gray > 30                                                                                                # grey shadow pixels exist
    again = render_page(sc, 0, dpi=72)
    assert again.tobytes() == im.tobytes()                                                              # same seed, same picture
    sc.seed = 4
    assert render_page(sc, 0, dpi=72).tobytes() != im.tobytes()


def test_ring_masonry_cli_and_presets(red, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Masonry, Ring, Scatter, Shapes
    pg = PageExtent(400, 400)
    pg.set_margins(10, 10, 10, 10)
    ring = Ring(page=pg, photos=8, gutter=4)
    for _ in range(9):
        ring.add(ImageItem(red))
    im = render_page(ring, 0, dpi=72)
    for r in (c for _, c in ring.slots(0)):
        assert im.getpixel((int(r.x + r.w / 2), int(r.y + r.h / 2))) == (255, 0, 0)                 # every circle shows its photo
    one = Ring(page=pg, photos=8, center=False, gutter=4)                                            # a lone circle: its box corners are free
    one.add(ImageItem(red))
    lone = render_page(one, 0, dpi=72)
    (_, r), = one.slots(0)
    assert lone.getpixel((int(r.x + 2), int(r.y + 2))) == (255, 255, 255)                            # round, not square
    assert lone.getpixel((int(r.x + r.w / 2), int(r.y + r.h / 2))) == (255, 0, 0)
    for args in (["--layout", "masonry", "--cols", "3"], ["--layout", "shapes", "--shape", "star", "--rows", "2", "--cols", "2"],
                 ["--layout", "ring", "--ring-photos", "6", "--no-center", "--marks"], ["--layout", "ring"],
                 ["--layout", "scatter", "--scatter-photos", "3", "--tilt", "20", "--seed", "5", "--border", "6"]):
        out = tmp_path / "o.pdf"
        assert cli.main([red, red, red, "-o", str(out), "--dpi", "40", *args]) == 0 and out.stat().st_size > 0
    for lay in (Masonry(page=PageExtent(300, 300), columns=4, gutter=2), Shapes(page=PageExtent(300, 300), rows=2, cols=4, kind="diamond"),
                Ring(page=PageExtent(300, 300), photos=11, center=False, gutter=3, marks=True),
                Scatter(page=PageExtent(300, 300), photos=4, size=0.3, tilt=25, border=3, seed=99)):
        f = str(tmp_path / "p.json")
        preset.save(lay, f)
        back = preset.load(f)
        assert type(back) is type(lay) and back == lay


def test_slant_cuts_each_photo_to_its_parallelogram(tmp_path):
    from photosheet.core.layouts import Slant
    reds = []
    for i, c in enumerate(((255, 0, 0), (0, 160, 0), (0, 0, 255))):
        p = tmp_path / f"c{i}.png"
        Image.new("RGB", (300, 600), c).save(p)
        reds.append(str(p))
    pg = PageExtent(300, 400)
    s = Slant(page=pg, panels=3, slant=20, gutter=10)
    for p in reds:
        s.add(ImageItem(p))
    im = render_page(s, 0, dpi=72)
    for k, ((x0, y0), (x1, _), (x2, y2), (x3, _)) in enumerate(s.outlines(0)):
        mid_x = ((x0 + x3) / 2 + (x1 + x2) / 2) / 2                                    # the middle of the panel at half height
        assert im.getpixel((int(mid_x), 200)) == [(255, 0, 0), (0, 160, 0), (0, 0, 255)][k]
        assert im.getpixel((int(x0 + 4), int(y0 + 4))) == [(255, 0, 0), (0, 160, 0), (0, 0, 255)][k]   # top-left corner is filled
    b0 = s.slots(0)[0][1]                                                                # the first panel has no neighbour to its left
    assert im.getpixel((int(b0.x + 2), int(b0.y + 3))) == (255, 255, 255)               # top-left of its box is cut away (leans right)
    assert im.getpixel((int(b0.x + b0.w - 3), int(b0.y + b0.h - 3))) in ((255, 255, 255), (0, 160, 0))   # bottom-right: paper or the next panel
    p0, p1 = s.outlines(0)[:2]
    right0 = (p0[1][0] + p0[2][0]) / 2                                                   # panel 0's right edge at half height
    left1 = (p1[0][0] + p1[3][0]) / 2                                                    # panel 1's left edge at half height
    assert abs((left1 - right0) - 10) < 1e-6 and im.getpixel((int((right0 + left1) / 2), 200)) == (255, 255, 255)   # the gutter is paper


def test_fan_renders_tilted_cards_with_shadows_and_the_last_card_on_top(tmp_path):
    from photosheet.core.layouts import Fan
    cols = ((255, 0, 0), (0, 160, 0), (0, 0, 255))
    files = []
    for i, c in enumerate(cols):
        p = tmp_path / f"f{i}.png"
        Image.new("RGB", (300, 400), c).save(p)
        files.append(str(p))
    pg = PageExtent(500, 400)
    pg.set_margins(10, 10, 10, 10)
    f = Fan(page=pg, photos=3, spread=50, border=0.08)
    for p in files:
        f.add(ImageItem(p))
    im = render_page(f, 0, dpi=72)
    pile = f.pile(0)
    seen = {c: sum(1 for x in range(0, 500, 2) for y in range(0, 400, 2) if im.getpixel((x, y)) == c) for c in cols}
    assert all(n > 150 for n in seen.values()), seen                                     # every card shows its photo, even the covered ones
    top = pile[-1]
    assert im.getpixel((int(top[2].x + top[2].w / 2), int(top[2].y + top[2].h / 2))) == cols[-1]   # last card lies on top
    assert im.getpixel((2, 2)) == (255, 255, 255) and im.getpixel((497, 397)) == (255, 255, 255)
    assert render_page(f, 0, dpi=72).tobytes() == im.tobytes()                          # deterministic


def test_brick_and_timeline_render(tmp_path, red):
    from photosheet.core.layouts import Brick, Timeline
    b = Brick(page=PageExtent(300, 300), columns=3, rows=3, gutter=10)
    for _ in range(8):
        b.add(ImageItem(red, crop=True, rotation=0))
    im = render_page(b, 0, dpi=72)
    for _, r in b.slots(0):
        assert im.getpixel((int(r.x + r.w / 2), int(r.y + r.h / 2))) == (255, 0, 0)
    r0, r1 = b.slots(0)[0][1], b.slots(0)[3][1]
    assert r1.w < r0.w * 0.6 and abs(r1.x - r0.x) < 1e-9                                   # an odd row starts with a half tile at the edge...
    assert im.getpixel((int(r1.x + 2), int(r1.y + r1.h / 2))) == (255, 0, 0)               # ...and it is filled with photo, not a gap
    last = max((r for _, r in b.slots(0) if abs(r.y - r1.y) < 1e-9), key=lambda r: r.x)
    assert abs(last.x + last.w - 300) < 1e-9 and im.getpixel((int(last.x + last.w - 3), int(last.y + last.h / 2))) == (255, 0, 0)   # right edge too
    jp = []
    for i in range(3):
        p = tmp_path / f"t{i}.jpg"
        ex = Image.Exif()
        ex[306] = f"2024:0{i + 1}:15 10:00:00"
        Image.new("RGB", (240, 160), (255, 0, 0)).save(p, exif=ex)
        jp.append(str(p))
    pg = PageExtent(500, 700)
    pg.set_margins(20, 20, 20, 20)
    t = Timeline(page=pg, photos=3, gutter=12, caption_pt=12)
    for p in jp:
        t.add(ImageItem(p, rotation=0))
    out = render_page(t, 0, dpi=100)
    k = 100 / 72
    for (_, r), (text, band) in zip(t.slots(0), t.captions(0)):
        assert min(out.getpixel((int((r.x + r.w / 2) * k), int((r.y + r.h / 2) * k)))[:1]) >= 245     # the (red) photo; JPEG is not exact
        ink = out.crop((int(band.x * k), int(band.y * k), int((band.x + band.w) * k), int((band.y + band.h) * k))).getextrema()
        assert ink[0][0] < 140 and ink[1][0] < 140                                       # dark date text in the caption band
    dots = [r for kind, r, _ in t.shapes(0) if kind == "ellipse"]
    d = dots[0]
    px = out.getpixel((int((d.x + d.w / 2) * k), int((d.y + d.h / 2) * k)))
    assert px[2] > 150 and px[0] < 120                                                    # a blue dot on the line
    render_page(t, 0, dpi=30)                                                             # hairlines at low resolution must not crash


def test_cli_and_presets_for_slant_fan_brick_timeline(red, tmp_path):
    from photosheet.core import preset
    from photosheet.core.layouts import Brick, Fan, Slant, Timeline
    for args in (["--layout", "slant", "--panels", "4", "--slant", "-15"], ["--layout", "slant"],
                 ["--layout", "fan", "--fan-photos", "3", "--spread", "80", "--fan-border", "9"],
                 ["--layout", "brick", "--cols", "4", "--rows", "3"], ["--layout", "brick", "--cols", "1"],
                 ["--layout", "timeline", "--timeline-photos", "3"], ["--layout", "timeline", "--no-dates", "--landscape"]):
        out = tmp_path / "o.pdf"
        assert cli.main([red, red, red, "-o", str(out), "--dpi", "35", *args]) == 0 and out.stat().st_size > 0
    for lay in (Slant(page=PageExtent(300, 400), panels=7, slant=-22, gutter=3), Fan(page=PageExtent(400, 300), photos=8, spread=90, border=0.1),
                Brick(page=PageExtent(300, 300), columns=5, rows=2, gutter=1), Timeline(page=PageExtent(300, 400), photos=9, dates=False, gutter=2)):
        f = str(tmp_path / "p.json")
        preset.save(lay, f)
        back = preset.load(f)
        assert type(back) is type(lay) and back == lay


def test_cli_grid_defaults_follow_each_layout_unless_rows_and_cols_are_given(red, tmp_path):
    import subprocess
    import sys

    def pages(layout, n, *extra):
        d = tmp_path / f"{layout}{len(extra)}{n}"
        d.mkdir()
        subprocess.run([sys.executable, "-m", "src.cli", *([red] * n), "-o", str(d / "g.png"), "--layout", layout, "--dpi", "20", *extra],
                       check=True, capture_output=True)
        return len(list(d.glob("g*.png")))   # one page is saved as g.png, several as g-1.png, g-2.png ...
    assert pages("nup", 30) == 4                                  # 3 x 3 = 9 per page: 30 photos = 4 pages
    assert pages("nup", 30, "--rows", "5", "--cols", "5") == 2    # asked for 5 x 5 = 25 per page
    assert pages("brick", 40) == 2                                # Brick starts at 5 x 5 = 27 per page (half tiles at the row ends)
    assert pages("brick", 40, "--rows", "2", "--cols", "3") > 2
    assert pages("shapes", 13) == 3                               # Shapes 3 x 2 = 6 per page
    assert pages("masonry", 3) == 1 and pages("polaroid", 10) == 2


def test_cli_brick_defaults_to_landscape_paper_unless_asked_otherwise(red, tmp_path):
    import subprocess
    import sys

    def size(*extra):
        out = tmp_path / f"b{len(extra)}{''.join(extra).replace('-', '')}.png"
        subprocess.run([sys.executable, "-m", "src.cli", red, "-o", str(out), "--dpi", "36", *extra], check=True, capture_output=True)
        return Image.open(out).size
    portrait, landscape = (298, 421), (421, 298)                       # A4 at 36 dpi
    assert size("--layout", "brick") == landscape                     # Brick starts on landscape paper
    assert size("--layout", "brick", "--portrait") == portrait
    assert size("--layout", "brick", "--landscape") == landscape      # asking for landscape still works
    assert size("--layout", "nup") == portrait                        # every other layout is unchanged
    assert size("--layout", "nup", "--landscape") == landscape
    assert size("--layout", "shapes") == portrait and size("--layout", "masonry") == portrait


def test_carousel_feather_fades_the_overlap_and_round_trips(red, tmp_path):
    from PIL import Image
    from photosheet.core.layouts import Carousel
    from photosheet.core.preset import from_dict, to_dict
    blue = tmp_path / "b.png"
    Image.new("RGB", (50, 50), (0, 0, 255)).save(blue)
    c = Carousel(page=PageExtent(200, 200), segments=2, overlap=60, feather=20)
    c.add(ImageItem(red))
    c.add(ImageItem(str(blue)))
    px = render_page(c, 0, dpi=72).getpixel((100, 100 - 40))   # on the seam between the two wedges
    assert 0 < px[0] < 255 and 0 < px[2] < 255                 # a mix of red and blue
    assert from_dict(to_dict(c)).feather == 20


def test_feather_softens_edges_in_every_overlapping_layout(red):
    from photosheet.core.layouts import Badges, Bento, Collage, Fan, Mosaic, NUp, Scatter, Stairs
    for cls in (Collage, Scatter, Fan, Stairs, NUp, Mosaic, Bento, Badges):
        def dark_edge(feather):
            lay = cls(page=PageExtent(300, 300), feather=feather)
            lay.add(ImageItem(red))
            lay.add(ImageItem(red))
            im = render_page(lay, 0, dpi=72)
            return sum(n for n, p in im.getcolors(10**6) if p not in ((255, 0, 0), (255, 255, 255)) and p[0] > 200 and p[1] > 0)
        assert dark_edge(20) > dark_edge(0), cls.__name__   # more blended edge pixels (red mixed with the paper)
