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


@pytest.mark.parametrize("d", [{"type": "nup", "page": {"width": 1e7}}, {"type": "nup", "page": {"height": float("nan")}},
                               {"type": "nup", "page": {"width": 0}}, {"type": "poster", "poster_w": 1e9}])
def test_preset_rejects_unbounded_sizes(d):
    from photosheet.core import preset
    with pytest.raises(ValueError):   # would otherwise exhaust memory when rendered
        preset.from_dict(d)


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
    assert crop_overflow(200, 100, Rect(0, 0, 100, 200)) == (300, 0)                  # AUTO never turns a cropped photo
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

    from photosheet.core.layouts import MM, Badges
    pg = PageExtent(595, 842)
    pg.set_margins(20, 20, 20, 20)
    hexed, square = Badges(page=pg, diameter=38 * MM), Badges(page=pg, diameter=38 * MM, staggered=False)
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


# ---- Bento, Spiral, Honeycomb, Calendar ----

def test_bento_templates_tile_the_page_exactly_with_the_biggest_tile_first():
    from photosheet.core.layouts import BENTO, Bento
    pg = PageExtent(400, 600)
    for key, (label, cols, rows, cells) in BENTO.items():
        grid = [(x, y) for c, r, cs, rs in cells for x in range(c, c + cs) for y in range(r, r + rs)]
        assert len(grid) == len(set(grid)) == cols * rows, key                    # no gaps, no overlaps in the unit grid
        assert cells[0][2] * cells[0][3] == max(cs * rs for _, _, cs, rs in cells), key   # tile 1 is (one of) the biggest
        b = Bento(page=pg, template=key, gutter=0)
        for i in range(b.per_page * 2 + 1):
            b.add(ImageItem(f"{i}.jpg"))
        rects = [r for _, r in b.slots(0)]
        assert len(rects) == b.per_page and _rects_disjoint(rects), key
        assert abs(sum(r.w * r.h for r in rects) - 400 * 600) < 1e-6, key          # zero gutter: covers the whole page
        assert b.pages == 3 and len(b.slots(2)) == 1
    g = Bento(page=pg, template="six-tiles", gutter=10)
    for i in range(6):
        g.add(ImageItem(f"{i}.jpg"))
    gr = [r for _, r in g.slots(0)]
    assert _rects_disjoint(gr) and all(r.x >= -1e-6 and r.x + r.w <= 400 + 1e-6 and r.y + r.h <= 600 + 1e-6 for r in gr)
    for bad in (dict(template="nope"), dict(gutter=-1)):
        with pytest.raises(ValueError):
            Bento(**bad)


def test_spiral_photos_shrink_turn_and_fill_the_page_in_both_directions():
    from photosheet.core.layouts import Spiral
    pg = PageExtent(600, 400)
    cw = Spiral(page=pg, photos=5, gutter=4)
    ccw = Spiral(page=pg, photos=5, gutter=4, clockwise=False)
    for sp in (cw, ccw):
        for i in range(7):
            sp.add(ImageItem(f"{i}.jpg"))
        rects = [r for _, r in sp.slots(0)]
        assert len(rects) == 5 and _rects_disjoint(rects) and sp.pages == 2 and len(sp.slots(1)) == 2
        areas = [r.w * r.h for r in rects]
        assert areas[0] == max(areas) and areas[0] > areas[1] > areas[2]           # each is smaller than the one before
        assert all(r.x >= -1e-6 and r.y >= -1e-6 and r.x + r.w <= 600 + 1e-6 and r.y + r.h <= 400 + 1e-6 for r in rects)
        assert sum(areas) > 0.9 * 600 * 400                                          # the page is nearly full
    a, b = [r for _, r in cw.slots(0)], [r for _, r in ccw.slots(0)]
    assert a[1].y < b[1].y                                                           # clockwise goes up second, counter-clockwise down
    assert abs(a[0].w - 600 * 0.618) < 1e-9
    for bad in (dict(photos=1), dict(share=0.1), dict(share=0.9)):
        with pytest.raises(ValueError):
            Spiral(**bad)


def test_honeycomb_cells_nest_without_touching_and_stay_on_the_page():
    import math

    from photosheet.core.layouts import MM, Honeycomb
    pg = PageExtent(595, 842)
    pg.set_margins(20, 20, 20, 20)
    h = Honeycomb(page=pg, size=40 * MM, gutter=6)
    assert h.shape == "hexagon" and h.per_page >= 24
    for i in range(h.per_page + 2):
        h.add(ImageItem(f"{i}.jpg"))
    assert h.pages == 2 and len(h.slots(1)) == 2
    cs = [(r.x + r.w / 2, r.y + r.h / 2) for _, r in h.slots(0)]
    assert abs(h.cell_h - h.size * 2 / math.sqrt(3)) < 1e-9
    assert all(20 - 1e-6 <= r.x and r.x + r.w <= 575 + 1e-6 and 20 - 1e-6 <= r.y and r.y + r.h <= 822 + 1e-6 for _, r in h.slots(0))
    assert min(math.dist(a, c) for i, a in enumerate(cs) for c in cs[i + 1:]) >= h.size + 6 - 1e-6   # flat sides keep the gutter
    h.marks = True
    assert len(h.cut_guides(0)) == 6 * h.per_page
    assert Honeycomb(page=pg, size=3000).per_page == 0 and Honeycomb(page=pg, size=3000).pages == 1
    with pytest.raises(ValueError):
        Honeycomb(size=0)


