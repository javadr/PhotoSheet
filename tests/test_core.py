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


def test_passport_four_by_two_on_landscape_4x6_like_a_photo_shop():
    from photosheet.core.layouts import MM, Passport
    page = PageExtent(432, 288)                       # 6 x 4 in landscape
    page.set_margins(4, 4, 4, 4)
    p = Passport(page=page, cell_w=35 * MM, cell_h=45 * MM, hgutter=4, vgutter=4)
    assert (p.cols, p.rows, p.per_page) == (4, 2, 8)  # the classic "8 passport photos on a 4x6"
    p.add(ImageItem("me.jpg"))
    assert p.pages == 1 and len(p.slots(0)) == 8      # fill sheet: one photo repeated into every slot
    assert all(img is p.images[0] for img, _ in p.slots(0))
    rects = [r for _, r in p.slots(0)]
    assert all(r.x >= 4 - 1e-6 and r.x + r.w <= 428 + 1e-6 and r.y >= 4 - 1e-6 and r.y + r.h <= 284 + 1e-6 for r in rects)


def test_passport_copies_and_sharing_the_sheet():
    from photosheet.core.layouts import MM, Passport
    page = PageExtent(432, 288)
    page.set_margins(4, 4, 4, 4)
    p = Passport(page=page, cell_w=35 * MM, cell_h=45 * MM, hgutter=4, vgutter=4)
    for n in ("a.jpg", "b.jpg"):
        p.add(ImageItem(n))
    assert [i.path for i, _ in p.slots(0)] == ["a.jpg"] * 4 + ["b.jpg"] * 4   # two people share one sheet
    p.copies = 6
    assert p.pages == 2 and len(p.slots(1)) == 4                              # 12 prints -> 8 + 4
    p.copies = 0
    p.images.clear()
    assert p.pages == 1 and p.slots(0) == []                                  # no photos: empty, no crash
    with pytest.raises(ValueError):
        Passport(copies=-1)


def test_crop_overflow_is_how_far_the_photo_sticks_out():
    from photosheet.core.geometry import crop_overflow
    assert crop_overflow(200, 100, Rect(0, 0, 100, 100), Rotation.NONE) == (100, 0)   # wide photo, square slot
    assert crop_overflow(100, 200, Rect(0, 0, 100, 100), Rotation.NONE) == (0, 100)
    assert crop_overflow(200, 100, Rect(0, 0, 100, 200), Rotation.NONE) == (300, 0)
    assert crop_overflow(200, 100, Rect(0, 0, 100, 200)) == (0, 0)                    # AUTO turns it to fit exactly
    assert crop_overflow(100, 100, Rect(0, 0, 50, 50)) == (0, 0)                      # same shape: nothing to move


# ---- Polaroid, Triptych, Package, Collage ----

def test_polaroid_cards_keep_proportions_thick_bottom_and_captions():
    from photosheet.core.layouts import Polaroid
    pg = PageExtent(400, 500)
    p = Polaroid(page=pg, rows=1, cols=2, hgutter=0, vgutter=0, border=10, bottom_ratio=3, caption="filename")
    p.add(ImageItem("/x/me.jpg"))
    (img, photo), = p.slots(0)
    card, = p.cards(0)
    assert abs(card.w / card.h - 88 / 107) < 1e-9                                  # real instant-film card shape
    assert photo.x - card.x == 10 and photo.y - card.y == 10                       # thin side/top border...
    assert abs((card.y + card.h) - (photo.y + photo.h) - 30) < 1e-9                # ...thick bottom: 3 x border
    (text, band), = p.captions(0)
    assert text == "me.jpg" and band.y >= photo.y + photo.h and band.y + band.h <= card.y + card.h
    assert Polaroid(page=pg, marks=False).cut_guides(0) == []
    with pytest.raises(ValueError):
        Polaroid(border=0)


