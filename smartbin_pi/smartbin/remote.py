"""Đồng bộ với web server mỗi giây (POST /api/sync/<device_id>):
- báo khoảng cách siêu âm hiện tại để trang chủ hiện cạnh video và số cân;
- báo danh sách thông số chỉnh được + giá trị hiện tại cho trang /config;
- nhận cấu hình mới từ web: kiểm tra, áp dụng NGAY lúc đang chạy (sửa thẳng vào dict cấu hình dùng chung),
  lưu vào config/local.json (không commit, đè lên hardware.json / bins.json lúc khởi động, xem
  smartbin.load_config), rồi báo kết quả lại cho web. File gốc trong git không bao giờ bị sửa;
- nhận lệnh thử servo (thả vào thùng N / về nghỉ), controller chạy khi đang rảnh.
Log của chương trình KHÔNG đẩy lên web (xem logs/app.log hoặc journalctl trên Pi)."""
import json
import logging
import os
import threading
from pathlib import Path

import requests

from smartbin import LOCAL_FILE, ROOT
from smartbin.servo import EASINGS, MOVE_MS_RANGE, validate as validate_servo

log = logging.getLogger(__name__)
SYNC_S = 1.0
RETRY_S = 5.0


def _num(lo, hi, step, integer=False):
    return {"kind": "int" if integer else "number", "min": lo, "max": hi, "step": step}


def _sel(*options):
    return {"kind": "select", "options": list(options)}


# key: "<nhóm>.<khoá>" (cũng là khoá trong config/local.json, xem smartbin.apply_local);
# servo.* -> hardware.json["servo"], hw.* -> hardware.json, cls.* -> bins.json, bin.<id>.* -> bins.json["bins"][id]
BASE_FIELDS = [
    ("Servo: chuyển động", "servo.easing", "Kiểu easing", _sel(*EASINGS)),
    ("Servo: chuyển động", "servo.pan_ms", "Thời gian xoay ngang (ms)", _num(*MOVE_MS_RANGE, 10, True)),
    ("Servo: chuyển động", "servo.tilt_ms", "Thời gian lật dọc (ms)", _num(*MOVE_MS_RANGE, 10, True)),
    ("Servo: chuyển động", "servo.update_hz", "Tần số cập nhật (Hz)", _num(10, 200, 1, True)),
    ("Servo: chuyển động", "servo.settle_s", "Chờ sau khi xoay ngang (s)", _num(0, 5, 0.05)),
    ("Servo: chuyển động", "servo.hold_s", "Giữ khay nghiêng (s)", _num(0, 5, 0.05)),
    ("Servo: góc", "servo.pan_rest", "Ngang: góc nghỉ (°)", _num(0, 180, 0.5)),
    ("Servo: góc", "servo.tilt_rest", "Dọc: góc nghỉ (°)", _num(0, 180, 0.5)),
    ("Servo: góc", "servo.tilt_delta", "Dọc: biên độ lật (°)", _num(0, 90, 0.5)),
    ("Servo: góc", "servo.tilt_sign", "Dọc: đảo chiều", _sel(1, -1)),
    ("Servo: góc", "servo.pan_approach", "Ngang: về nghỉ từ phía (1 góc nhỏ, -1 góc lớn, 0 tắt)", _sel(0, 1, -1)),
    ("Servo: góc", "servo.tilt_approach", "Dọc: về nghỉ từ phía (1 góc nhỏ, -1 góc lớn, 0 tắt)", _sel(0, 1, -1)),
    ("Servo: góc", "servo.approach_deg", "Quay vượt góc nghỉ (°)", _num(0, 45, 0.5)),
    ("Servo: góc", "servo.min_pulse_ms", "Xung 0° (ms)", _num(0.5, 2.5, 0.01)),
    ("Servo: góc", "servo.max_pulse_ms", "Xung 180° (ms)", _num(0.5, 2.5, 0.01)),
    ("Phát hiện vật", "hw.trigger_mode", "Cách kích hoạt", _sel("hand", "object")),
    ("Phát hiện vật", "hw.trigger_cm", "Ngưỡng có vật (cm)", _num(1, 200, 0.5)),
    ("Phát hiện vật", "hw.clear_cm", "Ngưỡng khay trống (cm)", _num(1, 200, 0.5)),
    ("Phát hiện vật", "hw.stable_ms", "Ổn định / rút tay (ms)", _num(0, 10000, 50, True)),
    ("Phát hiện vật", "hw.near_hold_ms", "Vật to: chờ (ms, 0 = tắt)", _num(0, 20000, 100, True)),
    ("Phát hiện vật", "hw.settle_ms", "Chờ trước khi chụp (ms)", _num(0, 10000, 50, True)),
    ("Phát hiện vật", "hw.cooldown_ms", "Nghỉ sau mỗi lượt (ms)", _num(0, 20000, 100, True)),
    ("Phát hiện vật", "hw.distance_log_s", "In log siêu âm mỗi (s, 0 = tắt)", _num(0, 60, 0.5)),
    ("Phân loại", "cls.confident_threshold", "Ngưỡng chắc chắn", _num(0, 1, 0.01)),
]


