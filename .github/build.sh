#!/bin/bash
# Builds the standalone Linux binary into dist/. Needs `pip install . pyinstaller` first (a regular install,
# not an editable one: PyInstaller must find the photosheet package in site-packages).
set -euo pipefail

version=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' src/__init__.py | head -1)
export CURRENT_VERSION="$version"

pyinstaller --onefile --windowed --clean --noupx -i src/icons/photosheet.ico \
	--collect-data photosheet .github/launcher.py -n "photosheet-v$version"

chmod +x "dist/photosheet-v$version"
