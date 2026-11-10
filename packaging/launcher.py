"""PyInstaller entry point. src/__main__.py uses relative imports, so it cannot be built as a script."""
import sys

from photosheet.__main__ import main

sys.exit(main())
