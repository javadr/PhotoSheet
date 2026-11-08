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
