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


def _img(tmp_path, name, w, h):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (w, h)).save(p)
    return ImageItem(str(p))


def test_fixed_size_packs_page_centres_block_and_paginates():
    from photosheet.core.layouts import FixedSize
    f = FixedSize(page=PageExtent(width=220, height=320), cell_w=100, cell_h=150, hgutter=10, vgutter=10)
    assert (f.cols, f.rows, f.per_page) == (2, 2, 4)
    for i in range(5):
        f.add(ImageItem(f"{i}.jpg"))
    assert f.pages == 2 and len(f.slots(0)) == 4 and len(f.slots(1)) == 1
    first = f.slots(0)[0][1]
    assert first == Rect(5, 5, 100, 150)   # 210x310 block centred in 220x320


def test_fixed_size_rejects_bad_cell():
    from photosheet.core.layouts import FixedSize
    with pytest.raises(ValueError):
        FixedSize(cell_w=0)


def test_mosaic_rows_fill_width_and_keep_aspect(tmp_path):
    from photosheet.core.layouts import Mosaic
    m = Mosaic(page=PageExtent(width=400, height=600), row_height=100, gutter=0)
    for i, (w, h) in enumerate([(200, 100), (200, 100), (100, 200), (100, 100)]):
        m.add(_img(tmp_path, f"{i}.png", w, h))
    s = m.slots(0)
    # first row: aspects 2 + 2 -> 400 px of width at h=100 -> exactly fills the page width
    assert [round(r.w) for _, r in s[:2]] == [200, 200] and round(s[0][1].h) == 100
    for img, r in s:                       # every slot keeps its photo's shape
        assert abs(r.w / r.h - aspect_of(img)) < 1e-6
    assert m.pages == 1


def aspect_of(item):
    from photosheet.core.layouts import aspect
    return aspect(item)


def test_mosaic_overflow_starts_new_page_and_missing_file_is_square():
    from photosheet.core.layouts import Mosaic, aspect
    assert aspect(ImageItem("/nonexistent.jpg")) == 1.0
    m = Mosaic(page=PageExtent(width=100, height=100), row_height=80, gutter=0)
    for i in range(6):
        m.add(ImageItem(f"/nonexistent{i}.jpg"))   # squares: 2 per row, rows stretch to 50 -> 3 rows = 150 > 100
    assert m.pages == 2
    assert all(r.y + r.h <= 100 + 1e-6 for pg in range(m.pages) for _, r in m.slots(pg))


def test_nup_captions_reserve_a_band_and_number_across_pages():
    n = NUp(page=PageExtent(200, 200), rows=1, cols=2, hgutter=0, vgutter=0, caption="both", caption_pt=10)
    for i in range(3):
        n.add(ImageItem(f"/x/photo{i}.jpg"))
    cap = n.captions(1)                                    # page 2 holds image 3
    assert cap[0][0] == "3  photo2.jpg"
    (_, img_rect), (_, band) = n.slots(0)[0], n.captions(0)[0]
    assert abs(img_rect.h + band.h - 200) < 1e-6 and band.y == img_rect.y + img_rect.h
    assert NUp(caption="none").captions(0) == []
    with pytest.raises(ValueError):
        NUp(caption="bogus")


def test_marks_give_eight_ticks_per_photo_and_none_by_default():
    from photosheet.core.layouts import FixedSize
    f = FixedSize(page=PageExtent(200, 200), cell_w=80, cell_h=80)
    f.add(ImageItem("a.jpg"))
    assert f.cut_guides(0) == []
    f.marks = True
    assert len(f.cut_guides(0)) == 8


def test_strip_geometry_pagination_and_outlines():
    from photosheet.core.layouts import Strip
    s = Strip(page=PageExtent(300, 500), frames=3, strip_w=100, strip_h=300, padding=10, frame_gap=5, gutter=10)
    assert (s.cols, s.rows, s.per_page) == (2, 1, 6)
    for i in range(7):
        s.add(ImageItem(f"{i}.jpg"))
    assert s.pages == 2 and len(s.slots(0)) == 6 and len(s.slots(1)) == 1
    rects = [r for _, r in s.slots(0)]
    assert abs(rects[0].h - (300 - 20 - 10) / 3) < 1e-6 and rects[1].y > rects[0].y + rects[0].h   # stacked with a gap
    assert rects[3].x > rects[0].x + 100 - 1                                                       # 4th photo: second strip
    assert len(s.cut_guides(0)) == 8 and len(s.cut_guides(1)) == 4                                 # only strips in use
    with pytest.raises(ValueError):
        Strip(frames=40, strip_h=100)
