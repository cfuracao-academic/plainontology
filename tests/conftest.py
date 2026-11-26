import shutil
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def examples(tmp_path: Path) -> Path:
    """A private copy of the examples folder."""
    target = tmp_path / "examples"
    shutil.copytree(EXAMPLES, target)
    return target
