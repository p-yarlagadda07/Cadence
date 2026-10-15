import re

from conftest import PASSWORD, typing


def login(client, keys=None, password=PASSWORD, username="poojitha"):
    keys = keys if keys is not None else typing(seed=100)
    return client.post("/api/login", json={"username": username, "password": password, "keys": keys})


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
    res = login(client)
    data = res.get_json()
    assert data["ok"], data
    assert data["reason"] is None
    with client.application.app_context():
        import json
        from app import db
        samples = json.loads(db().execute("SELECT samples FROM users").fetchone()["samples"])
    assert len(samples) == account["kept"] + 1


def test_wrong_password_is_rejected(client, account):
    res = login(client, password="wrongpass")
    assert res.status_code == 401
    assert res.get_json()["attempts_left"] == 4


def test_much_faster_typing_is_blocked_for_speed(client, account):
    data = login(client, keys=typing(speed=0.4, seed=7)).get_json()
    assert not data["ok"]
    assert data["reason"] == "speed"


def test_different_rhythm_is_blocked(client, account):
    other = typing(seed=9, holds=[160, 60, 70, 150, 140, 60, 70, 140, 60], gaps=[120, 260, 110, 230, 130, 240, 120, 250])
    data = login(client, keys=other).get_json()
    assert not data["ok"]


def test_corrections_and_bad_timings_are_rejected(client, account):
    keys = typing(seed=1)
    keys[2], keys[3] = keys[3], keys[2]
    assert login(client, keys=keys).status_code == 400
    assert login(client, keys=typing(seed=1)[:-1]).status_code == 400


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
    res = client.post("/api/unlock", json={"username": "poojitha", "code": code})
    data = res.get_json()
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


def test_test_page_records_attempts_and_stats(client, account):
    for label, seed in [("owner", 1), ("owner", 2), ("impostor", 3)]:
        res = client.post("/api/test", json={"username": "poojitha", "password": PASSWORD,
                                             "keys": typing(seed=seed), "label": label})
        assert res.get_json()["ok"]
    stats = client.get("/api/stats?username=poojitha").get_json()
    assert len(stats["owner"]) == 2
    assert len(stats["impostor"]) == 1
    assert [r["label"] for r in stats["recent"]] == ["impostor", "owner", "owner"]


def test_test_attempts_dont_count_towards_lockout(client, account):
    for _ in range(6):
        client.post("/api/test", json={"username": "poojitha", "password": PASSWORD,
                                       "keys": typing(speed=0.4, seed=5), "label": "owner"})
    assert login(client).status_code == 200


def test_threshold_needs_the_password_and_a_sane_value(client, account):
    url = "/api/threshold"
    assert client.post(url, json={"username": "poojitha", "password": "nope", "threshold": 2}).status_code == 401
    assert client.post(url, json={"username": "poojitha", "password": PASSWORD, "threshold": 50}).status_code == 400
    res = client.post(url, json={"username": "poojitha", "password": PASSWORD, "threshold": 1.8})
    assert res.get_json()["threshold"] == 1.8


def test_clear_test_data(client, account):
    client.post("/api/test", json={"username": "poojitha", "password": PASSWORD, "keys": typing(seed=1), "label": "owner"})
    assert client.post("/api/tests/clear", json={"username": "poojitha", "password": PASSWORD}).get_json()["ok"]
    assert client.get("/api/stats?username=poojitha").get_json()["owner"] == []


def test_old_databases_are_upgraded(client, tmp_path, monkeypatch):
    import sqlite3
    import app
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
    monkeypatch.setattr(app, "DB_PATH", old)
    app.init_db()
    with sqlite3.connect(old) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert {"failed_logins", "locked_until", "recovery_hash"} <= cols
        assert conn.execute("SELECT username FROM users").fetchone() == ("old",)
