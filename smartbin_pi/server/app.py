"""Web server lưu log thùng rác: nhận sự kiện + ảnh từ Pi, hiển thị lịch sử và thống kê,
xem log trực tiếp của Pi (/logs), chỉnh thông số servo/cảm biến và chạy thử servo (/config).

    python server/app.py              # chạy ở 0.0.0.0:5000, xem từ điện thoại cùng WiFi
Biến môi trường: SMARTBIN_API_KEY (mặc định "change-me", phải khớp config/app.json), SMARTBIN_PORT.
"""
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, g, jsonify, render_template, request, send_from_directory

import db

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
DB_PATH = BASE / "smartbin.db"
API_KEY = os.environ.get("SMARTBIN_API_KEY", "change-me")
OFFLINE_AFTER_S = 90          # 3 nhịp heartbeat
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_FRAME = 2 * 1024 * 1024
MAX_STREAMS = 8
STREAM_LIVE_S = 5             # quá ngần này giây không có khung mới thì coi là ngừng phát
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


def get_db():
    if "db" not in g:
        g.db = db.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_):
    con = g.pop("db", None)
    if con:
        con.close()


def require_key():
    if request.headers.get("X-API-Key") != API_KEY:
        abort(401)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def touch_device(con, device_id, info=None):
    con.execute("""INSERT INTO devices(device_id, last_seen, info) VALUES (?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen,
                   info = COALESCE(excluded.info, devices.info)""",
                (device_id, now_iso(), json.dumps(info) if info else None))


# ---------------- API ----------------
@app.post("/api/events")
def api_add_event():
    require_key()
    try:
        m = json.loads(request.form["meta"])
        event_id, device_id, ts = m["event_id"], m["device_id"], m["timestamp"]
    except (KeyError, ValueError, TypeError):
        return jsonify(error="thiếu hoặc sai trường meta"), 400
    # event_id và ts đi vào đường dẫn file nên phải kiểm tra chặt (chống ghi ra ngoài uploads/)
    if not (isinstance(event_id, str) and event_id.isalnum() and isinstance(device_id, str)):
        return jsonify(error="event_id không hợp lệ"), 400
    if not (isinstance(ts, str) and TS_RE.match(ts)):
        return jsonify(error="timestamp không hợp lệ"), 400

    con = get_db()
    if con.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone():
        return jsonify(status="duplicate"), 200   # Pi gửi lại sau khi mất mạng

    rel = None
    f = request.files.get("image")
    if f:
        day_dir = UPLOADS / ts[:10]
        day_dir.mkdir(parents=True, exist_ok=True)
        f.save(day_dir / f"{event_id}.jpg")
        rel = f"{ts[:10]}/{event_id}.jpg"

    con.execute("""INSERT INTO events VALUES (:event_id, :device_id, :ts, :result, :bin_id, :bin_name, :message,
                   :top_class, :top_prob, :top3, :bin_probs, :uncertain, :distance_cm, :inference_ms, :total_ms,
                   :model, :image_path, :received_at)""", {
        "event_id": event_id, "device_id": device_id, "ts": ts, "result": m.get("result", "BIN"),
        "bin_id": m.get("bin_id"), "bin_name": m.get("bin_name"), "message": m.get("message"),
        "top_class": m.get("top_class"), "top_prob": m.get("top_prob"),
        "top3": json.dumps(m.get("top3")), "bin_probs": json.dumps(m.get("bin_probs")),
        "uncertain": int(bool(m.get("uncertain"))), "distance_cm": m.get("distance_cm"),
        "inference_ms": m.get("inference_ms"), "total_ms": m.get("total_ms"), "model": m.get("model"),
        "image_path": rel, "received_at": now_iso(),
    })
    touch_device(con, device_id)
    con.commit()
    return jsonify(status="ok"), 201


@app.post("/api/heartbeat")
def api_heartbeat():
    require_key()
    info = request.get_json(silent=True) or {}
    if not info.get("device_id"):
        return jsonify(error="thiếu device_id"), 400
    con = get_db()
    touch_device(con, info["device_id"], info)
    con.commit()
    return jsonify(status="ok")


def query_events(limit=50, bin_id=None, cls=None, date=None, result=None):
    sql, args = "SELECT * FROM events WHERE 1=1", []
    if bin_id:
        sql += " AND bin_id = ?"; args.append(bin_id)
    if cls:
        sql += " AND top_class = ?"; args.append(cls)
    if date:
        sql += " AND substr(ts, 1, 10) = ?"; args.append(date)
    if result:
        sql += " AND result = ?"; args.append(result)
    sql += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
    return [db.row_to_event(r) for r in get_db().execute(sql, args)]