def test_triptych_splits_one_photo_into_panels_with_gaps(tmp_path):
    from photosheet.core.layouts import Triptych
    t = Triptych(page=PageExtent(300, 400), panels=3, gap=10)
    t.add(_img(tmp_path, "wide.png", 300, 100))          # aspect 3:1
    group, panels = t.panels_for(0)
    assert abs(group.w / group.h - 3) < 1e-9 and group.w <= 300 + 1e-9             # keeps the photo's shape
    assert len(panels) == 3 and abs(sum(r.w for r in panels) + 20 - group.w) < 1e-9
    assert panels[1].x - (panels[0].x + panels[0].w) == 10                          # the gap
    assert t.pages == 1 and len(t.slots(0)) == 3 and t.slots(5) == []
    t.vertical = True
    _, vp = t.panels_for(0)
    assert vp[1].y > vp[0].y + vp[0].h and vp[0].x == vp[1].x
    t.add(_img(tmp_path, "b.png", 100, 100))
    assert t.pages == 2                                                              # one page per photo
    with pytest.raises(ValueError):
        Triptych(panels=0)


def test_package_recipes_all_fit_without_overlap_and_inside_the_page():
    from photosheet.core.layouts import RECIPES, Package
    for key, (label, paper, prints) in RECIPES.items():
        pg = PageExtent(*paper)
        pg.set_margins(4, 4, 4, 4)
        pk = Package(page=pg, prints=prints)
        pk.add(ImageItem("me.jpg"))
        rects = [r for _, r in pk.slots(0)]
        assert pk.missing == 0 and len(rects) == sum(n for _, _, n in prints), label
        for r in rects:
            assert r.x >= 4 - 1e-6 and r.y >= 4 - 1e-6 and r.x + r.w <= paper[0] - 4 + 1e-6 and r.y + r.h <= paper[1] - 4 + 1e-6, label
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                assert a.x + a.w <= b.x + 1e-6 or b.x + b.w <= a.x + 1e-6 or a.y + a.h <= b.y + 1e-6 or b.y + b.h <= a.y + 1e-6, label


def test_package_reports_what_does_not_fit_and_one_sheet_per_photo():
    from photosheet.core.layouts import MM, Package
    pk = Package(page=PageExtent(200, 200), prints=[(127, 177.8, 1)])              # a 5x7 on a 200 pt square
    pk.add(ImageItem("a.jpg"))
    assert pk.missing == 1 and pk.slots(0) == []
    pk2 = Package(page=PageExtent(576, 720), prints=[(63.5, 88.9, 4)])
    pk2.add(ImageItem("a.jpg"))
    pk2.add(ImageItem("b.jpg"))
    assert pk2.pages == 2 and all(i.path == "b.jpg" for i, _ in pk2.slots(1))
    sizes = sorted((round(r.w / MM, 1), round(r.h / MM, 1)) for _, r in pk2.slots(0))
    assert all(sorted(s) == [63.5, 88.9] for s in sizes)                           # each print keeps its size (maybe turned)
    with pytest.raises(ValueError):
        Package(prints=[])


def test_collage_places_photos_automatically_and_keeps_them_on_the_page(tmp_path):
    from photosheet.core.layouts import Collage
    c = Collage(page=PageExtent(400, 600))
    for i, (w, h) in enumerate([(300, 200), (200, 300), (300, 300)]):
        c.add(_img(tmp_path, f"{i}.png", w, h))
    slots = c.slots(0)
    assert c.pages == 1 and len(slots) == 3 and all(img.box for img, _ in slots)
    for _, r in slots:
        assert r.x >= -1e-6 and r.y >= -1e-6 and r.x + r.w <= 400 + 1e-6 and r.y + r.h <= 600 + 1e-6
    assert [round(r.w / r.h, 3) for _, r in slots] == [1.5, 0.667, 1.0]            # photos keep their shape
    late = _img(tmp_path, "late.png", 100, 100)
    c.add(late)
    c.slots(0)                                                                      # placed on the next layout
    assert late.box and c.images[0].box == slots[0][0].box                          # earlier photos stay put
    far = c.clamp((380, 580, 200, 100))
    assert far[0] + far[2] <= 400 and far[1] + far[3] <= 600                        # dragged off the page: pulled back
    big = c.clamp((0, 0, 1000, 500))
    assert abs(big[2] / big[3] - 2) < 1e-9 and big[2] <= 400                        # too big: shrunk, same shape


