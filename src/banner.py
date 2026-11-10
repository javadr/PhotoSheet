"""The colourful name banner shown by `--version`."""
from __future__ import annotations

from . import __version__

# figlet "slant", embedded so there is no figlet/pyfiglet dependency at run time.
ART = r"""
    ____  __          __       _____ __              __
   / __ \/ /_  ____  / /_____ / ___// /_  ___  ___  / /_
  / /_/ / __ \/ __ \/ __/ __ \\__ \/ __ \/ _ \/ _ \/ __/
 / ____/ / / / /_/ / /_/ /_/ /__/ / / / /  __/  __/ /_
/_/   /_/ /_/\____/\__/\____/____/_/ /_/\___/\___/\__/
"""

TAGLINE = "Print many photos per sheet"
START, END = (0, 200, 255), (255, 90, 200)   # cyan -> magenta, left to right


def _mix(t: float) -> str:
    r, g, b = (round(a + (z - a) * t) for a, z in zip(START, END))
    return f"rgb({r},{g},{b})"


def print_banner(console=None) -> None:
    """Print the name in a colour gradient, then the version. rich drops the colour when output is not a terminal."""
    import sys

    if console is None and sys.stdout is None:   # a windowed (no console) Windows build has no stdout at all
        return
    from rich.console import Console
    from rich.text import Text

    console = console or Console()
    lines = ART.strip("\n").splitlines()
    width = max(len(line) for line in lines)
    text = Text()
    for line in lines:
        for x, ch in enumerate(line):
            text.append(ch, style=f"bold {_mix(x / max(1, width - 1))}")
        text.append("\n")
    text.append(f"  {TAGLINE}  ", style="dim")
    text.append(f"v{__version__}", style=f"bold {_mix(1.0)}")
    console.print(text)
