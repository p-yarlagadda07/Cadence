import json
import re
import sqlite3

import app as cadence
from conftest import PASSWORD, typing

OTHER = dict(holds=[160, 60, 70, 150, 140, 60, 70, 140, 60], gaps=[120, 260, 110, 230, 130, 240, 120, 250])


def login(client, keys=None, password=PASSWORD, username="poojitha"):
    keys = keys if keys is not None else typing(seed=100)
    return client.post("/api/login", json={"username": username, "password": password, "keys": keys})


def try_test_page(client, label="owner", seed=1, **kw):
    return client.post("/api/test", json={"username": "poojitha", "password": PASSWORD,
                                          "keys": typing(seed=seed, **kw), "label": label})



def rows(sql):
    with sqlite3.connect(cadence.DB_PATH) as conn:
        return conn.execute(sql).fetchall()


def test_enroll_returns_a_recovery_code(account):
    assert account["ok"]
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", account["recovery_code"])
    assert 1.5 <= account["threshold"] <= 4.0


def test_enroll_rejects_short_passwords(client):
    res = client.post("/api/enroll", json={"username": "a", "password": "abc", "samples": []})
    assert res.status_code == 400


def test_enroll_rejects_taken_usernames(client, account):
    samples = [typing(seed=i) for i in range(8)]
    res = client.post("/api/enroll", json={"username": "poojitha", "password": PASSWORD, "samples": samples})
    assert res.status_code == 409


def test_enroll_rejects_a_broken_sample(client):
    samples = [typing(seed=i) for i in range(8)]
    samples[3] = samples[3][:-1]
    res = client.post("/api/enroll", json={"username": "x", "password": PASSWORD, "samples": samples})
    assert res.status_code == 400


def test_owner_gets_in_and_profile_grows(client, account):
    data = login(client).get_json()
    assert data["ok"], data
    assert data["alerts"] == []
    samples = json.loads(rows("SELECT samples FROM users")[0][0])
    assert len(samples) == account["kept"] + 1


def test_failed_logins_all_look_the_same(client, account):
    wrong_password = login(client, password="wrongpass")
    wrong_rhythm = login(client, keys=typing(seed=9, **OTHER))
    too_fast = login(client, keys=typing(speed=0.4, seed=7))
    for res in (wrong_password, wrong_rhythm, too_fast):
        data = res.get_json()
        assert res.status_code == 401
        assert set(data) == {"ok", "error", "attempts_left"}
        assert data["error"] == "Login failed."


def test_bad_timings_are_rejected_before_the_password_is_checked(client, account):
    keys = typing(seed=1)
    keys[2], keys[3] = keys[3], keys[2]
    right = login(client, keys=keys)
    wrong = login(client, keys=keys, password="wrongpass")
    assert right.status_code == wrong.status_code == 400
    assert right.get_json() == wrong.get_json()
    assert login(client, keys=typing(seed=1)[:-1]).status_code == 400


def test_blocked_attempt_with_right_password_becomes_an_alert(client, account):
    login(client, keys=typing(seed=9, side=1, **OTHER))
    login(client, password="wrongpass")
    data = login(client).get_json()
    assert data["ok"]
    assert len(data["alerts"]) == 1
    reasons = " ".join(data["alerts"][0]["reasons"])
    assert "right Shift for 'H'" in reasons
    assert login(client).get_json()["alerts"] == []


def test_alerts_never_store_password_characters(client, account):
    login(client, keys=typing(seed=9, side=1, **OTHER))
    stored = rows("SELECT details FROM alerts")[0][0]
    for ch in set(PASSWORD):
        assert f'"{ch}"' not in stored
        assert f"'{ch}'" not in stored


def test_five_failures_lock_the_account(client, account):
    for _ in range(5):
        login(client, password="wrongpass")
    res = login(client)
    assert res.status_code == 423
    assert res.get_json()["reason"] == "locked"


def test_recovery_code_unlocks_once(client, account):
    for _ in range(5):
        login(client, password="wrongpass")
    code = account["recovery_code"].lower().replace("-", " ")
    data = client.post("/api/unlock", json={"username": "poojitha", "code": code}).get_json()
    assert data["ok"]
    assert data["recovery_code"] != account["recovery_code"]
    assert login(client).get_json()["ok"]
    again = client.post("/api/unlock", json={"username": "poojitha", "code": account["recovery_code"]})
    assert again.status_code == 401


def test_success_resets_the_failure_count(client, account):
    for _ in range(4):
        login(client, password="wrongpass")
    assert login(client).get_json()["ok"]
    assert login(client, password="wrongpass").get_json()["attempts_left"] == 4


def test_test_page_needs_the_owner_logged_in(client, account):
    assert try_test_page(client).status_code == 403
    assert client.get("/api/stats?username=poojitha").status_code == 403
    assert client.post("/api/tests/clear", json={"username": "poojitha", "password": PASSWORD}).status_code == 403
    assert client.get("/api/me").get_json()["user"] is None
    login(client)
    assert client.get("/api/me").get_json()["user"] == "poojitha"
    assert try_test_page(client).status_code == 200


def test_logout_closes_the_test_page(client, account):
    login(client)
    client.post("/api/logout")
    assert client.get("/api/me").get_json()["user"] is None
    assert try_test_page(client).status_code == 403


def test_test_page_records_attempts_and_stats(client, account):
    login(client)
    for label, seed in [("owner", 1), ("owner", 2), ("impostor", 3)]:
        assert try_test_page(client, label, seed).get_json()["ok"]
    stats = client.get("/api/stats?username=poojitha").get_json()
    assert len(stats["owner"]) == 2
    assert len(stats["impostor"]) == 1
    assert [r["label"] for r in stats["recent"]] == ["impostor", "owner", "owner"]


def test_test_attempts_dont_count_towards_lockout(client, account):
    login(client)
    for _ in range(6):
        try_test_page(client, seed=5, speed=0.4)
    assert login(client).status_code == 200


def test_threshold_needs_the_password_and_a_sane_value(client, account):
    login(client)
    url = "/api/threshold"
    assert client.post(url, json={"username": "poojitha", "password": "nope", "threshold": 2}).status_code == 401
    assert client.post(url, json={"username": "poojitha", "password": PASSWORD, "threshold": 50}).status_code == 400
    res = client.post(url, json={"username": "poojitha", "password": PASSWORD, "threshold": 1.8})
    assert res.get_json()["threshold"] == 1.8


def test_clear_test_data(client, account):
    login(client)
    try_test_page(client)
    assert client.post("/api/tests/clear", json={"username": "poojitha", "password": PASSWORD}).get_json()["ok"]
    assert client.get("/api/stats?username=poojitha").get_json()["owner"] == []


def test_old_databases_are_upgraded(client, tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    with sqlite3.connect(old) as conn:
        conn.executescript("""
            CREATE TABLE users (username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL,
                                samples TEXT NOT NULL, threshold REAL NOT NULL);
            CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
                                   label TEXT NOT NULL, distance REAL NOT NULL,
                                   created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO users VALUES ('old', 'x', '[]', 2.0);
        """)
    monkeypatch.setattr(cadence, "DB_PATH", old)
    cadence.init_db()
    with sqlite3.connect(old) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert {"failed_logins", "locked_until", "recovery_hash"} <= cols
        assert conn.execute("SELECT username FROM users").fetchone() == ("old",)
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'alerts'").fetchone()
