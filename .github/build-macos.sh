#!/bin/bash
# Builds PhotoSheet.app and zips it into dist/. Needs `pip install . pyinstaller` first (a regular install).
# PyInstaller's one-file mode is deprecated for macOS apps, so this builds a normal .app bundle.
set -euo pipefail

version=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' src/__init__.py | head -1)
export CURRENT_VERSION="$version"

pyinstaller --windowed --clean --noupx -i src/icons/photosheet.icns \
	--osx-bundle-identifier io.github.photosheet \
	--collect-data photosheet .github/launcher.py -n PhotoSheet

# ditto keeps the bundle's symlinks and permissions, which plain zip would break.
ditto -c -k --keepParent dist/PhotoSheet.app "dist/photosheet-v$version-macos.zip"
