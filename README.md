<p align="center"><img src="src/icons/photosheet.png" width="128" alt="PhotoSheet logo"></p>

# PhotoSheet

Put many photos on one printed sheet: passport photos, contact sheets, wallet prints, photo-booth strips,
posters and more. PhotoSheet previews the page, lets you arrange the photos with the mouse, and then
prints it or saves it as a PDF or PNG. Built with Python and Qt (PySide6).

## Features

**Layouts**

| Layout | What it is for |
|---|---|
| **N-up** | A grid of rows × columns. Optional contact-sheet captions (filename and/or number) and cut marks. |
| **Single** | One photo per page. |
| **Poster** | One photo tiled across several pages, with overlap. |
| **Carousel** | A circular montage with one wedge per photo. |
| **Fixed size** | Prints of one size (passport, wallet, 4 × 6, 5 × 7 …) packed to fill the page. |
| **Passport** | Copies of each photo filling a small sheet, like a photo shop: 8 passport photos in a 4 × 2 grid on a landscape 4 × 6 in. |
| **Mosaic** | Rows of mixed-shape photos, each shown whole and not cropped. |
| **Photo strip** | Booth-style strips with several frames each, outlined for cutting. Strips run sideways on a landscape page; the Direction setting can force either way. |
| **Polaroid** | Instant-photo prints: white cards with a thin border and a thick bottom edge, with optional captions. |
| **Triptych** | One photo split into 2 to 12 panels with a gap between them, side by side or stacked, on one page. |
| **Package** | Studio sheets that mix print sizes, such as one 5 × 7 plus four wallets on an 8 × 10. Prints are packed to use the sheet well. |
| **Collage** | Free-form: move and resize each photo with the mouse, with snapping guides, handles and bring-to-front. |
| **Framed print** | One photo per page on a wide white, black or cream mat, with a thin line around the picture and an optional caption. |
| **Badges** | Round photos for button badges, stickers or coasters (25 to 75 mm), staggered to fit more, with optional cut circles. |
| **Feature** | One large hero photo with a row or column of smaller photos, repeated page after page. |
| **Film strip** | 35 mm negative-style strips: black film with sprocket holes and 2:3 frames, several strips per page, vertical or horizontal (Direction setting). |
| **Bento** | Asymmetric tile grids that fill the page (hero-left, hero-top, wide, pinwheel, magazine, six tiles). |
| **Golden spiral** | Photos in a spiral, each taking about 62% of the space that is left. |
| **Honeycomb** | Hexagon-shaped photos nested like a honeycomb, with optional cut lines. |
| **Calendar** | A wall calendar: a photo above a month grid, one page per month, weekends in red. |

**Working with the page**

- Live preview that fits the window. Zoom with `Ctrl` + mouse wheel, `Ctrl` + `+` / `-`, or `Ctrl+0` to fit.
- Drag a photo on the page onto another to swap them. With *Crop all images to fill* on, dragging
  repositions the photo inside its frame instead (hold `Shift` to swap). Works on Carousel wedges too.
- In a **Collage**, drag a photo to move it and drag a corner to resize it. It snaps to the page edges, the centre
  and other photos (hold `Ctrl` to turn snapping off, `Shift` for a free shape).
- Undo and redo for adding, removing, reordering, moving and effect changes.
- Drop image files onto the window to add them.
- Paper sizes A4, Letter, A5, 4 × 6 in and 5 × 7 in, in portrait or landscape.

**Image handling**

- ICC colour management through Pillow's `ImageCms` (per-image or printer profile, rendering intent).
- Per-image gamma, warm/cool, sharpen, desaturate and flips.
- Save and open your setups as JSON presets.
- Export to PDF (multi-page) or PNG, or print through Qt.

## Install

Needs Python 3.10 or newer.

```sh
pip install -e .        # or:  make install-dev   (also adds a desktop launcher and icon)
photosheet              # start the app
```

Without installing, run it straight from the source tree: `make run`.

**Standalone downloads** (no Python needed) are attached to each [release](../../releases) when a `v*` tag is pushed:

| Platform | File |
|---|---|
| Windows | `photosheet-vX.Y.Z.exe` |
| Linux | `photosheet-vX.Y.Z` (built on a recent Ubuntu, so it needs a similarly recent distribution) |
| macOS | `photosheet-vX.Y.Z-macos.zip`, an Apple-silicon app that is not code-signed: right-click it and choose Open the first time |

## Command line

Render a sheet without opening the window:

```sh
# 8 passport photos on a landscape 4 x 6 in print
photosheet-cli me.jpg -o passport.pdf --layout passport --page 4x6 --landscape --margin 4 --crop

# A4 contact sheet, 4 x 5, with filenames and numbers
photosheet-cli photos/*.jpg -o contact.pdf --layout nup --rows 5 --cols 4 --caption both

# Photo-booth strips with 4 frames each
photosheet-cli photos/*.jpg -o strips.pdf --layout strip --frames 4

# A studio 8 x 10 sheet: one 5 x 7 and four wallets of each photo
photosheet-cli me.jpg -o package.pdf --layout package --page 8x10 --recipe 5x7-4wallets

# A 12-month wall calendar for 2027, one photo per month
photosheet-cli photos/*.jpg -o calendar.pdf --layout calendar --year 2027 --crop

# 32 mm badge graphics, staggered, with cut circles
photosheet-cli photos/*.jpg -o badges.pdf --layout badges --diameter 32 --marks

# Negative-strip look, 6 frames per strip
photosheet-cli photos/*.jpg -o film.pdf --layout filmstrip --frames 6

# Instant-photo prints, 2 x 3 per page, numbered
photosheet-cli photos/*.jpg -o polaroid.pdf --layout polaroid --rows 3 --cols 2 --caption number
```

`photosheet-cli --help` lists every option.

## Development

```sh
make test      # runs the test suite (the Qt tests run offscreen)
make help      # lists all targets
```

The layout and rendering code (`src/core/`) does not import Qt, so it can be tested and used on
its own. The window lives in `src/ui/`.
