import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def guide_ref_b64() -> str:
    return (FIXTURES / "guide_ref.txt").read_text().strip()


@pytest.fixture
def place_full() -> dict:
    return json.loads((FIXTURES / "place_full.json").read_text())


@pytest.fixture
def place_no_annotation() -> dict:
    return json.loads((FIXTURES / "place_no_annotation.json").read_text())
