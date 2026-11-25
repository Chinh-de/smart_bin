"""Tạo CSDL giả để thử web server mà không cần Pi.

    python seed.py                    # ghi vào server/dev_data/ (smartbin.db + uploads/), 60 sự kiện
    python seed.py --n 200 --days 7
    SMARTBIN_DATA=dev_data python app.py    # chạy server trên dữ liệu giả

Chạy lại sẽ XOÁ và tạo lại thư mục dữ liệu giả. Không bao giờ ghi vào server/smartbin.db thật
(từ chối nếu --data trỏ vào thư mục server/). Ảnh sự kiện vẽ bằng OpenCV (nếu có), không có thì bỏ qua ảnh."""
import argparse
import json
import random
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import db

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent

# màu nền ảnh giả theo lớp (BGR)
COLORS = {"paper": (235, 225, 200), "plastic": (230, 190, 120), "trash": (120, 120, 130), "battery": (60, 60, 200),
          "biological": (90, 170, 90), "cardboard": (110, 160, 200), "clothes": (180, 120, 180),
          "glass": (200, 220, 160), "metal": (170, 170, 175), "shoes": (80, 110, 150)}
WEIGHTS = {"plastic": 5, "paper": 5, "trash": 4, "cardboard": 3, "glass": 2, "metal": 2, "biological": 2,
           "clothes": 1, "shoes": 1, "battery": 1}


# khối lượng điển hình (g) của mỗi vật
TYPICAL_G = {"battery": 25, "biological": 180, "cardboard": 120, "clothes": 260, "glass": 320, "metal": 90,
             "paper": 35, "plastic": 28, "shoes": 450, "trash": 110}
BIN_FULL_G = 3000        # thùng có cân đầy thì "đổ rác" (cân về 0)


def add_scale_weights(events, scale_bin, rng):
    """Cân nằm trong 1 thùng: số cân sau mỗi sự kiện = tổng khối lượng rác đã rơi vào thùng đó (tăng dần,
    về 0 khi đổ). Sự kiện vào thùng khác không làm đổi số cân. Thỉnh thoảng cân lỗi -> không có số."""
    total = 0
    for e in events:                                  # đã sắp theo thời gian
        item = e.pop("item_g")
        if e["result"] == "BIN" and e["bin_id"] == scale_bin:
            total += item
            if total > BIN_FULL_G:                    # thùng đầy: đổ rác, cân về 0 (vật vừa bỏ vào cũng đi theo)
                total = 0
        e["weight_g"] = None if rng.random() < 0.03 else total


