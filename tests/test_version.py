"""Guard against `__version__`/`pyproject.toml` drift.

The 0.13.0 bump updated pyproject.toml's `version` but not the hardcoded
`__version__` in `src/nazca/__init__.py` (which `nazca --version` actually
reads), so a real 0.13.0 install still reported 0.12.0. This test fails loudly
the next time a bump commit only touches one of the two.
"""

from __future__ import annotations

import re
from pathlib import Path

import nazca

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_dunder_version_matches_pyproject():
    text = PYPROJECT.read_text()
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match, "couldn't find a `version = \"...\"` line in pyproject.toml"
    assert nazca.__version__ == match.group(1)
