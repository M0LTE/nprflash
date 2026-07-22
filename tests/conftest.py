import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def vectors():
    """Reference frames for the bootloader wire format, keyed by name."""
    data = json.loads((FIXTURES / "frames.json").read_text())
    return {v["name"]: v for v in data["vectors"]}
