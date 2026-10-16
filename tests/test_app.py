import json
import re
import sqlite3

import app as cadence
from conftest import PASSWORD, typing

OTHER = dict(holds=[160, 60, 70, 150, 140, 60, 70, 140, 60], gaps=[120, 260, 110, 230, 130, 240, 120, 250])


def login(client, keys=None, password=PASSWORD, username="poojitha", ip=None, again=None):
    keys = keys if keys is not None else typing(seed=100)
    headers = {"X-Real-IP": ip} if ip else {}
    body = {"username": username, "password": password, "keys": keys}
    res = client.post("/api/login", json=body, headers=headers)
    if res.status_code == 200 and res.get_json().get("again"):
        body["keys"] = again if again is not None else keys
        res = client.post("/api/login", json=body, headers=headers)
    return res


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


def test_someone_else_cant_lock_the_owner_out(client, account):
    for _ in range(5):
        login(client, password="wrongpass", ip="10.0.0.9")
    assert login(client, ip="10.0.0.9").status_code == 423
    assert login(client, ip="10.0.0.1").get_json()["ok"]


def test_unknown_usernames_fail_the_same_way(client, account):
    real = login(client, password="wrongpass").get_json()
    fake = login(client, password="wrongpass", username="nobody_here").get_json()
    assert real == fake
    for _ in range(4):
        login(client, password="wrongpass", username="nobody_here")
    assert login(client, username="nobody_here").status_code == 423


def test_one_network_cant_keep_guessing(client, account):
    for i in range(40):
        login(client, password="wrongpass", username=f"guess{i}", ip="10.0.0.7")
    assert login(client, ip="10.0.0.7").status_code == 429
    assert login(client, ip="10.0.0.8").get_json()["ok"]


def test_sign_ups_are_limited_per_network(client):
    samples = [typing(seed=i) for i in range(8)]
    for i in range(20):
        res = client.post("/api/enroll", json={"username": f"user{i}", "password": PASSWORD, "samples": samples})
        assert res.status_code == 200
    res = client.post("/api/enroll", json={"username": "one_more", "password": PASSWORD, "samples": samples})
    assert res.status_code == 429


def test_sign_ups_can_be_closed(client, account):
    runner = cadence.app.test_cli_runner()
    assert "closed" in runner.invoke(args=["signups", "off"]).output
    samples = [typing(seed=i) for i in range(8)]
    res = client.post("/api/enroll", json={"username": "new_person", "password": PASSWORD, "samples": samples})
    assert res.status_code == 403
    assert 'data-signups="off"' in client.get("/").get_data(as_text=True)
    assert login(client).get_json()["ok"]
    runner.invoke(args=["signups", "on"])
    res = client.post("/api/enroll", json={"username": "new_person", "password": PASSWORD, "samples": samples})
    assert res.status_code == 200


def test_pages_send_safety_headers(client):
    res = client.get("/")
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_huge_requests_are_refused(client, account):
    res = client.post("/api/login", data="x" * 100_000, content_type="application/json")
    assert res.status_code == 413


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


def open_testing(client, minutes=60, note=""):
    login(client)
    res = client.post("/api/testing", json={"username": "poojitha", "minutes": minutes, "note": note})
    client.post("/api/logout")
    return res


def public_try(client, password=PASSWORD, seed=9, like_owner=False):
    keys = typing(seed=seed) if like_owner else typing(seed=seed, **OTHER)
    listing = client.get("/api/testing").get_json()["open"]
    account = listing[0]["account"] if listing else rows("SELECT testing_code FROM users")[0][0]
    body = {"account": account, "password": password, "keys": keys}
    res = client.post("/api/testing/try", json=body)
    if res.status_code == 200 and res.get_json().get("again"):
        res = client.post("/api/testing/try", json=body)
    return res


def test_only_the_owner_can_open_testing(client, account):
    res = client.post("/api/testing", json={"username": "poojitha", "minutes": 60})
    assert res.status_code == 403
    assert client.get("/api/testing").get_json()["open"] == []


def test_testing_window_must_be_sensible(client, account):
    login(client)
    for minutes in (1, 99999, "soon"):
        res = client.post("/api/testing", json={"username": "poojitha", "minutes": minutes})
        assert res.status_code == 400


def test_public_try_needs_an_open_window(client, account):
    assert public_try(client).status_code == 403


def test_public_try_only_says_let_in_or_not(client, account):
    open_testing(client, note="The password is on my LinkedIn post")
    listing = client.get("/api/testing")
    assert "poojitha" not in listing.get_data(as_text=True)
    assert listing.get_json()["open"][0]["note"] == "The password is on my LinkedIn post"
    page = client.get("/").get_data(as_text=True)
    assert "poojitha" not in page
    data = public_try(client).get_json()
    assert set(data) == {"ok", "let_in", "tries_left"}
    assert data["let_in"] is False
    assert data["tries_left"] == 14
    assert public_try(client, seed=100, like_owner=True).get_json()["let_in"] is True


def test_each_person_gets_fifteen_tries(client, account):
    open_testing(client)
    for _ in range(15):
        assert public_try(client).status_code == 200
    res = public_try(client)
    assert res.status_code == 429
    assert res.get_json()["tries_left"] == 0


def test_a_new_window_resets_the_tries(client, account):
    open_testing(client)
    for _ in range(15):
        public_try(client)
    open_testing(client)
    assert public_try(client).status_code == 200


