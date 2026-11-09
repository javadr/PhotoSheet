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
| **Photo strip** | Booth-style strips with several frames each, outlined for cutting. |

**Working with the page**

- Live preview that fits the window. Zoom with `Ctrl` + mouse wheel, `Ctrl` + `+` / `-`, or `Ctrl+0` to fit.
- Drag a photo on the page onto another to swap them. With *Crop all images to fill* on, dragging
  repositions the photo inside its frame instead (hold `Shift` to swap). Works on Carousel wedges too.
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

## Command line

Render a sheet without opening the window:

```sh
# 8 passport photos on a landscape 4 x 6 in print
photosheet-cli me.jpg -o passport.pdf --layout passport --page 4x6 --landscape --margin 4 --crop

# A4 contact sheet, 4 x 5, with filenames and numbers
photosheet-cli photos/*.jpg -o contact.pdf --layout nup --rows 5 --cols 4 --caption both

# Photo-booth strips with 4 frames each
photosheet-cli photos/*.jpg -o strips.pdf --layout strip --frames 4
```

`photosheet-cli --help` lists every option.

## Development

```sh
make test      # runs the test suite (the Qt tests run offscreen)
make help      # lists all targets
```

The layout and rendering code (`src/core/`) does not import Qt, so it can be tested and used on
its own. The window lives in `src/ui/`.