class Remote:
    def __init__(self, server_url, api_key, device_id, sensor, servo_cfg, hw, decider, config_dir,
                 on_command=None):
        """sensor: cảm biến siêu âm (đọc khoảng cách để báo lên web);
        servo_cfg, hw: dict cấu hình ĐANG DÙNG (cùng object với PanTilt.cfg, Controller.hw);
        decider: Decider (bins + ngưỡng); on_command(cmd): đẩy lệnh thử servo cho controller."""
        self.url = f"{server_url.rstrip('/')}/api/sync/{device_id}"
        self.http = requests.Session()
        self.http.headers["X-API-Key"] = api_key
        self.sensor = sensor
        self.servo, self.hw, self.decider = servo_cfg, hw, decider
        self.cfg_dir = ROOT / config_dir
        self.on_command = on_command
        self.fields = self._build_fields()
        self.send_config = True            # gửi danh sách thông số ở lần đầu và sau mỗi lần đổi
        self.result = None                 # kết quả áp dụng cấu hình gần nhất, báo lại cho web
        self.last_cfg_id = None
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="remote").start()

    # ---------- thông số ----------
    def _build_fields(self):
        out = [{"group": g, "key": k, "label": lbl, **spec} for g, k, lbl, spec in BASE_FIELDS]
        for bid, b in sorted(self.decider.bins.items()):
            g = f"Thùng {bid}: {b['name']}"
            out.append({"group": g, "key": f"bin.{bid}.pan", "label": "Góc xoay ngang (°)", **_num(0, 180, 0.5)})
            out.append({"group": g, "key": f"bin.{bid}.tilt_dir", "label": "Chiều lật", **_sel(-1, 1)})
        return out

    def _get(self, key):
        grp, _, rest = key.partition(".")
        if grp == "servo":
            return self.servo.get(rest, 0 if rest in ("pan_approach", "tilt_approach", "approach_deg") else None)
        if grp == "hw":
            return self.hw.get(rest)
        if grp == "cls":
            return self.decider.conf_th
        bid, _, k = rest.partition(".")
        return self.decider.bins[int(bid)][k]

    def values(self):
        return {f["key"]: self._get(f["key"]) for f in self.fields}

    @staticmethod
    def _coerce(f, v):
        if f["kind"] == "select":
            for o in f["options"]:
                if str(o) == str(v):
                    return o
            raise ValueError(f"{f['label']}: chọn một trong {f['options']}")
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{f['label']}: phải là số") from None
        if not (f["min"] <= x <= f["max"]):
            raise ValueError(f"{f['label']}: {x:g} ngoài {f['min']}..{f['max']}")
        if f["kind"] == "int" or x.is_integer():
            x = int(round(x))
        return x

    def apply(self, values):
        """Kiểm tra toàn bộ rồi mới áp dụng (sai 1 thông số thì không đổi gì). Trả về danh sách thay đổi."""
        by_key = {f["key"]: f for f in self.fields}
        new = {}
        for k, v in values.items():
            if k not in by_key:
                raise ValueError(f"không có thông số {k}")
            x = self._coerce(by_key[k], v)
            if x != self._get(k):
                new[k] = x
        if not new:
            return []

        servo = {**self.servo, **{k[6:]: v for k, v in new.items() if k.startswith("servo.")}}
        bins = [{**b, **{k.split(".")[2]: v for k, v in new.items() if k.startswith(f"bin.{bid}.")}}
                for bid, b in sorted(self.decider.bins.items())]
        try:
            validate_servo(servo, bins)
        except SystemExit as e:
            raise ValueError(str(e)) from None
        trig = new.get("hw.trigger_cm", self.hw["trigger_cm"])
        clear = new.get("hw.clear_cm", self.hw["clear_cm"])
        if clear < trig:
            raise ValueError(f"ngưỡng khay trống ({clear:g}) phải >= ngưỡng có vật ({trig:g})")

        changes = [f"{k} {self._get(k)} -> {v}" for k, v in new.items()]
        for k, v in new.items():          # gán từng khoá: luồng điều khiển đọc dict mỗi lần dùng nên nhận ngay
            grp, _, rest = k.partition(".")
            if grp == "servo":
                self.servo[rest] = v
            elif grp == "hw":
                self.hw[rest] = v
            elif grp == "cls":
                self.decider.conf_th = v
            else:
                bid, _, key = rest.partition(".")
                self.decider.bins[int(bid)][key] = v
        self._save(new)
        return changes

    def _save(self, new):
        """Ghi thêm các thông số vừa đổi vào config/local.json (giữ các thông số đã chỉnh trước đó)."""
        path = self.cfg_dir / LOCAL_FILE
        try:
            local = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            log.warning("%s hỏng, ghi lại từ đầu", path)
            local = {}
        if not isinstance(local, dict):
            local = {}
        local.update(new)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sorted(local.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)          # ghi nguyên tử: mất điện giữa chừng không hỏng file cấu hình

    # ---------- đồng bộ ----------
    def _distance(self):
        """Khoảng cách siêu âm (cm) để hiện trên web; None nếu chưa đo được. Không đụng tới log/cảnh báo
        của luồng điều khiển (peek_cm không in gì)."""
        try:
            read = getattr(self.sensor, "peek_cm", None) or self.sensor.distance_cm
            d = read()
            return round(d, 1) if d is not None else None
        except Exception:
            return None

    def _sync_once(self):
        body = {"distance_cm": self._distance()}
        if self.send_config:
            body["config"] = {"fields": self.fields, "values": self.values()}
        if self.result:
            body["result"] = self.result
        r = self.http.post(self.url, json=body, timeout=5)
        r.raise_for_status()
        resp = r.json()
        if "config" in body:
            self.send_config = False
        if body.get("result") is self.result:
            self.result = None
        if resp.get("need_config"):
            self.send_config = True

        pending = resp.get("config")
        if pending and pending.get("id") != self.last_cfg_id:
            self.last_cfg_id = pending["id"]
            try:
                changes = self.apply(pending.get("values") or {})
                log.info("Cấu hình từ web: %s", "; ".join(changes) if changes else "không có gì thay đổi")
                self.result = {"id": pending["id"], "ok": True, "changes": changes}
            except Exception as e:
                log.warning("Cấu hình từ web bị từ chối: %s", e)
                self.result = {"id": pending["id"], "ok": False, "error": str(e)}
            self.send_config = True
        for cmd in resp.get("commands") or []:
            if self.on_command:
                self.on_command(cmd)

    def _loop(self):
        ok_before = None
        while not self._stop.is_set():
            try:
                self._sync_once()
                ok, why = True, ""
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                ok, why = False, f"HTTP {code}" + (" (sai api_key?)" if code == 401 else "")
            except Exception as e:      # luồng đồng bộ không được chết vì lỗi bất kỳ
                ok, why = False, type(e).__name__
            if ok != ok_before:
                if not ok:
                    log.warning("Đồng bộ cấu hình với server lỗi: %s", why)
                elif ok_before is False:
                    log.info("Đồng bộ cấu hình: đã kết nối lại server")
                ok_before = ok
            self._stop.wait(SYNC_S if ok else RETRY_S)

    def close(self):
        self._stop.set()