def test_snap_delta_and_collage_snap_targets(tmp_path):
    from photosheet.core.layouts import Collage, snap_delta
    assert snap_delta([10.0, 50.0], [12.0, 100.0], 3) == 2.0                        # left edge is 2 pt from a guide
    assert snap_delta([10.0], [20.0], 3) is None                                    # nothing close
    assert snap_delta([10.0, 50.0], [12.0, 48.5], 3) == -1.5                       # the closer of two matches wins
    c = Collage(page=PageExtent(400, 600))
    c.add(_img(tmp_path, "a.png", 100, 100))
    c.add(_img(tmp_path, "b.png", 100, 100))
    c.slots(0)
    xs, ys = c.snap_targets(skip=0)
    assert 0 in xs and 200 in xs and 400 in xs and 300 in ys                         # page edges and centre lines
    b = c.images[1].box
    assert b[0] in xs and b[0] + b[2] in xs                                          # the other photo's edges
    xs0, _ = c.snap_targets(skip=None)
    assert len(xs0) == len(xs) + 3                                                  # without skip, photo 0's 3 x-lines are included


# ---- Framed print, Badges, Feature, Film strip ----

def _rects_disjoint(rects):
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if not (a.x + a.w <= b.x + 1e-6 or b.x + b.w <= a.x + 1e-6 or a.y + a.h <= b.y + 1e-6 or b.y + b.h <= a.y + 1e-6):
                return False
    return True


def test_framed_print_mat_photo_caption_and_colours(tmp_path):
    from photosheet.core.layouts import MAT_COLORS, FramedPrint
    f = FramedPrint(page=PageExtent(400, 500), mat=40, caption="both")
    f.add(_img(tmp_path, "wide.png", 300, 100))
    (img, photo), = f.slots(0)
    card = f._card()
    assert photo.x >= card.x + 40 - 1e-6 and photo.x + photo.w <= card.x + card.w - 40 + 1e-6      # mat on both sides
    assert photo.y >= card.y + 40 - 1e-6 and abs(photo.w / photo.h - 3) < 1e-9                       # keeps the photo's shape
    (text, band), = f.captions(0)
    assert text == "1  wide.png" and band.y >= photo.y + photo.h and band.y + band.h <= card.y + card.h
    kinds = [k for k, _, _ in f.shapes(0)]
    assert kinds == ["card", "frame"] and f.shapes(0)[0][2] == MAT_COLORS["white"]
    assert FramedPrint(color="black").caption_color[0] > 200 and f.caption_color[0] < 100            # readable on either mat
    img.crop = True
    assert f.slots(0)[0][1] == f._opening()                                                          # cropped: fills the opening
    assert f.pages == 1 and f.slots(3) == []
    for bad in (dict(mat=-1), dict(color="pink"), dict(caption="x")):
        with pytest.raises(ValueError):
            FramedPrint(**bad)


def test_badges_circles_do_not_overlap_stay_on_the_page_and_stagger_fits_more():
    import math

    from photosheet.core.layouts import Badges
    pg = PageExtent(595, 842)
    pg.set_margins(20, 20, 20, 20)
    hexed, square = Badges(page=pg), Badges(page=pg, staggered=False)
    assert hexed.per_page > square.per_page >= 20
    for b in (hexed, square):
        for i in range(b.per_page + 3):
            b.add(ImageItem(f"{i}.jpg"))
        assert b.pages == 2 and len(b.slots(0)) == b.per_page and len(b.slots(1)) == 3
        cs = [(r.x + r.w / 2, r.y + r.h / 2) for _, r in b.slots(0)]
        d = b.diameter
        assert all(20 - 1e-6 <= c[0] - d / 2 and c[0] + d / 2 <= 575 + 1e-6 and 20 - 1e-6 <= c[1] - d / 2 and c[1] + d / 2 <= 822 + 1e-6 for c in cs)
        assert min(math.dist(a, c) for i, a in enumerate(cs) for c in cs[i + 1:]) >= d + b.gutter - 1e-6   # neighbours never touch
    assert Badges(page=pg, diameter=2000).per_page == 0 and Badges(page=pg, diameter=2000).pages == 1     # too big: empty, no crash
    assert Badges(page=pg).shape == "ellipse"
    hexed.marks = True
    assert len(hexed.cut_guides(0)) == 48 * hexed.per_page
    with pytest.raises(ValueError):
        Badges(diameter=0)