def filters_from_request():
    a = request.args
    return dict(limit=max(1, min(a.get("limit", 100, type=int), 1000)), bin_id=a.get("bin", type=int),
                cls=a.get("class") or None, date=a.get("date") or None, result=a.get("result") or None)


@app.get("/api/events")
def api_events():
    return jsonify(query_events(**filters_from_request()))


def compute_stats(date=None):
    con = get_db()
    where, args = ("WHERE substr(ts, 1, 10) = ?", [date]) if date else ("", [])

    def group(col):
        return [dict(r) for r in con.execute(
            f"SELECT {col} AS k, COUNT(*) AS n FROM events {where} GROUP BY {col} ORDER BY n DESC", args)]

    tot = con.execute(f"""SELECT COUNT(*) AS n, AVG(inference_ms) AS inf, AVG(total_ms) AS total,
                          SUM(result = 'REJECT') AS rej, SUM(result = 'ERROR') AS err, SUM(uncertain) AS unc
                          FROM events {where}""", args).fetchone()
    return {
        "total": tot["n"], "reject": tot["rej"] or 0, "error": tot["err"] or 0, "uncertain": tot["unc"] or 0,
        "avg_inference_ms": round(tot["inf"] or 0, 1), "avg_total_ms": round(tot["total"] or 0),
        "by_bin": [dict(r) for r in con.execute(
            f"""SELECT COALESCE(bin_name, CASE result WHEN 'REJECT' THEN 'Từ chối' ELSE 'Lỗi' END) AS k,
                COUNT(*) AS n FROM events {where} GROUP BY k ORDER BY n DESC""", args)],
        "by_class": group("top_class"),
        "by_hour": group("substr(ts, 12, 2)"),
        "by_day": [dict(r) for r in con.execute(
            "SELECT substr(ts, 1, 10) AS k, COUNT(*) AS n FROM events GROUP BY k ORDER BY k DESC LIMIT 14")],
    }


@app.get("/api/stats")
def api_stats():
    return jsonify(compute_stats(request.args.get("date")))


@app.get("/uploads/<path:p>")
def uploads(p):
    return send_from_directory(UPLOADS, p)


# ---------------- video trực tiếp ----------------
# Pi đẩy từng khung JPEG lên; server giữ khung mới nhất của mỗi thiết bị trong RAM
# và phát lại cho trình duyệt dạng MJPEG. Không ghi đĩa.
class Stream:
    def __init__(self):
        self.cond = threading.Condition()
        self.frame, self.seq, self.ts, self.viewers = None, 0, 0.0, 0


STREAMS, STREAMS_LOCK = {}, threading.Lock()


def get_stream(device_id, create=False):
    with STREAMS_LOCK:
        s = STREAMS.get(device_id)
        if s is None and create and len(STREAMS) < MAX_STREAMS:
            s = STREAMS[device_id] = Stream()
        return s


@app.post("/api/stream/<device_id>/frame")
def api_stream_frame(device_id):
    require_key()
    data = request.get_data(cache=False)
    if not (SAFE_ID.match(device_id) and 2 < len(data) <= MAX_FRAME and data[:2] == b"\xff\xd8"):
        return jsonify(error="khung hình không hợp lệ"), 400
    s = get_stream(device_id, create=True)
    if s is None:
        return jsonify(error="quá nhiều luồng video"), 429
    with s.cond:
        s.frame, s.seq, s.ts = data, s.seq + 1, time.time()
        s.cond.notify_all()
        return jsonify(watch=s.viewers)        # Pi dùng số người xem để biết có nên tiếp tục gửi


@app.get("/api/stream/<device_id>/want")
def api_stream_want(device_id):
    require_key()
    s = get_stream(device_id)
    return jsonify(watch=s.viewers if s else 0)


# ---------------- cân (load cell) ----------------
# Pi đẩy số gram mới nhất mỗi giây; chỉ giữ trong RAM, hiện cạnh video.
WEIGHTS = {}            # device_id -> {"grams", "calibrated", "error", "ts"}
WEIGHT_STALE_S = 5


@app.post("/api/weight/<device_id>")
def api_weight(device_id):
    require_key()
    d = request.get_json(silent=True) or {}
    g = d.get("grams")
    if not SAFE_ID.match(device_id) or not (g is None or isinstance(g, (int, float))):
        return jsonify(error="dữ liệu cân không hợp lệ"), 400
    if device_id in WEIGHTS or len(WEIGHTS) < MAX_STREAMS:
        WEIGHTS[device_id] = {"grams": g, "calibrated": bool(d.get("calibrated")),
                              "error": str(d["error"])[:200] if d.get("error") else None, "ts": time.time()}
    return jsonify(status="ok")


