import json
import sqlite3
from pathlib import Path
from flask import Flask, g, jsonify, render_template, request
from werkzeug.security import check_password_hash, generate_password_hash
import rhythm

DB_PATH = Path(__file__).parent / "cadence.db"
ENROLL_SAMPLES = 8
MAX_SAMPLES = 15
MAX_HOLD_MS = 2000

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

def fail(message, status=400, **extra):
    return jsonify(ok=False, error=message, **extra), status

def read_body():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = data.get("password", "")
    if not isinstance(password, str):
        password = ""
    return data, username, password

def clean_keys(keys, length):
    if not isinstance(keys, list) or len(keys) != length:
        return None
    out = []
    for k in keys:
        try:
            down, up = float(k[0]), float(k[1])
        except (TypeError, ValueError, IndexError):
            return None
        if up < down or up - down > MAX_HOLD_MS:
            return None
        out.append((down, up))
    downs = [d for d, _ in out]
    if downs != sorted(downs):
        return None
    return out

def get_user(username):
    return db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

def check_attempt(user, password, keys):
    if user is None or not check_password_hash(user["pw_hash"], password):
        return None, fail("Wrong username or password.", 401, reason="password")
    keys = clean_keys(keys, len(password))
    if keys is None:
        return None, fail("Couldn't read the typing rhythm. Type it again in one go.")
    samples = json.loads(user["samples"])
    profile = rhythm.build_profile(samples)
    attempt = rhythm.features(keys)
    dist = rhythm.distance(profile, attempt)
    return {
        "keys": keys,
        "samples": samples,
        "distance": round(dist, 3),
        "threshold": round(user["threshold"], 3),
        "accepted": dist <= user["threshold"],
        "profile": profile,
        "attempt": attempt,
        "length": len(password),
    }, None

@app.get("/")
def index():
    return render_template("index.html", enroll_samples=ENROLL_SAMPLES)

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
    db().execute(
        "INSERT INTO users (username, pw_hash, samples, threshold) VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), json.dumps(samples), threshold),
    )
    db().commit()
    return jsonify(ok=True, threshold=round(threshold, 3), kept=len(samples))

@app.post("/api/login")
def login():
    data, username, password = read_body()
    result, error = check_attempt(get_user(username), password, data.get("keys"))
    if error:
        return error
    if result["accepted"]:
        samples = (result["samples"] + [result["keys"]])[-MAX_SAMPLES:]
        db().execute("UPDATE users SET samples = ? WHERE username = ?", (json.dumps(samples), username))
        db().commit()
    return jsonify(
        ok=result["accepted"],
        reason=None if result["accepted"] else "rhythm",
        distance=result["distance"],
        threshold=result["threshold"],
        profile=result["profile"],
        attempt=result["attempt"],
        length=result["length"],
    )

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
        "INSERT INTO attempts (username, label, distance) VALUES (?, ?, ?)",
        (username, label, result["distance"]),
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
        "SELECT label, distance FROM attempts WHERE username = ? ORDER BY id", (username,)
    ).fetchall()
    return jsonify(
        ok=True,
        threshold=round(user["threshold"], 3),
        owner=[r["distance"] for r in rows if r["label"] == "owner"],
        impostor=[r["distance"] for r in rows if r["label"] == "impostor"],
        recent=[{"label": r["label"], "distance": r["distance"]} for r in rows[-8:]][::-1],
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
