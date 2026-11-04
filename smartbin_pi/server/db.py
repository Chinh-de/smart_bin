import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    device_id    TEXT NOT NULL,
    ts           TEXT NOT NULL,          -- giờ trên thiết bị, ISO 8601
    result       TEXT NOT NULL,          -- BIN | REJECT | ERROR
    bin_id       INTEGER,
    bin_name     TEXT,
    message      TEXT,
    top_class    TEXT,
    top_prob     REAL,
    top3         TEXT,                   -- JSON
    bin_probs    TEXT,                   -- JSON
    uncertain    INTEGER,
    distance_cm  REAL,
    inference_ms REAL,
    total_ms     REAL,
    model        TEXT,
    image_path   TEXT,
    received_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS devices (
    device_id  TEXT PRIMARY KEY,
    last_seen  TEXT NOT NULL,
    info       TEXT                      -- JSON heartbeat gần nhất
);
"""


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def row_to_event(r):
    e = dict(r)
    for k in ("top3", "bin_probs"):
        e[k] = json.loads(e[k]) if e[k] else None
    e["uncertain"] = bool(e["uncertain"])
    return e