def weight_of(device_id):
    w = WEIGHTS.get(device_id)
    if not w or time.time() - w["ts"] > WEIGHT_STALE_S:
        return None
    return {k: w[k] for k in ("grams", "calibrated", "error")}


@app.get("/api/stream/state")
def api_stream_state():
    with STREAMS_LOCK:
        items = dict(STREAMS)
    out = {d: {"live": s.ts > 0 and time.time() - s.ts < STREAM_LIVE_S, "viewers": s.viewers}
           for d, s in items.items()}
    for d in set(out) | set(WEIGHTS):
        out.setdefault(d, {"live": False, "viewers": 0})["weight"] = weight_of(d)
    return jsonify(out)


@app.get("/stream/<device_id>.mjpg")
def stream_mjpg(device_id):
    if not SAFE_ID.match(device_id) or not get_db().execute(
            "SELECT 1 FROM devices WHERE device_id = ?", (device_id,)).fetchone():
        abort(404)
    s = get_stream(device_id, create=True)
    if s is None:
        abort(404)

    def gen():
        seq, last_new = 0, time.time()
        with s.cond:
            s.viewers += 1
        try:
            while time.time() - last_new < 30:      # 30 s không có hình: kết thúc, trang web sẽ tự nối lại
                with s.cond:
                    fresh = s.cond.wait_for(lambda: s.seq != seq, timeout=2)
                    frame, seq = s.frame, s.seq
                if fresh:
                    last_new = time.time()
                if frame is None:
                    continue
                # không có khung mới thì gửi lại khung cũ: làm "nhịp tim" để phát hiện trình duyệt đã đóng
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame)
                       + frame + b"\r\n")
        finally:
            with s.cond:
                s.viewers -= 1

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})


# ---------------- log trực tiếp + cấu hình từ xa ----------------
# Pi gọi POST /api/sync/<id> mỗi giây (smartbin/remote.py): đẩy log, báo thông số hiện tại, nhận cấu hình
# mới / lệnh thử servo. Chỉ giữ trong RAM: server khởi động lại thì Pi tự gửi lại thông số (need_config).
LOG_KEEP = 5000
LOG_PAGE = 1000
SYNC_ONLINE_S = 5
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class DeviceRT:
    def __init__(self):
        self.lock = threading.Lock()
        self.logs, self.seq = deque(maxlen=LOG_KEEP), 0     # (seq, dòng log)
        self.fields, self.values, self.config_ts = None, None, 0.0
        self.pending = None          # {"id", "values", "ts"}: cấu hình web gửi, Pi chưa nhận kết quả
        self.result = None           # kết quả áp dụng gần nhất do Pi báo
        self.commands = []
        self.last_sync = 0.0


RT, RT_LOCK = {}, threading.Lock()


def get_rt(device_id, create=False):
    with RT_LOCK:
        rt = RT.get(device_id)
        if rt is None and create and SAFE_ID.match(device_id) and len(RT) < MAX_STREAMS:
            rt = RT[device_id] = DeviceRT()
        return rt


@app.post("/api/sync/<device_id>")
def api_sync(device_id):
    require_key()
    d = request.get_json(silent=True)
    rt = get_rt(device_id, create=True)
    if not isinstance(d, dict) or rt is None:
        return jsonify(error="dữ liệu không hợp lệ hoặc quá nhiều thiết bị"), 400
    with rt.lock:
        rt.last_sync = time.time()
        for ln in (d.get("logs") or [])[:2000]:
            if not isinstance(ln, dict):
                continue
            rt.seq += 1
            rt.logs.append((rt.seq, {
                "ts": ln.get("ts") if isinstance(ln.get("ts"), (int, float)) else time.time(),
                "level": ln.get("level") if ln.get("level") in LEVELS else "INFO",
                "name": str(ln.get("name", ""))[:64], "msg": str(ln.get("msg", ""))[:4000]}))
        c = d.get("config")
        if isinstance(c, dict) and isinstance(c.get("fields"), list) and isinstance(c.get("values"), dict):
            rt.fields, rt.values, rt.config_ts = c["fields"], c["values"], time.time()
        r = d.get("result")
        if isinstance(r, dict):
            rt.result = {**r, "ts": time.time()}
            if rt.pending and r.get("id") == rt.pending["id"]:
                rt.pending = None
        commands, rt.commands = rt.commands, []
        return jsonify(need_config=rt.fields is None,
                       config={"id": rt.pending["id"], "values": rt.pending["values"]} if rt.pending else None,
                       commands=commands)