def test_feature_hero_and_thumbnails_on_every_page_both_orientations():
    from photosheet.core.layouts import Feature
    pg = PageExtent(400, 600)
    for side in (False, True):
        f = Feature(page=pg, thumbs=3, side=side, hero_share=0.6, gutter=10)
        for i in range(9):
            f.add(ImageItem(f"{i}.jpg"))
        assert f.per_page == 4 and f.pages == 3
        rects = [r for _, r in f.slots(0)]
        assert len(rects) == 4 and _rects_disjoint(rects)
        hero, thumbs = rects[0], rects[1:]
        assert hero.w * hero.h > 2 * max(t.w * t.h for t in thumbs)                          # the hero is clearly the biggest
        assert all(r.x >= -1e-6 and r.y >= -1e-6 and r.x + r.w <= 400 + 1e-6 and r.y + r.h <= 600 + 1e-6 for r in rects)
        assert len({round(t.w, 6) for t in thumbs}) == 1 and len({round(t.h, 6) for t in thumbs}) == 1
        assert (thumbs[0].x > hero.x + hero.w - 1e-6) if side else (thumbs[0].y > hero.y + hero.h - 1e-6)
        assert [i.path for i, _ in f.slots(2)] == ["8.jpg"]                                  # the last page holds the remainder
    for bad in (dict(thumbs=0), dict(hero_share=0.1), dict(hero_share=0.95)):
        with pytest.raises(ValueError):
            Feature(**bad)


def test_filmstrip_frames_are_2_to_3_holes_on_both_edges_and_it_fits_the_page():
    from photosheet.core.layouts import MM, Filmstrip
    pg = PageExtent(595, 842)
    pg.set_margins(20, 20, 20, 20)
    f = Filmstrip(page=pg, frames=6)
    assert abs(f.strip_w - 35 * MM) < 1e-6 and f.strip_h <= pg.imageable_h + 1e-6
    for i in range(7):
        f.add(ImageItem(f"{i}.jpg"))
    rects = [r for _, r in f.slots(0)]
    assert len(rects) == 7 and all(abs(r.h / r.w - 1.5) < 1e-9 for r in rects)               # 24 x 36 mm frames
    assert _rects_disjoint(rects)
    shapes = f.shapes(0)
    strips = [r for k, r, _ in shapes if k == "rect"]
    holes = [r for k, r, _ in shapes if k == "round"]
    assert len(strips) == 2 and f.cut_guides(0) == []                                         # 7 photos = 2 strips of 6
    assert len(holes) > 40 and all(any(s.x <= h.x and h.x + h.w <= s.x + s.w and s.y <= h.y and h.y + h.h <= s.y + s.h for s in strips) for h in holes)
    assert not any(h.x < r.x + r.w and h.x + h.w > r.x and h.y < r.y + r.h and h.y + h.h > r.y for h in holes for r in rects)   # holes stay clear of frames
    xs = {round(h.x - next(s.x for s in strips if s.x <= h.x < s.x + s.w), 3) for h in holes}
    assert len(xs) == 2                                                                         # one column of holes per edge
    tall = Filmstrip(page=pg, frames=12)                                                      # 12 frames would not fit: film is scaled down
    assert tall.strip_h <= pg.imageable_h * (1 + 1e-9) and tall.strip_w < 35 * MM
    with pytest.raises(ValueError):
        Filmstrip(page=pg, frames=0)


# ---- strip direction ----

