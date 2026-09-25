import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FLOWER_DEMO"] = "1"  # never hit the network in tests
os.environ.pop("FLOWER_PASSWORD", None)


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from backend import cache, db
    from backend.main import app

    db.set_path(tmp_path / "test.db")
    cache.clear()
    return TestClient(app)
