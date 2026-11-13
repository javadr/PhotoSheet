"""Make `import photosheet` work from a fresh checkout: the package folder is src/ itself, so unless it has
been installed (pip install -e .) register src/ under the name photosheet."""
import importlib.util
import pathlib
import sys

_src = pathlib.Path(__file__).resolve().parent.parent / "src"
if importlib.util.find_spec("photosheet") is None:
    spec = importlib.util.spec_from_file_location("photosheet", _src / "__init__.py",
                                                  submodule_search_locations=[str(_src)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["photosheet"] = mod
    spec.loader.exec_module(mod)

import pytest


@pytest.fixture(autouse=True)
def _private_settings(tmp_path, monkeypatch):
    """Never read or write the developer's real settings.toml."""
    from photosheet import settings
    monkeypatch.setattr(settings, "path", lambda: tmp_path / "PhotoSheet" / "settings.toml")
