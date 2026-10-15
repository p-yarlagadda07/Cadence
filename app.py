import json
import math
import os
import secrets
import sqlite3
import time
from pathlib import Path
from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash
import rhythm

DB_PATH = Path(os.environ.get("CADENCE_DB", Path(__file__).parent / "cadence.db"))
ENROLL_SAMPLES = 8
MAX_SAMPLES = 15
MAX_HOLD_MS = 2000
MAX_SHIFT_MS = 5000
MAX_FAILS = 5
PUBLIC_TRIES = 15
PUBLIC_TRIES_PER_NETWORK = 60
MAX_TESTING_MINUTES = 7 * 24 * 60
LOCK_SECONDS = 15 * 60
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

app = Flask(__name__)


def load_secret():
    if os.environ.get("CADENCE_SECRET"):
        return os.environ["CADENCE_SECRET"]
    path = Path(__file__).parent / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


app.secret_key = load_secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def add_column(conn, table, name, definition):
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                pw_hash TEXT NOT NULL,
                samples TEXT NOT NULL,
                threshold REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                label TEXT NOT NULL,
                distance REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        add_column(conn, "attempts", "tempo_ok", "INTEGER NOT NULL DEFAULT 1")
        add_column(conn, "users", "failed_logins", "INTEGER NOT NULL DEFAULT 0")
        add_column(conn, "users", "locked_until", "REAL NOT NULL DEFAULT 0")
        add_column(conn, "users", "recovery_hash", "TEXT")
        add_column(conn, "users", "testing_until", "REAL NOT NULL DEFAULT 0")
        add_column(conn, "users", "testing_round", "INTEGER NOT NULL DEFAULT 0")
        add_column(conn, "users", "testing_note", "TEXT NOT NULL DEFAULT ''")
        add_column(conn, "attempts", "public", "INTEGER NOT NULL DEFAULT 0")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS public_tries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                round INTEGER NOT NULL,
                tester TEXT NOT NULL,
                network TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                details TEXT NOT NULL,
                seen INTEGER NOT NULL DEFAULT 0
            )
        """)

def fail(message, status=400, **extra):
    return jsonify(ok=False, error=message, **extra), status

def read_body():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = data.get("password", "")
    if not isinstance(password, str):
        password = ""
    return data, username, password

def new_recovery_code():
    raw = "".join(secrets.choice(CODE_CHARS) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))

def normalize_code(code):
    return "".join(ch for ch in str(code).upper() if ch.isalnum())

def clean_keys(keys, length):
    if not isinstance(keys, list) or len(keys) != length:
        return None
    out = []
    for k in keys:
        if not isinstance(k, list) or len(k) not in (2, 5):
            return None
        try:
            vals = [float(v) for v in k]
        except (TypeError, ValueError):
            return None
        down, up = vals[0], vals[1]
        if up < down or up - down > MAX_HOLD_MS:
            return None
        if len(vals) == 5:
            s_down, s_up, side = vals[2], vals[3], vals[4]
            if s_down > down or s_up < s_down or s_up - s_down > MAX_SHIFT_MS or side not in (0, 1):
                return None
            out.append((down, up, s_down, s_up, int(side)))
        else:
            out.append((down, up))
    downs = [k[0] for k in out]
    if downs != sorted(downs):
        return None
    return out

def score_of(row):
    return row["distance"] if row["tempo_ok"] else None

def get_user(username):
    return db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

def minutes_left(user):
    return max(1, math.ceil((user["locked_until"] - time.time()) / 60))

def register_failure(username):
    fails = get_user(username)["failed_logins"] + 1
    if fails >= MAX_FAILS:
        db().execute(
            "UPDATE users SET failed_logins = 0, locked_until = ? WHERE username = ?",
            (time.time() + LOCK_SECONDS, username),
        )
        db().commit()
        return 0
    db().execute("UPDATE users SET failed_logins = ? WHERE username = ?", (fails, username))
    db().commit()
    return MAX_FAILS - fails

def check_attempt(user, password, keys):
    keys = clean_keys(keys, len(password))
    if keys is None:
        return None, fail("Couldn't read the typing rhythm. Type it again in one go.")
    if user is None or not check_password_hash(user["pw_hash"], password):
        return None, fail("Login failed.", 401)
    samples = json.loads(user["samples"])
    profile = rhythm.build_profile(samples)
    dist = rhythm.distance(profile, keys)
    speed_ok = rhythm.tempo_ok(profile, keys)
    return {
        "keys": keys,
        "samples": samples,
        "distance": round(dist, 3),
        "threshold": round(user["threshold"], 3),
        "speed_ok": speed_ok,
        "tempo": round(rhythm.tempo(keys)),
        "usual_tempo": round(profile["tempo"]),
        "accepted": speed_ok and dist <= user["threshold"],
        "profile": profile,
        "attempt": rhythm.features(keys),
        "length": len(password),
    }, None

def key_name(password, i):
    ch = password[i] if 0 <= i < len(password) else "?"
    return "space" if ch == " " else f"'{ch}'"


def describe(item, password):
    kind, i, r = item["kind"], item.get("i", 0), item.get("ratio", 1)
    if kind == "hold":
        if r > 1:
            return f"held {key_name(password, i)} {r:.1f}x as long as you do"
        return f"held {key_name(password, i)} only {r:.0%} as long as you do"
    if kind == "gap":
        pair = f"{key_name(password, i)} and {key_name(password, i + 1)}"
        if r > 1:
            return f"paused {r:.1f}x longer than you between {pair}"
        return f"went between {pair} {1 / r:.1f}x faster than you"
    if kind == "no_shift":
        return f"typed {key_name(password, i)} without holding Shift"
    if kind == "shift_side":
        used, yours = ("right", "left") if item.get("side") == 1 else ("left", "right")
        return f"used the {used} Shift for {key_name(password, i)}, you use the {yours}"
    if kind == "speed":
        if r < 1:
            return f"typed the whole password {1 - r:.0%} faster than you usually do"
        return f"typed the whole password {r - 1:.0%} slower than you usually do"
    return "typed it differently"


def record_alert(username, profile, keys):
    db().execute(
        "INSERT INTO alerts (username, created_at, details) VALUES (?, ?, ?)",
        (username, time.time(), json.dumps(rhythm.explain(profile, keys))),
    )


def unseen_alerts(username, password):
    rows = db().execute(
        "SELECT created_at, details FROM alerts WHERE username = ? AND seen = 0 ORDER BY id",
        (username,),
    ).fetchall()
    db().execute("UPDATE alerts SET seen = 1 WHERE username = ?", (username,))
    return [
        {"at": r["created_at"], "reasons": [describe(f, password) for f in json.loads(r["details"])]}
        for r in rows
    ]


def owner_only(username):
    if not username or session.get("user") != username:
        return fail("Log in as this account first. This page is only for the account owner.", 403)
    return None


@app.get("/")
def index():
    return render_template("index.html", enroll_samples=ENROLL_SAMPLES, max_fails=MAX_FAILS, public_tries=PUBLIC_TRIES)

@app.post("/api/enroll")
def enroll():
    data, username, password = read_body()
    raw = data.get("samples")
    if not username or len(username) > 32:
        return fail("Pick a username (up to 32 characters).")
    if len(password) < 6:
        return fail("Password needs at least 6 characters, otherwise there's no rhythm to learn.")
    if not isinstance(raw, list) or len(raw) < ENROLL_SAMPLES:
        return fail(f"Need {ENROLL_SAMPLES} samples of your typing.")
    if get_user(username) is not None:
        return fail("That username is taken.", 409)
    samples = [clean_keys(s, len(password)) for s in raw]
    if any(s is None for s in samples):
        return fail("One of the samples looked broken. Start over.")
    samples = rhythm.drop_outliers(samples)
    threshold = rhythm.auto_threshold(samples)
    code = new_recovery_code()
    db().execute(
        "INSERT INTO users (username, pw_hash, samples, threshold, recovery_hash) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), json.dumps(samples), threshold,
         generate_password_hash(normalize_code(code))),
    )
    db().commit()
    return jsonify(ok=True, threshold=round(threshold, 3), kept=len(samples), recovery_code=code)

@app.post("/api/login")
def login():
    data, username, password = read_body()
    user = get_user(username)
    if user is not None and user["locked_until"] > time.time():
        return fail(
            f"Too many failed attempts. Try again in {minutes_left(user)} min, or unlock with your recovery code.",
            423, reason="locked",
        )
    result, error = check_attempt(user, password, data.get("keys"))
    if error:
        if user is not None and error[1] == 401:
            return fail("Login failed.", 401, attempts_left=register_failure(username))
        return error
    if not result["accepted"]:
        record_alert(username, result["profile"], result["keys"])
        return fail("Login failed.", 401, attempts_left=register_failure(username))
    samples = (result["samples"] + [result["keys"]])[-MAX_SAMPLES:]
    db().execute(
        "UPDATE users SET samples = ?, failed_logins = 0 WHERE username = ?",
        (json.dumps(samples), username),
    )
    alerts = unseen_alerts(username, password)
    db().commit()
    session.clear()
    session["user"] = username
    return jsonify(
        ok=True,
        distance=result["distance"],
        threshold=result["threshold"],
        tempo=result["tempo"],
        usual_tempo=result["usual_tempo"],
        profile=result["profile"],
        attempt=result["attempt"],
        length=result["length"],
        alerts=alerts,
    )


@app.get("/api/me")
def me():
    return jsonify(user=session.get("user"))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post("/api/unlock")
def unlock():
    data, username, _password = read_body()
    user = get_user(username)
    code = normalize_code(data.get("code", ""))
    if user is None or not user["recovery_hash"] or not check_password_hash(user["recovery_hash"], code):
        return fail("That recovery code doesn't match.", 401)
    new_code = new_recovery_code()
    db().execute(
        "UPDATE users SET failed_logins = 0, locked_until = 0, recovery_hash = ? WHERE username = ?",
        (generate_password_hash(normalize_code(new_code)), username),
    )
    db().commit()
    return jsonify(ok=True, recovery_code=new_code)

@app.post("/api/test")
def test_attempt():
    data, username, password = read_body()
    denied = owner_only(username)
    if denied:
        return denied
    label = data.get("label")
    if label not in ("owner", "impostor"):
        return fail("Say whether this attempt is you or someone else.")
    result, error = check_attempt(get_user(username), password, data.get("keys"))
    if error:
        return error
    db().execute(
        "INSERT INTO attempts (username, label, distance, tempo_ok) VALUES (?, ?, ?, ?)",
        (username, label, result["distance"], int(result["speed_ok"])),
    )
    db().commit()
    return jsonify(ok=True, accepted=result["accepted"], distance=result["distance"])

def client_network():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return request.headers.get("X-Real-IP") or forwarded.split(",")[0].strip() or request.remote_addr or "?"


def tester_id():
    if "tester" not in session:
        session["tester"] = secrets.token_hex(16)
    return session["tester"]


def testing_open(user):
    return user is not None and user["testing_until"] > time.time()


def tries_used(user, column, value):
    return db().execute(
        f"SELECT COUNT(*) FROM public_tries WHERE username = ? AND round = ? AND {column} = ?",
        (user["username"], user["testing_round"], value),
    ).fetchone()[0]


@app.get("/api/testing")
def testing_status():
    rows = db().execute(
        "SELECT username, testing_until, testing_note FROM users WHERE testing_until > ? ORDER BY username",
        (time.time(),),
    ).fetchall()
    open_now = [{"username": r["username"], "until": r["testing_until"], "note": r["testing_note"]} for r in rows]
    mine = None
    if session.get("user"):
        user = get_user(session["user"])
        if user is not None:
            mine = {
                "open": testing_open(user),
                "until": user["testing_until"],
                "note": user["testing_note"],
                "tries": db().execute(
                    "SELECT COUNT(*) FROM public_tries WHERE username = ? AND round = ?",
                    (user["username"], user["testing_round"]),
                ).fetchone()[0],
            }
    return jsonify(ok=True, open=open_now, mine=mine, tries_per_person=PUBLIC_TRIES)


@app.post("/api/testing")
def set_testing():
    data, username, _password = read_body()
    denied = owner_only(username)
    if denied:
        return denied
    try:
        minutes = int(data.get("minutes"))
    except (TypeError, ValueError):
        return fail("Pick how long to keep testing open.")
    if minutes == 0:
        db().execute("UPDATE users SET testing_until = 0 WHERE username = ?", (username,))
        db().commit()
        return jsonify(ok=True, open=False)
    if not 5 <= minutes <= MAX_TESTING_MINUTES:
        return fail("Testing can be open for 5 minutes up to a week.")
    note = str(data.get("note", "")).strip()[:200]
    until = time.time() + minutes * 60
    db().execute(
        "UPDATE users SET testing_until = ?, testing_round = testing_round + 1, testing_note = ? WHERE username = ?",
        (until, note, username),
    )
    db().commit()
    return jsonify(ok=True, open=True, until=until)


@app.post("/api/testing/try")
def public_try():
    data, username, password = read_body()
    user = get_user(username)
    if not testing_open(user):
        return fail("Testing isn't open for this account right now.", 403)
    tester, network = tester_id(), client_network()
    used = tries_used(user, "tester", tester)
    if used >= PUBLIC_TRIES or tries_used(user, "network", network) >= PUBLIC_TRIES_PER_NETWORK:
        return fail("You've used all your tries for this round. Thanks for testing!", 429, tries_left=0)
    result, error = check_attempt(user, password, data.get("keys"))
    if error and error[1] != 401:
        return error
    db().execute(
        "INSERT INTO public_tries (username, round, tester, network, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, user["testing_round"], tester, network, time.time()),
    )
    left = PUBLIC_TRIES - used - 1
    if error:
        db().commit()
        return fail("That's not the right password.", 401, tries_left=left)
    db().execute(
        "INSERT INTO attempts (username, label, distance, tempo_ok, public) VALUES (?, 'impostor', ?, ?, 1)",
        (username, result["distance"], int(result["speed_ok"])),
    )
    db().commit()
    return jsonify(ok=True, let_in=result["accepted"], tries_left=left)


@app.get("/api/stats")
def stats():
    username = request.args.get("username", "").strip().lower()
    denied = owner_only(username)
    if denied:
        return denied
    user = get_user(username)
    if user is None:
        return fail("No such user.", 404)
    rows = db().execute(
        "SELECT label, distance, tempo_ok, public FROM attempts WHERE username = ? ORDER BY id", (username,)
    ).fetchall()
    return jsonify(
        ok=True,
        threshold=round(user["threshold"], 3),
        owner=[score_of(r) for r in rows if r["label"] == "owner"],
        impostor=[score_of(r) for r in rows if r["label"] == "impostor"],
        recent=[{"label": r["label"], "distance": r["distance"], "speed_ok": bool(r["tempo_ok"]),
                 "public": bool(r["public"])} for r in rows[-8:]][::-1],
    )

@app.post("/api/threshold")
def set_threshold():
    data, username, password = read_body()
    denied = owner_only(username)
    if denied:
        return denied
    user = get_user(username)
    if user is None or not check_password_hash(user["pw_hash"], password):
        return fail("Wrong username or password.", 401)
    try:
        threshold = float(data.get("threshold"))
    except (TypeError, ValueError):
        return fail("Threshold should be a number.")
    if not 0.5 <= threshold <= 10:
        return fail("Keep the threshold between 0.5 and 10.")
    db().execute("UPDATE users SET threshold = ? WHERE username = ?", (threshold, username))
    db().commit()
    return jsonify(ok=True, threshold=round(threshold, 3))

@app.post("/api/tests/clear")
def clear_tests():
    _data, username, password = read_body()
    denied = owner_only(username)
    if denied:
        return denied
    user = get_user(username)
    if user is None or not check_password_hash(user["pw_hash"], password):
        return fail("Wrong username or password.", 401)
    db().execute("DELETE FROM attempts WHERE username = ?", (username,))
    db().commit()
    return jsonify(ok=True)

init_db()

if __name__ == "__main__":
    app.run(debug=True)