def test_calendar_days_land_in_the_right_columns_and_weeks_start_where_asked():
    from photosheet.core.layouts import Calendar
    pg = PageExtent(595, 842)
    pg.set_margins(20, 20, 20, 20)
    c = Calendar(page=pg, year=2024, month=2, months=3)                              # Feb 2024: starts on a Thursday, 29 days
    assert c.pages == 3 and [c.month_of(i) for i in range(3)] == [(2024, 2), (2024, 3), (2024, 4)]
    caps = c.captions(0)
    assert caps[0][0] == "February 2024"
    assert [t for t, _, _ in caps[1:8]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days = {t: r for t, r, _ in caps[8:]}
    assert sorted(int(d) for d in days) == list(range(1, 30))                          # exactly 29 days
    head = {t: r for t, r, _ in caps[1:8]}
    assert abs((days["1"].x + days["1"].w / 2) - (head["Thu"].x + head["Thu"].w / 2)) < 1e-6   # 1 Feb sits under Thu
    assert abs((days["8"].x + days["8"].w / 2) - (days["1"].x + days["1"].w / 2)) < 1e-6      # the next Thursday is below it
    assert days["8"].y > days["1"].y
    colour = {t: col for t, _, col in caps}
    assert colour["Sat"][0] > 150 and colour["Sun"][0] > 150 and colour["Mon"][0] < 120       # weekends are red
    s = Calendar(page=pg, year=2024, month=2, week_start="sunday").captions(0)
    assert [t for t, _, _ in s[1:8]] == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    rolled = Calendar(page=pg, year=2026, month=11, months=4)
    assert [rolled.month_of(i) for i in range(4)] == [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]
    assert len(c._geometry(0)[3]) == 5 and len(Calendar(page=pg, year=2026, month=8)._geometry(0)[3]) == 6   # 5 vs 6 week rows


def test_calendar_cycles_photos_keeps_the_photo_above_the_grid_and_validates():
    from photosheet.core.layouts import Calendar
    pg = PageExtent(595, 842)
    c = Calendar(page=pg, year=2025, month=1, months=12, photo_share=0.5)
    assert c.slots(0) == [] and c.slots(5) == []                                       # no photos yet
    for n in ("a.jpg", "b.jpg", "c.jpg"):
        c.add(ImageItem(n))
    assert [c.slots(i)[0][0].path for i in range(5)] == ["a.jpg", "b.jpg", "c.jpg", "a.jpg", "b.jpg"]   # repeats after 3
    photo = c.slots(0)[0][1]
    assert abs(photo.h - 842 * 0.5) < 1e-9 and photo.w == 595
    assert min(r.y for _, r, _ in c.captions(0)) >= photo.y + photo.h                  # all text is below the photo
    assert c.slots(12) == [] and c.captions(12) == [] and c.shapes(12) == []
    assert len(c.shapes(0)) == 7 * len(c._geometry(0)[3])                              # one outlined cell per day slot
    for bad in (dict(month=0), dict(month=13), dict(months=0), dict(months=25), dict(week_start="friday"), dict(photo_share=0.9)):
        with pytest.raises(ValueError):
            Calendar(**bad)


# ---- Masonry, Shapes, Ring, Scatter ----

def test_masonry_uses_the_shortest_column_keeps_shapes_and_starts_new_pages(tmp_path):
    from photosheet.core.layouts import Masonry
    pg = PageExtent(300, 400)
    m = Masonry(page=pg, columns=3, gutter=0)
    assert m.pages == 1 and m.slots(0) == []                                        # no photos: one empty page
    for i, (w, h) in enumerate([(100, 200), (100, 100), (100, 100), (100, 100), (100, 100)]):
        m.add(_img(tmp_path, f"{i}.png", w, h))
    rects = [r for _, r in m.slots(0)]
    assert len(rects) == 5 and _rects_disjoint(rects)
    cols = sorted({round(r.x) for r in rects})
    assert cols == [0, 100, 200]                                                      # three equal columns
    assert [round(r.w / r.h, 3) for r in rects] == [0.5, 1.0, 1.0, 1.0, 1.0]         # every photo keeps its shape
    assert round(rects[1].x) == 100 and round(rects[2].x) == 200                      # 2nd and 3rd go to the empty columns
    assert round(rects[3].x) == 100 and round(rects[3].y) == 100                      # the next goes under the shortest column
    assert all(r.y + r.h <= 400 + 1e-6 for r in rects)
    for i in range(5, 14):
        m.add(_img(tmp_path, f"{i}.png", 100, 100))
    assert m.pages >= 2 and all(r.y + r.h <= 400 + 1e-6 for p in range(m.pages) for _, r in m.slots(p))
    assert sum(len(m.slots(p)) for p in range(m.pages)) == 14                         # nothing lost across pages
    tall = Masonry(page=pg, columns=1, gutter=0)
    tall.add(_img(tmp_path, "tall.png", 10, 1000))
    assert tall.slots(0)[0][1].h <= 400 + 1e-6                                        # a too-tall photo is capped to the page
    for bad in (dict(columns=0), dict(gutter=-1)):
        with pytest.raises(ValueError):
            Masonry(**bad)


def test_shapes_grid_of_squares_centred_with_validation():
    from photosheet.core.layouts import SHAPE_NAMES, Shapes
    pg = PageExtent(400, 600)
    s = Shapes(page=pg, rows=3, cols=2, gutter=10, kind="star")
    for i in range(8):
        s.add(ImageItem(f"{i}.jpg"))
    rects = [r for _, r in s.slots(0)]
    assert len(rects) == 6 and s.pages == 2 and len(s.slots(1)) == 2 and _rects_disjoint(rects)
    assert all(abs(r.w - r.h) < 1e-9 for r in rects) and len({round(r.w, 6) for r in rects}) == 1   # equal squares
    assert min(r.x for r in rects) >= -1e-6 and max(r.x + r.w for r in rects) <= 400 + 1e-6
    left, right = min(r.x for r in rects), 400 - max(r.x + r.w for r in rects)
    assert abs(left - right) < 1e-6                                                   # centred across
    assert s.shape == "star" and Shapes(kind="circle").shape == "ellipse"
    assert [Shapes(kind=k).shape for k in SHAPE_NAMES if k != "circle"] == [k for k in SHAPE_NAMES if k != "circle"]
    for bad in (dict(kind="blob"), dict(rows=0), dict(cols=0), dict(gutter=-1)):
        with pytest.raises(ValueError):
            Shapes(**bad)


def test_ring_circles_do_not_overlap_fit_the_page_and_the_centre_is_biggest():
    import math

    from photosheet.core.layouts import Ring
    pg = PageExtent(595, 842)
    pg.set_margins(20, 20, 20, 20)
    for n in (3, 5, 8, 12, 24):
        r = Ring(page=pg, photos=n, gutter=6)
        cs = r._circles()
        assert len(cs) == r.per_page and all(abs(c.w - c.h) < 1e-9 for c in cs)
        ring = cs[1:] if len(cs) == n + 1 else cs
        centres = [(c.x + c.w / 2, c.y + c.h / 2) for c in cs]
        d = min(math.dist(a, b) - (ca.w + cb.w) / 2 for i, (a, ca) in enumerate(zip(centres, cs))
                for b, cb in zip(centres[i + 1:], cs[i + 1:]))
        assert d >= 6 - 1e-6, (n, d)                                                  # nothing touches, centre included
        assert all(20 - 1e-6 <= c.x and c.x + c.w <= 575 + 1e-6 and 20 - 1e-6 <= c.y and c.y + c.h <= 822 + 1e-6 for c in cs)
        assert (len(cs) == n + 1) == (n >= 6)                                         # a centre only when it is not tiny
        assert r.center_left_out == (n < 6)
        if len(cs) == n + 1:
            assert cs[0].w >= ring[0].w - 1e-9                                        # the middle photo is never smaller
    r = Ring(page=pg, photos=8)
    assert r.per_page == 9 and Ring(page=pg, photos=8, center=False).per_page == 8
    assert Ring(page=pg, photos=4).per_page == 4 and not Ring(page=pg, photos=4, center=False).center_left_out
    for i in range(20):
        r.add(ImageItem(f"{i}.jpg"))
    assert r.pages == 3 and len(r.slots(0)) == 9 and len(r.slots(2)) == 2
    first = r._circles()[1]
    assert abs((first.x + first.w / 2) - 297.5) < 1e-6 and first.y < r._circles()[0].y   # ring photo 1 sits at the top
    assert r.shape == "ellipse" and r.cut_guides(0) == []
    r.marks = True
    assert len(r.cut_guides(0)) == 48 * 9
    for bad in (dict(photos=2), dict(photos=37), dict(gutter=-1)):
        with pytest.raises(ValueError):
            Ring(**bad)


def test_scatter_is_repeatable_stays_on_the_page_and_the_seed_changes_the_pile(tmp_path):
    import math

    from photosheet.core.layouts import Scatter
    pg = PageExtent(595, 842)
    pg.set_margins(20, 20, 20, 20)
    items = [_img(tmp_path, f"{i}.png", 300 + 40 * i, 200) for i in range(8)]
    a, b = Scatter(page=pg, photos=5, seed=7), Scatter(page=pg, photos=5, seed=7)
    for sc in (a, b):
        for it in items:
            sc.add(it)
    assert a.pages == 2 and len(a.slots(0)) == 5 and len(a.slots(1)) == 3
    assert [(r.x, r.y, ang) for _, r, _, ang in a.pile(0)] == [(r.x, r.y, ang) for _, r, _, ang in b.pile(0)]   # same seed, same pile
    c = Scatter(page=pg, photos=5, seed=8)
    for it in items:
        c.add(it)
    assert [(r.x, r.y) for _, r, _, _ in a.pile(0)] != [(r.x, r.y) for _, r, _, _ in c.pile(0)]
    assert [ang for *_, ang in a.pile(0)] != [ang for *_, ang in a.pile(1)]            # each page its own pile
    for item, photo, card, ang in a.pile(0) + a.pile(1):
        assert abs(ang) <= 12 + 1e-9
        assert abs(photo.w / photo.h - aspect(item)) < 1e-9                            # the photo keeps its shape
        assert card.x < photo.x and card.y < photo.y and card.y + card.h - (photo.y + photo.h) > photo.y - card.y   # thicker bottom
        t = math.radians(abs(ang))
        hw, hh = (card.w * math.cos(t) + card.h * math.sin(t)) / 2, (card.w * math.sin(t) + card.h * math.cos(t)) / 2
        cx, cy = card.x + card.w / 2, card.y + card.h / 2
        assert cx - hw >= 20 - 1e-6 and cx + hw <= 575 + 1e-6 and cy - hh >= 20 - 1e-6 and cy + hh <= 822 + 1e-6   # tilted card stays inside
    flat = Scatter(page=pg, photos=3, tilt=0)
    flat.add(items[0])
    assert flat.pile(0)[0][3] == 0
    for bad in (dict(photos=0), dict(size=0.05), dict(size=0.95), dict(tilt=-1), dict(tilt=60), dict(border=-1), dict(spread=-.1), dict(spread=1.1)):
        with pytest.raises(ValueError):
            Scatter(**bad)
    centres = lambda lay: [(c.x + c.w / 2, c.y + c.h / 2) for _, _, c, _ in lay.pile(0)]
    spans = {}
    for sp in (1.0, 0.5, 0.0):
        lay = Scatter(page=pg, photos=3, spread=sp)
        for it in items:
            lay.add(it)
        xs, ys = zip(*centres(lay))
        spans[sp] = (max(xs) - min(xs)) + (max(ys) - min(ys))
        for _, _, card, _ in lay.pile(0):                                          # still on the page at every spread
            assert card.x >= 20 - 1e-6 and card.x + card.w <= 575 + 1e-6
    assert spans[1.0] > spans[0.5] > spans[0.0] and spans[0.0] < 1e-6             # 0 stacks them all in the middle
    big = Scatter(page=pg, photos=12, size=0.25, even=True, tilt=0)                # fills the page: every quarter holds photos
    for i in range(12):
        big.add(ImageItem(f"{i}.png"))
    cs = [((c.x + c.w / 2 - 20) / 555, (c.y + c.h / 2 - 20) / 802) for _, _, c, _ in big.pile(0)]
    assert len({(int(x * 2), int(y * 2)) for x, y in cs}) == 4


def aspect(item):
    from photosheet.core.layouts import aspect as _a
    return _a(item)


# ---- Slant, Fan, Brick, Timeline ----

def _jpeg(tmp_path, name, w=60, h=40, when=None, ifd=False):
    from PIL import Image
    p = tmp_path / name
    im = Image.new("RGB", (w, h), (90, 120, 200))
    if when:
        ex = Image.Exif()
        if ifd:
            ex.get_ifd(0x8769)[36867] = when
        else:
            ex[306] = when
        im.save(p, exif=ex)
    else:
        im.save(p)
    return ImageItem(str(p))


def test_slant_panels_are_parallelograms_that_keep_the_gutter_and_fit_the_page():
    import math

    from photosheet.core.layouts import Slant
    pg = PageExtent(600, 800)
    pg.set_margins(20, 20, 20, 20)
    for angle in (14, -14, 0, 40):
        s = Slant(page=pg, panels=4, slant=angle, gutter=10)
        for i in range(6):
            s.add(ImageItem(f"{i}.jpg"))
        polys = s.outlines(0)
        assert len(polys) == 4 and s.pages == 2 and len(s.outlines(1)) == 2
        for poly in polys:
            (ax, ay), (bx, by), (cx, cy), (dx, dy) = poly
            assert ay == by == 20 and cy == dy == 780                               # panels run the full height
            assert abs((bx - ax) - (cx - dx)) < 1e-9                                # opposite sides equal: a parallelogram
            assert all(20 - 1e-6 <= x <= 580 + 1e-6 for x, _ in poly)                # inside the page
        gaps = [polys[i + 1][0][0] + (polys[i + 1][3][0] - polys[i + 1][0][0]) / 2 - (polys[i][1][0] + (polys[i][2][0] - polys[i][1][0]) / 2)
                for i in range(3)]
        assert all(abs(g - (gaps[0])) < 1e-6 for g in gaps) and gaps[0] >= 10 - 1e-6   # evenly spaced, gutter kept
        rects = [r for _, r in s.slots(0)]
        assert all(r.x >= 20 - 1e-6 and r.x + r.w <= 580 + 1e-6 for r in rects)
    wide = Slant(page=pg, panels=4, slant=40, gutter=10)                                    # too much lean for a portrait page
    assert wide.slant_limited and 20 < wide.effective_slant < 40
    assert abs(wide.effective_slant - math.degrees(math.atan(0.6 * 560 / 760))) < 1e-9
    assert all(abs(abs(p[1][0] - p[0][0]) - (0.4 * 560 - 3 * 10) / 4) < 1e-9 for p in wide._polys())   # what is left after the lean and gutters
    assert not Slant(page=pg, panels=4, slant=14).slant_limited and Slant(page=pg, slant=-40).effective_slant < 0
    right, left = Slant(page=pg, slant=20), Slant(page=pg, slant=-20)
    assert right.outlines(0) == [] and right._polys()[0][0][0] > right._polys()[0][3][0]   # leans right: the top is further right
    assert left._polys()[0][0][0] < left._polys()[0][3][0]                                  # leans left
    for bad in (dict(panels=1), dict(panels=13), dict(slant=41), dict(slant=-41), dict(gutter=-1)):
        with pytest.raises(ValueError):
            Slant(**bad)


def test_fan_cards_pivot_symmetrically_fill_the_page_and_the_last_card_is_on_top():
    import math

    from photosheet.core.layouts import Fan
    pg = PageExtent(842, 595)
    pg.set_margins(24, 24, 24, 24)
    f = Fan(page=pg, photos=5, spread=60, border=0.06)
    assert f.pile(0) == []                                                           # no photos yet
    for i in range(7):
        f.add(ImageItem(f"{i}.jpg"))
    assert f.pages == 2 and len(f.pile(0)) == 5 and len(f.pile(1)) == 2
    pile = f.pile(0)
    angles = [a for *_, a in pile]
    assert angles == sorted(angles, reverse=True) and abs(angles[0] + angles[-1]) < 1e-9 and abs(angles[0] - 30) < 1e-9   # symmetric, 60 apart
    assert abs(angles[2]) < 1e-9                                                     # the middle card stands straight
    cards = [c for _, _, c, _ in pile]
    assert all(abs(c.w / c.h - 0.72) < 1e-9 for c in cards) and len({round(c.w, 6) for c in cards}) == 1   # one card shape
    for item, photo, card, ang in pile:
        assert card.x < photo.x and card.y < photo.y and photo.x + photo.w < card.x + card.w
        assert card.y + card.h - (photo.y + photo.h) > photo.y - card.y              # thicker bottom edge, like an instant print
        t = math.radians(abs(ang))
        hw, hh = (card.w * math.cos(t) + card.h * math.sin(t)) / 2, (card.w * math.sin(t) + card.h * math.cos(t)) / 2
        cx, cy = card.x + card.w / 2, card.y + card.h / 2
        assert cx - hw >= 24 - 1e-6 and cx + hw <= 818 + 1e-6 and cy - hh >= 24 - 1e-6 and cy + hh <= 571 + 1e-6   # tilted card fits
    xs = [c.x + c.w / 2 for c in cards]
    assert xs == sorted(xs)                                                          # left to right, so the right-most is on top
    assert abs((xs[0] + xs[-1]) / 2 - 421) < 1e-6                                    # the fan is centred on the page
    one = Fan(page=pg, photos=5)
    one.add(ImageItem("a.jpg"))
    assert one.pile(0)[0][3] == 0
    for bad in (dict(photos=0), dict(photos=13), dict(spread=5), dict(spread=130), dict(border=0.01), dict(border=0.2)):
        with pytest.raises(ValueError):
            Fan(**bad)


def test_brick_rows_are_staggered_with_half_tiles_so_every_row_fills_the_page_width():
    from photosheet.core.layouts import Brick
    pg = PageExtent(400, 600)
    b = Brick(page=pg, columns=3, rows=4, gutter=10)
    assert b.per_page == 3 + 4 + 3 + 4                                               # odd rows: half, 2 whole, half
    for i in range(16):
        b.add(ImageItem(f"{i}.jpg"))
    assert b.pages == 2 and len(b.slots(0)) == 14 and len(b.slots(1)) == 2
    rects = [r for _, r in b.slots(0)]
    assert _rects_disjoint(rects)
    rows = {}
    for r in rects:
        rows.setdefault(round(r.y, 6), []).append(r)
    assert [len(v) for v in rows.values()] == [3, 4, 3, 4]
    for k, row in enumerate(rows.values()):
        row.sort(key=lambda r: r.x)
        assert abs(row[0].x) < 1e-9 and abs(row[-1].x + row[-1].w - 400) < 1e-9, k   # every row reaches both page edges
        assert all(abs((b2.x - (a.x + a.w)) - 10) < 1e-9 for a, b2 in zip(row, row[1:])), k   # gutters are even, half tiles too
    whole = rows[min(rows)][0].w
    for k in (1, 3):                                                                  # the shifted rows
        row = rows[sorted(rows)[k]]
        halves = (row[0], row[-1])
        assert all(abs(h.w - (whole - 10) / 2) < 1e-9 for h in halves)                # half tiles: half a whole tile less half a gutter
        assert all(abs(m.w - whole) < 1e-9 for m in row[1:-1])
        assert abs((row[1].x + row[1].w / 2) - (rows[min(rows)][0].x + rows[min(rows)][0].w + 5)) < 1e-9   # whole tiles sit across the seam above
    assert all(abs(r.h - rects[0].h) < 1e-9 for r in rects)
    assert abs(rects[0].y) < 1e-9 and abs(rects[-1].y + rects[-1].h - 600) < 1e-6    # and the rows fill the page height
    assert Brick(page=pg, columns=2, rows=2, gutter=8).per_page == 5                  # the smallest brick: 2 + (half, 1, half)
    assert Brick().columns == 5 and Brick().rows == 5
    huge = Brick(page=pg, columns=5, rows=2, gutter=300)                              # a gutter wider than a tile cannot go negative
    assert all(r.w >= 1 and r.h >= 1 for _, r in huge.slots(0)) or huge.slots(0) == []
    for bad in (dict(columns=1), dict(rows=0), dict(gutter=-1)):
        with pytest.raises(ValueError):
            Brick(**bad)



def test_exif_dates_read_format_fall_back_and_sort(tmp_path):
    import datetime

    from photosheet.core.layouts import date_sort_key, date_text, taken
    a = _jpeg(tmp_path, "paris.jpg", when="2024:03:02 10:00:00")                       # DateTime
    b = _jpeg(tmp_path, "rome.jpg", when="2023:12:25 08:30:00", ifd=True)               # DateTimeOriginal
    c = _jpeg(tmp_path, "nodate.jpg")
    assert taken(a) == datetime.datetime(2024, 3, 2, 10, 0) and taken(b) == datetime.datetime(2023, 12, 25, 8, 30)
    assert taken(c) is None and taken(ImageItem("/nonexistent.jpg")) is None            # no EXIF / missing file: no date, no crash
    assert date_text(a) == "2 Mar 2024" and date_text(b) == "25 Dec 2023"              # fixed English month names
    assert date_text(c) == "nodate" and date_text(ImageItem("/x/y/Holiday 1.png")) == "Holiday 1"
    assert date_sort_key(b) < date_sort_key(a)                                          # the older photo sorts first
    ordered = sorted([a, c, b], key=date_sort_key)
    assert ordered[0] is b and ordered[1] is a                                          # undated falls back to the file's own time
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not a jpeg")
    assert taken(ImageItem(str(bad))) is None


def test_timeline_alternates_sides_keeps_order_and_captions_with_dates(tmp_path):
    from photosheet.core.layouts import Timeline
    pg = PageExtent(600, 800)
    pg.set_margins(20, 20, 20, 20)
    t = Timeline(page=pg, photos=6, gutter=10)
    items = [_jpeg(tmp_path, f"p{i}.jpg", when=f"2024:0{i + 1}:1{i} 10:00:00") for i in range(7)]
    for it in items:
        t.add(it)
    assert t.pages == 2 and len(t.slots(0)) == 6 and len(t.slots(1)) == 1
    rects = [r for _, r in t.slots(0)]
    mid = 300
    sides = [("left" if r.x + r.w / 2 < mid else "right") for r in rects]
    assert sides == ["left", "right"] * 3                                              # alternating around the line
    assert all(r.x + r.w <= mid - 6 + 1e-6 for r, s in zip(rects, sides) if s == "left")
    assert all(r.x >= mid + 6 - 1e-6 for r, s in zip(rects, sides) if s == "right")    # nothing crosses the line
    ys = [r.y for r in rects]
    assert ys == sorted(ys)                                                            # top to bottom in list order
    left, right = rects[0::2], rects[1::2]
    assert _rects_disjoint(left) and _rects_disjoint(right)                            # same-side photos never overlap
    caps = t.captions(0)
    assert [c[0] for c in caps][:2] == ["10 Jan 2024", "11 Feb 2024"]                  # dates from the photos
    for (text, band), (_, r) in zip(caps, t.slots(0)):
        assert band.y >= r.y + r.h - 1e-6 and band.x == r.x                            # each caption sits under its photo
    shapes = t.shapes(0)
    assert sum(1 for k, _, _ in shapes if k == "ellipse") == 6                         # one dot per photo
    assert all(0 <= r.x and r.x + r.w <= 600 and 0 <= r.y and r.y + r.h <= 800 for _, r, _ in shapes)
    t.dates = False
    assert t.captions(0) == [] and all(abs(r.h - c.h) < 1e-6 for (_, r), (_, c, _) in zip(t.slots(0), t._cells(0)[0]))   # no caption band
    land = Timeline(page=PageExtent(842, 595), photos=5)
    for it in items[:5]:
        land.add(it)
    lr = [r for _, r in land.slots(0)]
    xs = [r.x for r in lr]
    assert xs == sorted(xs) and [("above" if r.y + r.h / 2 < 297.5 else "below") for r in lr] == ["above", "below"] * 2 + ["above"]
    solo = Timeline(page=pg)
    solo.add(items[0])
    (_, only), = solo.slots(0)
    assert abs(only.y + only.h / 2 - 400) < 80                                         # a lone photo sits mid-page
    assert Timeline(page=pg).slots(0) == [] and Timeline(page=pg).shapes(0) == []
    for bad in (dict(photos=0), dict(photos=13), dict(gutter=-1)):
        with pytest.raises(ValueError):
            Timeline(**bad)


def test_checker():
    from photosheet.core.geometry import PageExtent
    from photosheet.core.layouts import Checker, ImageItem
    c = Checker(page=PageExtent(400, 400), columns=4, rows=4, gutter=0)
    assert c.per_page == 8 and len(c.shapes(0)) == 8
    c.images = [ImageItem(f"{i}.jpg") for i in range(9)]
    assert c.pages == 2 and len(c.slots(1)) == 1
    assert Checker(columns=1, rows=1).per_page == 1
    e = Checker(page=PageExtent(400, 400), columns=3, rows=3, first_empty=True)
    assert e.per_page == 4 and e.slots(0) == [] and len(e.shapes(0)) == 5
    e.images = [ImageItem("a.jpg")]
    assert e.slots(0)[0][1] == Checker(page=PageExtent(400, 400), columns=3, rows=3).shapes(0)[0][1]
    try:
        Checker(columns=0)
        raise AssertionError
    except ValueError:
        pass


def test_stairs_steps_diagonally_inside_the_page():
    from photosheet.core.layouts import ImageItem, Stairs
    s = Stairs(page=PageExtent(400, 400), photos=3)
    for i in range(7):
        s.add(ImageItem(f"{i}.jpg"))
    r = [x[1] for x in s.slots(0)]
    assert s.pages == 3 and len(s.slots(2)) == 1
    assert r[0].x < r[1].x < r[2].x and r[0].y < r[1].y < r[2].y and r[1].x < r[0].x + r[0].w   # overlap
    assert max(q.x + q.w for q in r) <= 400 + 1e-6 and max(q.y + q.h for q in r) <= 400 + 1e-6
    u = Stairs(page=PageExtent(400, 400), photos=3, rising=True)
    u.images = s.images[:3]
    q = [x[1] for x in u.slots(0)]
    assert q[0].x < q[2].x and q[0].y > q[2].y and q[0].y == r[2].y and q[2].y == r[0].y   # mirrored top to bottom
    with pytest.raises(ValueError):
        Stairs(photos=0)


def test_pyramid_and_diamond_counts():
    from photosheet.core.layouts import Diamond, Pyramid
    assert Pyramid(page=PageExtent(300, 300), rows=4).per_page == 10
    d = Diamond(page=PageExtent(300, 300), rows=5)
    assert d.per_page == 9 and all(r.x >= 0 and r.x + r.w <= 300 for r in d._rects())


def test_preset_ignores_keys_from_a_newer_version(tmp_path):
    from photosheet.core import preset
    lay = NUp(page=PageExtent(), rows=2, cols=2)
    lay.add(ImageItem("a.png"))
    d = preset.to_dict(lay)
    d["page"]["future"] = 1
    d["images"][0]["future"] = 1
    assert preset.from_dict(d) == lay


def test_badges_bleed_leaves_a_white_margin_inside_the_cut_ring():
    from photosheet.core.layouts import Badges
    kw = dict(page=PageExtent(300, 300), diameter=60, gutter=10, staggered=False, marks=True, bleed=10,
              images=[ImageItem("a.jpg", 100, 100)])
    b = Badges(**kw)
    assert b.slots(0)[0][1].w == 80 and len(b.cut_guides(0)) == 96   # default: photo runs to the cut ring; face guide too
    assert Badges(fill_margin=False, **kw).slots(0)[0][1].w == 60 and len(Badges(fill_margin=False, **kw).cut_guides(0)) == 48   # cut ring + face guide


def test_honeycomb_fill_spans_the_page_edge_to_edge():
    from photosheet.core.layouts import MM, Honeycomb
    pg = PageExtent(595, 842)
    h = Honeycomb(page=pg, size=40 * MM, fill=True)
    for i in range(200):
        h.add(ImageItem(f"{i}.jpg"))
    rs = [r for _, r in h.slots(0)]
    assert min(r.x for r in rs) == pytest.approx(0) and max(r.x + r.w for r in rs) == pytest.approx(595)
    assert min(r.y for r in rs) == pytest.approx(0) and max(r.y + r.h for r in rs) == pytest.approx(842)


def test_honeycomb_half_cells_fill_the_edges_and_brick_can_drop_its_halves():
    from photosheet.core.layouts import MM, Brick, Honeycomb
    pg = PageExtent(595, 842)
    kw = dict(page=pg, size=40 * MM, fill=True)
    plain, halves = Honeycomb(**kw), Honeycomb(half_cells=True, **kw)
    assert halves.per_page > plain.per_page
    for i in range(300):
        halves.add(ImageItem(f"{i}.jpg"))
    rs = [r for _, r in halves.slots(0)]
    assert min(r.x for r in rs) >= -1e-6 and max(r.x + r.w for r in rs) <= 595 + 1e-6 and len(halves.outlines(0)) == len(rs)
    assert Brick(page=pg, half_cells=False).per_page < Brick(page=pg).per_page
    b = Brick(page=pg)
    b.add(ImageItem("a.jpg"))
    assert Brick.auto_rotate is False


def test_slant_zigzag_alternates_cuts_and_timeline_first_right_flips_sides():
    from photosheet.core.layouts import PageExtent, Slant, Timeline
    page = PageExtent(595, 842)
    plain, zig = Slant(page=page, panels=4), Slant(page=page, panels=4, zigzag=True)
    lean = lambda poly: poly[0][0] - poly[3][0]   # top x minus bottom x
    assert len({round(lean(p), 3) for p in plain._polys()}) == 1
    assert [lean(p) > 0 for p in zig._polys()] == [False, True, False, True] or [lean(p) > 0 for p in zig._polys()] == [True, False, True, False]
    assert all(min(p[1][0] - p[0][0], p[2][0] - p[3][0]) > 0 for p in zig._polys())
    pos = lambda lay: [c.x for _, c, _ in (setattr(lay, "images", ["a", "b"]), lay._cells(0)[0])[1]]
    a, b = pos(Timeline(page=page)), pos(Timeline(page=page, first_right=True))
    assert a[0] < a[1] and b[0] > b[1]   # portrait page: photo 1 left of the line by default, right with the option


# ---- Disc, Cassette, VHS, Disc cover ----
def test_disc_wedges_tile_and_round_trip():
    from photosheet.core import preset
    from photosheet.core.layouts import Disc, ImageItem
    pg = PageExtent(595, 842)
    d = Disc(page=pg, per_disc=3)
    assert d.per_page == 2 and d.cut_guides(0) == []
    for i in range(7):
        d.add(ImageItem(f"{i}.jpg"))
    assert d.pages == 2 and len(d.slots(0)) == 6 and len(d.slots(1)) == 1 and len(d.outlines(0)) == 6
    t = Disc(page=pg, per_disc=3, tile=True)
    for i in range(3):
        t.add(ImageItem(f"{i}.jpg"))
    assert t.pages == 2 and len(t.slots(0)) == 6 and {id(i) for i, _ in t.slots(0)[:3]} == {id(t.images[0])}
    assert preset.from_dict(preset.to_dict(t)).tile
    with pytest.raises(ValueError):
        Disc(hole=9999)


def test_case_modes_use_the_panels_as_described():
    from photosheet.core import preset
    from photosheet.core.layouts import MM, VHS, Cassette, DiscCover, ImageItem
    pg, land = PageExtent(595, 842), PageExtent(842, 595)

    def make(cls, n, **kw):
        c = cls(**{"page": pg, **kw})
        for i in range(n):
            c.add(ImageItem(f"{i}.jpg"))
        return c
    c = make(Cassette, 3)
    c.page = PageExtent(320, 320)                                  # room for one, upright
    assert abs(c.slots(0)[0][1].w / MM - 106.8) < 1e-6 and c.per_insert == 1   # spread: 104.8 mm of panels + two 1 mm gaps
    assert sum(1 for _ in make(Cassette, 1, mode="front").slots(0)) == 1
    two = make(Cassette, 2, mode="pair")
    assert two.per_insert == 2 and len(two.slots(0)) == 2 and two.slots(0)[0][0] is not two.slots(0)[1][0]   # spine stays blank
    assert make(Cassette, 3, mode="each").per_insert == 3
    outer = make(Cassette, 1, mode="outer")
    assert len(outer.slots(0)) == 1 and len(outer.outlines(0)[0]) == 12 and len(make(Cassette, 1).outlines(0)[0]) == 20
    assert make(VHS, 3, mode="each", page=land).slots(0)[1][1].w == pytest.approx(25 * MM)
    cd, dvd = make(DiscCover, 2, size="CD", mode="pair", page=land), make(DiscCover, 3, size="DVD", mode="each", page=land)
    assert [round(r.w / MM, 1) for _, r in cd.slots(0)] == [120, 137] and len(dvd.slots(0)) == 3
    assert abs((dvd.slots(0)[2][1].x + dvd.slots(0)[2][1].w - dvd.slots(0)[0][1].x) / MM - 275) < 1e-6   # 273 mm of panels + two 1 mm gaps
    assert len(make(DiscCover, 1, size="CD", mode="spread", page=land).cut_guides(0)) == 6 + 16   # three folds: booklet | spine | back | spine
    assert make(VHS, 3, mode="each").per_page == 0                          # 235 mm does not fit across portrait A4, and is never turned
    assert VHS(page=PageExtent(100, 100)).per_page == 0 and VHS(page=PageExtent(100, 100)).pages == 1
    each = make(Cassette, 3, mode="each", page=PageExtent(320, 320))
    a, b = each.slots(0)[0][1], each.slots(0)[1][1]
    assert abs(b.x - (a.x + a.w) - 1 * MM) < 1e-6                           # 1 mm between panels
    assert len(each.cut_guides(0)) == 4 + 12 and abs(each.cut_guides(0)[0][0] - (a.x + a.w + 0.5 * MM)) < 1e-6   # a tick above and below each fold, mid-gap
    with pytest.raises(ValueError):
        Cassette(mode="bogus")
    for lay in (c, outer, cd):
        assert type(preset.from_dict(preset.to_dict(lay))) is type(lay)
    assert preset.from_dict(preset.to_dict(cd)).size == "CD"


def test_inserts_on_a_page_have_room_between_them():
    from photosheet.core.layouts import MM, Cassette, ImageItem
    c = Cassette(page=PageExtent(595, 842), mode="each")
    for _ in range(6):
        c.add(ImageItem("a.jpg"))
    rects = [r for _, r in c.slots(0)]
    assert c.per_page == 2 and rects[3].y - (rects[0].y + rects[0].h) > 10 * MM   # one insert's last panel to the next one's first