@app.get("/api/logs/<device_id>")
def api_logs(device_id):
    rt = get_rt(device_id)
    if rt is None:
        return jsonify(seq=0, lines=[], online=False)
    after = request.args.get("after", 0, type=int)
    with rt.lock:
        if after > rt.seq:            # server đã khởi động lại: trình duyệt đọc lại từ đầu
            after = 0
        lines = [{"seq": n, **ln} for n, ln in rt.logs if n > after][-LOG_PAGE:]
        return jsonify(seq=rt.seq, lines=lines, online=time.time() - rt.last_sync < SYNC_ONLINE_S)


@app.get("/api/config/<device_id>")
def api_config_get(device_id):
    rt = get_rt(device_id)
    if rt is None:
        return jsonify(online=False, fields=None)
    with rt.lock:
        return jsonify(online=time.time() - rt.last_sync < SYNC_ONLINE_S, fields=rt.fields, values=rt.values,
                       pending=rt.pending, result=rt.result)


@app.post("/api/config/<device_id>")
def api_config_set(device_id):
    rt = get_rt(device_id)
    d = request.get_json(silent=True) or {}
    values = d.get("values")
    if rt is None or rt.fields is None:
        return jsonify(error="thiết bị chưa kết nối"), 404
    if not isinstance(values, dict) or not values:
        return jsonify(error="không có thông số nào"), 400
    keys = {f.get("key") for f in rt.fields}
    if set(values) - keys:
        return jsonify(error=f"thông số lạ: {', '.join(sorted(set(values) - keys))}"), 400
    with rt.lock:
        rt.pending = {"id": uuid.uuid4().hex[:12], "values": values, "ts": time.time()}
        return jsonify(id=rt.pending["id"])


@app.post("/api/command/<device_id>")
def api_command(device_id):
    rt = get_rt(device_id)
    d = request.get_json(silent=True) or {}
    if rt is None:
        return jsonify(error="thiết bị chưa kết nối"), 404
    if d.get("cmd") == "home":
        cmd = {"cmd": "home"}
    elif d.get("cmd") == "bin" and isinstance(d.get("bin"), int):
        cmd = {"cmd": "bin", "bin": d["bin"]}
    else:
        return jsonify(error="lệnh không hợp lệ"), 400
    with rt.lock:
        if len(rt.commands) >= 3:
            return jsonify(error="đang có lệnh chờ, thử lại sau"), 429
        rt.commands.append(cmd)
    return jsonify(status="ok")


# ---------------- trang web ----------------
def device_ids():
    """Thiết bị trong CSDL + thiết bị đang đồng bộ (có thể chưa gửi heartbeat)."""
    ids = {d["id"] for d in devices()}
    with RT_LOCK:
        ids |= set(RT)
    return sorted(ids)


def pick_device():
    ids = device_ids()
    want = request.args.get("device")
    return (want if want in ids else (ids[0] if ids else None)), ids


@app.get("/logs")
def page_logs():
    dev, ids = pick_device()
    return render_template("logs.html", device=dev, ids=ids)


@app.get("/config")
def page_config():
    dev, ids = pick_device()
    return render_template("config.html", device=dev, ids=ids)


def devices():
    out = []
    for r in get_db().execute("SELECT * FROM devices ORDER BY device_id"):
        last = datetime.fromisoformat(r["last_seen"])
        age = (datetime.now().astimezone() - last).total_seconds()
        out.append({"id": r["device_id"], "last_seen": r["last_seen"][11:19], "online": age < OFFLINE_AFTER_S,
                    "info": json.loads(r["info"]) if r["info"] else {}})
    return out


@app.get("/")
def page_index():
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("index.html", devices=devices(), stats=compute_stats(today),
                           events=query_events(limit=12), today=today)


@app.get("/events")
def page_events():
    f = filters_from_request()
    con = get_db()
    classes = [r[0] for r in con.execute("SELECT DISTINCT top_class FROM events ORDER BY 1")]
    bins = [dict(r) for r in con.execute(
        "SELECT DISTINCT bin_id, bin_name FROM events WHERE bin_id IS NOT NULL ORDER BY 1")]
    return render_template("events.html", events=query_events(**f), f=f, classes=classes, bins=bins)


@app.get("/events/<event_id>")
def page_event(event_id):
    r = get_db().execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
    if not r:
        abort(404)
    return render_template("event.html", e=db.row_to_event(r))


@app.get("/stats")
def page_stats():
    date = request.args.get("date") or None
    return render_template("stats.html", s=compute_stats(date), date=date or "")


@app.template_filter("pct")
def pct(v):
    return f"{(v or 0) * 100:.0f}%"


@app.template_filter("hm")
def hm(ts):
    return ts[11:19] if ts else ""


if __name__ == "__main__":
    UPLOADS.mkdir(exist_ok=True)
    db.connect(DB_PATH).close()
    app.run(host="0.0.0.0", port=int(os.environ.get("SMARTBIN_PORT", 5000)), threaded=True)
