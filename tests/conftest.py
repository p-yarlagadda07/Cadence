import os
import random
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CADENCE_DB"] = str(Path(tempfile.mkdtemp()) / "import.db")

import app as cadence

PASSWORD = "Hyd@vnr26"
SHIFTED = {0, 3}
HOLDS = [95, 80, 110, 90, 85, 100, 120, 75, 105]
GAPS = [180, 140, 220, 160, 200, 150, 170, 190]


def typing(speed=1.0, jitter=0.04, seed=None, side=0, lead=90, lag=40, holds=HOLDS, gaps=GAPS):
    rng = random.Random(seed)
    t = 200.0
    keys = []
    for i in range(len(holds)):
        down = round(t, 1)
        up = round(t + holds[i] * speed * rng.gauss(1, jitter), 1)
        if i in SHIFTED:
            s_down = round(down - lead * rng.gauss(1, jitter), 1)
            s_up = round(up + lag * rng.gauss(1, jitter), 1)
            keys.append([down, up, s_down, s_up, side])
        else:
            keys.append([down, up])
        if i < len(gaps):
            t += gaps[i] * speed * rng.gauss(1, jitter)
    return keys


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cadence, "DB_PATH", tmp_path / "test.db")
    cadence.init_db()
    cadence.app.config["TESTING"] = True
    with cadence.app.test_client() as c:
        yield c


@pytest.fixture
def account(client):
    samples = [typing(seed=i) for i in range(8)]
    res = client.post("/api/enroll", json={"username": "poojitha", "password": PASSWORD, "samples": samples})
    return res.get_json()