def draw(path, cls, prob):
    import cv2
    import numpy as np
    img = np.full((480, 640, 3), 245, np.uint8)                       # khay trắng
    cv2.circle(img, (320, 240), 170, COLORS.get(cls, (150, 150, 150)), -1)   # "vật" lớn giữa khay
    cv2.circle(img, (320, 240), 170, (60, 60, 60), 4)
    (w, _), _ = cv2.getTextSize(cls, cv2.FONT_HERSHEY_SIMPLEX, 1.6, 3)
    cv2.putText(img, cls, (320 - w // 2, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (30, 30, 30), 3, cv2.LINE_AA)
    cv2.putText(img, f"{prob * 100:.0f}%", (285, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 30), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if ok:
        buf.tofile(str(path))
    return ok


def make_event(rng, bins_cfg, when, with_images, uploads):
    cfg = bins_cfg
    by_class = {c: b for b in cfg["bins"] for c in b["classes"]}
    classes = list(by_class)
    event_id = "".join(rng.choices("0123456789abcdef", k=32))
    ts = when.isoformat(timespec="seconds")
    roll = rng.random()
    base = {"event_id": event_id, "device_id": "smartbin-01", "ts": ts, "model": "model_int8.tflite",
            "distance_cm": round(rng.uniform(6, 14), 1), "inference_ms": round(rng.uniform(35, 90), 1),
            "received_at": ts}
    base["total_ms"] = round(base["inference_ms"] + rng.uniform(900, 1600))

    cls = rng.choices(classes, [WEIGHTS.get(c, 1) for c in classes])[0]
    prob = round(rng.uniform(0.45, 0.99), 2)
    others = rng.sample([c for c in classes if c != cls], 2)
    rest = round(1 - prob, 2)
    top3 = [[cls, prob], [others[0], round(rest * 0.65, 2)], [others[1], round(rest * 0.3, 2)]]
    bin_probs = {str(b["id"]): 0.0 for b in cfg["bins"]}
    for c, p in top3:
        bin_probs[str(by_class[c]["id"])] = round(bin_probs[str(by_class[c]["id"])] + p, 2)

    if roll < 0.04:                                  # kẹt rác
        base.update(result="ERROR", bin_id=None, bin_name=None, message="Khay còn vật sau khi lật 2 lần (kẹt?)")
    elif roll < 0.09:                                # từ chối (không phải rác / ảnh trống)
        base.update(result="REJECT", bin_id=None, bin_name=None, message="Không thấy vật trên khay")
        prob, top3, bin_probs = 0.0, [], {}
        cls = "none"
    else:
        win = by_class[cls]
        uncertain = bin_probs[str(win["id"])] < cfg["confident_threshold"]
        b = next(b for b in cfg["bins"] if b["id"] == cfg["uncertain_bin"]) if uncertain else win
        base.update(result="BIN", bin_id=b["id"], bin_name=b["name"], uncertain=int(uncertain),
                    message=f"Thùng {b['id']}: {b['name']}" + (" (không chắc chắn)" if uncertain else ""))
    base.setdefault("uncertain", 0)
    base["item_g"] = 0 if cls == "none" else max(5, round(rng.gauss(TYPICAL_G[cls], TYPICAL_G[cls] * 0.25 + 1)))
    base.update(top_class=cls, top_prob=prob, top3=json.dumps(top3), bin_probs=json.dumps(bin_probs), image_path=None)

    if with_images and cls != "none":
        d = uploads / ts[:10]
        d.mkdir(parents=True, exist_ok=True)
        if draw(d / f"{event_id}.jpg", cls, prob):
            base["image_path"] = f"{ts[:10]}/{event_id}.jpg"
    return base


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(BASE / "dev_data"), help="thư mục dữ liệu giả (mặc định server/dev_data)")
    ap.add_argument("--n", type=int, default=60, help="số sự kiện")
    ap.add_argument("--days", type=int, default=3, help="rải đều trong ngần này ngày gần đây")
    ap.add_argument("--seed", type=int, default=1, help="hạt giống ngẫu nhiên (cùng số = cùng dữ liệu)")
    ap.add_argument("--device", default="smartbin-01")
    ap.add_argument("--scale-bin", type=int, default=4, help="thùng đặt cân (số cân = tổng rác trong thùng này)")
    args = ap.parse_args()

    data = Path(args.data).resolve()
    if data == BASE or data in BASE.parents:
        sys.exit(f"Từ chối: {data} chứa dữ liệu thật (smartbin.db). Dùng thư mục khác, vd server/dev_data.")
    if data.exists():
        if any(data.iterdir()) and not (data / "smartbin.db").exists():   # chỉ xoá thư mục do seed.py tạo
            sys.exit(f"Từ chối: {data} không rỗng và không phải dữ liệu giả (không có smartbin.db).")
        shutil.rmtree(data)
    uploads = data / "uploads"
    uploads.mkdir(parents=True)

    try:
        import cv2  # noqa: F401
        with_images = True
    except ImportError:
        with_images = False
        print("Không có opencv -> bỏ qua ảnh (pip install opencv-python-headless)")

    rng = random.Random(args.seed)
    bins_cfg = json.loads((ROOT / "config" / "bins.json").read_text(encoding="utf-8"))
    now = datetime.now().astimezone().replace(microsecond=0)
    span = timedelta(days=args.days).total_seconds()
    times = sorted((now - timedelta(seconds=rng.uniform(60, span)) for _ in range(args.n)))
    events = [make_event(rng, bins_cfg, t, with_images, uploads) for t in times]
    add_scale_weights(events, args.scale_bin, rng)

    con = db.connect(data / "smartbin.db")
    for e in events:
        e["device_id"] = args.device
        con.execute("""INSERT INTO events (event_id, device_id, ts, result, bin_id, bin_name, message, top_class,
                       top_prob, top3, bin_probs, uncertain, distance_cm, inference_ms, total_ms, model, image_path,
                       received_at, weight_g)
                       VALUES (:event_id, :device_id, :ts, :result, :bin_id, :bin_name, :message,
                       :top_class, :top_prob, :top3, :bin_probs, :uncertain, :distance_cm, :inference_ms, :total_ms,
                       :model, :image_path, :received_at, :weight_g)""", e)
    con.execute("INSERT INTO devices VALUES (?, ?, ?)", (args.device, now.isoformat(timespec="seconds"),
                json.dumps({"device_id": args.device, "cpu_temp_c": 52.3, "pending": 0, "uptime_s": 5400})))
    con.commit()
    con.close()
    print(f"Đã tạo {len(events)} sự kiện, thiết bị {args.device} trong {data}")
    print(f"Chạy server:  SMARTBIN_DATA={data} python app.py")


if __name__ == "__main__":
    main()