def test_wrong_password_uses_a_try_but_isnt_recorded(client, account):
    open_testing(client)
    res = public_try(client, password="wrongpass")
    assert res.status_code == 401
    assert res.get_json()["tries_left"] == 14
    assert rows("SELECT COUNT(*) FROM attempts")[0][0] == 0


def test_public_tries_show_up_as_someone_else(client, account):
    open_testing(client)
    public_try(client)
    public_try(client)
    login(client)
    stats = client.get("/api/stats?username=poojitha").get_json()
    assert len(stats["impostor"]) == 2
    assert all(r["public"] for r in stats["recent"])


def test_public_tries_dont_lock_or_alert_the_owner(client, account):
    open_testing(client)
    for _ in range(10):
        public_try(client)
    data = login(client).get_json()
    assert data["ok"]
    assert data["alerts"] == []


def test_closing_testing_stops_tries(client, account):
    open_testing(client)
    login(client)
    client.post("/api/testing", json={"username": "poojitha", "minutes": 0})
    client.post("/api/logout")
    assert public_try(client).status_code == 403


def test_old_codes_stop_working_after_a_new_round(client, account):
    open_testing(client)
    old = client.get("/api/testing").get_json()["open"][0]["account"]
    open_testing(client)
    res = client.post("/api/testing/try", json={"account": old, "password": PASSWORD, "keys": typing(seed=9)})
    assert res.status_code == 403
    assert client.post("/api/testing/try", json={"account": "", "password": PASSWORD,
                                                 "keys": typing(seed=9)}).status_code == 403


def test_testing_closes_by_itself(client, account):
    open_testing(client)
    with sqlite3.connect(cadence.DB_PATH) as conn:
        conn.execute("UPDATE users SET testing_until = 1")
    assert public_try(client).status_code == 403


def first_typing(client, keys, password=PASSWORD):
    return client.post("/api/login", json={"username": "poojitha", "password": password, "keys": keys})


def score_of(keys):
    samples = json.loads(rows("SELECT samples FROM users")[0][0])
    return cadence.rhythm.distance(cadence.rhythm.build_profile(samples), keys)


def set_threshold(value):
    with sqlite3.connect(cadence.DB_PATH) as conn:
        conn.execute("UPDATE users SET threshold = ?", (value,))


def test_asking_to_retype_gives_nothing_away(client, account):
    replies = []
    for kw in [dict(password="wrongpass"), dict(keys=typing(seed=9, **OTHER)), dict(keys=typing(speed=0.4, seed=3))]:
        client.post("/api/logout")
        res = first_typing(client, kw.get("keys", typing(seed=1)), kw.get("password", PASSWORD))
        replies.append((res.status_code, res.get_json()))
    assert replies[0] == replies[1] == replies[2]
    assert replies[0][1]["again"] is True
    with client.session_transaction() as s:
        assert set(s) == {"pending"}
        assert len(s["pending"]) > 15


def test_a_close_call_is_settled_by_the_second_typing(client, account):
    keys = typing(seed=100)
    set_threshold(score_of(keys) * 1.05)
    assert first_typing(client, keys).get_json()["again"]
    assert first_typing(client, keys).get_json()["ok"]


def test_a_close_call_followed_by_someone_else_fails(client, account):
    keys = typing(seed=100)
    set_threshold(score_of(keys) * 1.05)
    first_typing(client, keys)
    res = first_typing(client, typing(seed=9, **OTHER))
    assert res.status_code == 401


def test_a_far_off_first_typing_cant_be_saved(client, account):
    first_typing(client, typing(seed=9, **OTHER))
    assert first_typing(client, typing(seed=100)).status_code == 401


def test_a_clear_match_needs_no_retype(client, account):
    keys = typing(seed=100)
    set_threshold(score_of(keys) * 2)
    assert first_typing(client, keys).get_json()["ok"]


def test_public_retype_uses_one_try(client, account):
    open_testing(client)
    listing = client.get("/api/testing").get_json()["open"]
    body = {"account": listing[0]["account"], "password": PASSWORD, "keys": typing(seed=9, **OTHER)}
    first = client.post("/api/testing/try", json=body).get_json()
    assert first == {"ok": True, "again": True, "tries_left": 14}
    second = client.post("/api/testing/try", json=body).get_json()
    assert second == {"ok": True, "let_in": False, "tries_left": 14}


def test_retype_can_be_switched_off(client, account):
    runner = cadence.app.test_cli_runner()
    assert "one typing" in runner.invoke(args=["retype", "off"]).output
    keys = typing(seed=100)
    set_threshold(score_of(keys) * 1.05)
    assert first_typing(client, keys).get_json()["ok"]
    client.post("/api/logout")
    res = first_typing(client, typing(seed=9, **OTHER))
    assert res.status_code == 401
    assert set(res.get_json()) == {"ok", "error", "attempts_left"}
    assert first_typing(client, keys, password="wrongpass").get_json() == res.get_json() | {"attempts_left": 3}
    open_testing(client)
    listing = client.get("/api/testing").get_json()["open"]
    body = {"account": listing[0]["account"], "password": PASSWORD, "keys": typing(seed=9, **OTHER)}
    assert client.post("/api/testing/try", json=body).get_json() == {"ok": True, "let_in": False, "tries_left": 14}
    runner.invoke(args=["retype", "on"])
    client.post("/api/logout")
    assert first_typing(client, typing(seed=9, **OTHER)).get_json()["again"]