def test_strip_direction_auto_follows_the_page_and_can_be_forced():
    from photosheet.core.layouts import Strip
    assert not Strip(page=PageExtent(300, 500)).horizontal                       # portrait page: vertical strips
    assert Strip(page=PageExtent(500, 300)).horizontal                           # landscape page: horizontal strips
    assert Strip(page=PageExtent(300, 500), direction="horizontal").horizontal
    assert not Strip(page=PageExtent(500, 300), direction="vertical").horizontal
    with pytest.raises(ValueError):
        Strip(direction="sideways")


def test_horizontal_strips_put_frames_side_by_side_and_tile_down_the_page():
    from photosheet.core.layouts import Strip
    pg = PageExtent(600, 400)
    s = Strip(page=pg, frames=3, strip_w=100, strip_h=300, padding=10, frame_gap=5, gutter=10)
    assert s.horizontal and (s.cols, s.rows, s.per_page) == (3, 1, 9)           # 3 strips stack down a 400 pt tall page
    for i in range(7):
        s.add(ImageItem(f"{i}.jpg"))
    rects = [r for _, r in s.slots(0)]
    assert len(rects) == 7 and _rects_disjoint(rects)
    first = rects[:3]
    assert first[0].y == first[1].y == first[2].y                               # one strip = one row of frames...
    assert first[1].x > first[0].x + first[0].w and first[2].x > first[1].x + first[1].w     # ...running left to right
    assert abs(first[0].h - (100 - 20)) < 1e-9 and abs(first[0].w - (300 - 20 - 10) / 3) < 1e-9   # across = strip_w, along = strip_h
    assert rects[3].y > rects[0].y + rects[0].h                                 # the next strip sits below
    assert all(r.x >= -1e-6 and r.y >= -1e-6 and r.x + r.w <= 600 + 1e-6 and r.y + r.h <= 400 + 1e-6 for r in rects)
    guides = s.cut_guides(0)
    assert len(guides) == 3 * 4                                                 # 7 photos = 3 strips, 4 outline lines each
    xs = [g[0] for g in guides] + [g[2] for g in guides]
    assert max(xs) - min(xs) <= 300 + 1e-6                                       # each outline is 300 long, flat on the page


def test_horizontal_filmstrip_has_landscape_frames_holes_in_the_film_and_fits_the_page_width():
    from photosheet.core.layouts import MM, Filmstrip
    pg = PageExtent(842, 595)
    pg.set_margins(20, 20, 20, 20)
    f = Filmstrip(page=pg, frames=6)                                             # landscape page: auto = horizontal
    assert f.horizontal and f.strip_h <= pg.imageable_w + 1e-6 and abs(f.strip_w - 35 * MM) < 1e-6
    for i in range(7):
        f.add(ImageItem(f"{i}.jpg"))
    rects = [r for _, r in f.slots(0)]
    assert all(abs(r.w / r.h - 1.5) < 1e-9 for r in rects) and _rects_disjoint(rects)       # 36 x 24 mm landscape frames
    shapes = f.shapes(0)
    strips = [r for k, r, _ in shapes if k == "rect"]
    holes = [r for k, r, _ in shapes if k == "round"]
    assert len(strips) == 2 and all(s.w > s.h for s in strips)                    # wide, flat black strips
    assert all(any(s.x <= h.x and h.x + h.w <= s.x + s.w and s.y <= h.y and h.y + h.h <= s.y + s.h for s in strips) for h in holes)
    assert not any(h.x < r.x + r.w and h.x + h.w > r.x and h.y < r.y + r.h and h.y + h.h > r.y for h in holes for r in rects)
    assert all(s.x >= 20 - 1e-6 and s.x + s.w <= 822 + 1e-6 for s in strips)
    long = Filmstrip(page=pg, frames=12)                                          # too long for the page width: scaled down
    assert long.strip_h <= pg.imageable_w * (1 + 1e-9) and long.strip_w < 35 * MM
    v = Filmstrip(page=pg, frames=6, direction="vertical")                        # forcing vertical on the same landscape page
    assert not v.horizontal and v.strip_h <= pg.imageable_h + 1e-6 and v.strip_w < 35 * MM
