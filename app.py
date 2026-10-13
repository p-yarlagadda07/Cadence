import json
import math
import secrets
import sqlite3
import time
from pathlib import Path
from flask import Flask, g, jsonify, render_template, request
from werkzeug.security import check_password_hash, generate_password_hash
import rhythm

DB_PATH = Path(__file__).parent / "cadence.db"
ENROLL_SAMPLES = 8
MAX_SAMPLES = 15
MAX_HOLD_MS = 2000
MAX_SHIFT_MS = 5000
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

app = Flask(__name__)

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
    if user is None or not check_password_hash(user["pw_hash"], password):
        return None, fail("Wrong username or password.", 401, reason="password")
    keys = clean_keys(keys, len(password))
    if keys is None:
        return None, fail("Couldn't read the typing rhythm. Type it again in one go.")
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

@app.get("/")
def index():
    return render_template("index.html", enroll_samples=ENROLL_SAMPLES, max_fails=MAX_FAILS)

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
            left = register_failure(username)
            return fail("Wrong username or password.", 401, reason="password", attempts_left=left)
        return error
    if result["accepted"]:
        samples = (result["samples"] + [result["keys"]])[-MAX_SAMPLES:]
        db().execute(
            "UPDATE users SET samples = ?, failed_logins = 0 WHERE username = ?",
            (json.dumps(samples), username),
        )
        db().commit()
        reason, left = None, None
    else:
        reason = "rhythm" if result["speed_ok"] else "speed"
        left = register_failure(username)
    return jsonify(
        ok=result["accepted"],
        reason=reason,
        attempts_left=left,
        distance=result["distance"],
        threshold=result["threshold"],
        tempo=result["tempo"],
        usual_tempo=result["usual_tempo"],
        profile=result["profile"],
        attempt=result["attempt"],
        length=result["length"],
    )

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

@app.get("/api/stats")
def stats():
    username = request.args.get("username", "").strip().lower()
    user = get_user(username)
    if user is None:
        return fail("No such user.", 404)
    rows = db().execute(
        "SELECT label, distance, tempo_ok FROM attempts WHERE username = ? ORDER BY id", (username,)
    ).fetchall()
    score = lambda r: r["distance"] if r["tempo_ok"] else None
    return jsonify(
        ok=True,
        threshold=round(user["threshold"], 3),
        owner=[score(r) for r in rows if r["label"] == "owner"],
        impostor=[score(r) for r in rows if r["label"] == "impostor"],
        recent=[{"label": r["label"], "distance": r["distance"], "speed_ok": bool(r["tempo_ok"])} for r in rows[-8:]][::-1],
    )

@app.post("/api/threshold")
def set_threshold():
    data, username, password = read_body()
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
    user = get_user(username)
    if user is None or not check_password_hash(user["pw_hash"], password):
        return fail("Wrong username or password.", 401)
    db().execute("DELETE FROM attempts WHERE username = ?", (username,))
    db().commit()
    return jsonify(ok=True)

init_db()

if __name__ == "__main__":
    app.run(debug=True)
