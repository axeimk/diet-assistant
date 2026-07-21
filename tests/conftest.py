from pathlib import Path

import pytest

from diet_assistant.db import initialize


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "data/diet.db"
    _ = initialize(path)
    return path
