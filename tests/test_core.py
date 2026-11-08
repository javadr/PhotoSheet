import pytest
from photosheet.core.geometry import PageExtent, Rect, Rotation, fit
from photosheet.core.layouts import ImageItem, NUp, Poster, Single


def test_fit_inside_centres():
    f = fit(200, 100, Rect(0, 0, 100, 100), rotation=Rotation.NONE)
    assert (f.width, f.height, f.xpos, f.ypos) == (100, 50, 0, 25)


def test_fit_auto_rotates_landscape_into_portrait_slot():
    f = fit(200, 100, Rect(0, 0, 100, 200))
    assert f.rotation == 90 and (f.width, f.height) == (100, 200)


def test_fit_crop_covers_and_pans():
    f = fit(200, 100, Rect(0, 0, 100, 100), crop=True, rotation=Rotation.NONE, hpan=0.0)
    assert (f.width, f.height, f.xoffset) == (200, 100, 0)
    f = fit(200, 100, Rect(0, 0, 100, 100), crop=True, rotation=Rotation.NONE, hpan=1.0)
    assert f.xoffset == 100


def test_margins_rejected_when_no_area_left():
    p = PageExtent(width=100, height=100)
    assert not p.set_margins(60, 40, 0, 0)
    assert p.left == 0


def test_nup_grid_and_pagination():
    n = NUp(page=PageExtent(width=200, height=200), rows=2, cols=2, hgutter=0, vgutter=0)
    for i in range(5):
        n.add(ImageItem(f"{i}.jpg"))
    assert n.pages == 2
    assert [r for _, r in n.slots(0)][3] == Rect(100, 100, 100, 100)
    assert len(n.slots(1)) == 1


def test_nup_rejects_zero_grid():
    with pytest.raises(ValueError):
        NUp(rows=0)


def test_single_fit_scale_never_upscales():
    s = Single(page=PageExtent(width=100, height=100))
    assert s.fit_scale(50, 50) == 100.0
    assert s.fit_scale(200, 100) == 50.0


def test_poster_tiles_match_original_formula():
    # imageable 100x100, overlap 10, poster 250 -> ceil((250-10)/90) = 3 tiles
    p = Poster(page=PageExtent(width=100, height=100), poster_w=250, poster_h=100,
               hoverlap=10, voverlap=10)
    assert (p.htiles, p.vtiles) == (3, 1)
    p.add(ImageItem("a.jpg"))
    assert p.pages == 3
    _, _, win = p.tile_window(2)
    assert win.x == 180 and win.w == 70


def test_carousel_grows_segments_and_stays_one_page():
    from photosheet.core.layouts import Carousel
    c = Carousel(page=PageExtent(200, 200), segments=2)
    for i in range(3):
        c.add(ImageItem(f"{i}.jpg"))
    assert c.segments == 3 and c.pages == 1 and len(c.wedges()) == 3


def test_preset_roundtrip(tmp_path):
    from photosheet.core import preset
    n = NUp(page=PageExtent(300, 400), rows=3, cols=1)
    n.add(ImageItem("a.jpg", effects=[("Gamma", {"gamma": 1.2})]))
    preset.save(n, str(tmp_path / "p.json"))
    m = preset.load(str(tmp_path / "p.json"))
    assert (type(m), m.rows, m.cols, m.page.width) == (NUp, 3, 1, 300)
    assert m.images[0].effects == [("Gamma", {"gamma": 1.2})]
    with pytest.raises(ValueError):
        preset.from_dict({})
