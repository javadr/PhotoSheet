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
