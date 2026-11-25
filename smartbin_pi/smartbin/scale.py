"""Cân load cell + HX711: đọc ở luồng nền, đổi ra gram theo config/loadcell.json, đẩy lên web server.

Hiệu chuẩn (tare + hệ số) làm bằng tools/loadcell_test.py (lệnh t, rồi c <gram>), kết quả lưu ở
config/loadcell.json; chương trình chính chỉ đọc file đó. Cách nối dây: xem đầu tools/loadcell_test.py.

Đọc HX711 bằng lgpio (cùng thư viện gpiozero dùng ở bên dưới): bit-bang 24 xung. Xung SCK ở mức cao
> 60 µs thì HX711 tự tắt nguồn nên thỉnh thoảng có mẫu hỏng; lấy trung vị nhiều mẫu để loại chúng."""
import json
import logging
import math
import queue
import random
import statistics
import threading
import time
from pathlib import Path

import requests

from smartbin import ROOT, load_json

log = logging.getLogger(__name__)

CALIB_PATH = "config/loadcell.json"
GAIN_PULSES = 1          # 1 xung thêm = kênh A, gain 128 (2 = kênh B gain 32, 3 = kênh A gain 64)
READY_TIMEOUT_S = 1.0    # HX711 mặc định 10 mẫu/giây, đợi tối đa 1 s cho 1 mẫu
SAT_MIN, SAT_MAX = -0x800000, 0x7FFFFF
DEFAULT_CALIB = {"offset": 0.0, "scale": 1.0, "calibrated": False}


class FakeHx711:
    """Giả lập (--mock/--bench): raw = nền + hệ số * khối lượng + nhiễu."""

    COUNTS_PER_GRAM = 420.0

    def __init__(self):
        self.grams = 0.0
        self.base = 84000

    def read_raw(self):
        time.sleep(0.01)
        return round(self.base + self.COUNTS_PER_GRAM * self.grams + random.gauss(0, 60))

    def close(self):
        pass


class Hx711:
    """HX711 qua lgpio, bit-bang."""

    def __init__(self, dt, sck, chip=None):
        try:
            import lgpio
        except ImportError:
            raise SystemExit("thiếu lgpio: sudo apt install -y python3-lgpio "
                             "(venv phải tạo bằng --system-site-packages, xem README)")
        self.lgpio = lgpio
        self.dt, self.sck = dt, sck
        if chip is None:
            chip = 4 if "Raspberry Pi 5" in self._model() else 0
        try:
            self.h = lgpio.gpiochip_open(chip)
            lgpio.gpio_claim_output(self.h, sck, 0)
            lgpio.gpio_claim_input(self.h, dt)
        except lgpio.error as e:
            raise SystemExit(f"không mở được GPIO (chip {chip}, DT=GPIO{dt}, SCK=GPIO{sck}): {e}\n"
                             f"Chân đang bị chương trình khác giữ? (main.py/calibrate.py/loadcell_test.py) "
                             f"Pi 5 thử --chip 4, Pi cũ --chip 0.")
        lgpio.gpio_write(self.h, sck, 0)  # SCK thấp = HX711 hoạt động (cao > 60 µs = tắt)

    @staticmethod
    def _model():
        try:
            return Path("/proc/device-tree/model").read_bytes().decode(errors="replace")
        except OSError:
            return ""

    def _wait_ready(self):
        """DT xuống thấp = có mẫu mới."""
        lg, deadline = self.lgpio, time.monotonic() + READY_TIMEOUT_S
        while lg.gpio_read(self.h, self.dt):
            if time.monotonic() > deadline:
                raise TimeoutError("DT luôn ở mức cao: kiểm tra dây VCC/GND/DT/SCK của HX711")
            time.sleep(0.001)

    def read_raw(self):
        """1 mẫu 24 bit có dấu."""
        lg, h, sck, dt = self.lgpio, self.h, self.sck, self.dt
        self._wait_ready()
        value = 0
        for _ in range(24):
            lg.gpio_write(h, sck, 1)
            lg.gpio_write(h, sck, 0)
            value = (value << 1) | lg.gpio_read(h, dt)
        for _ in range(GAIN_PULSES):  # chọn kênh/gain cho lần đọc kế tiếp
            lg.gpio_write(h, sck, 1)
            lg.gpio_write(h, sck, 0)
        return value - (1 << 24) if value & 0x800000 else value

    def close(self):
        try:
            self.lgpio.gpio_write(self.h, self.sck, 1)  # SCK cao > 60 µs: đưa HX711 vào chế độ tiết kiệm điện
            time.sleep(0.0002)
            self.lgpio.gpiochip_close(self.h)
        except Exception:
            pass


def sample(sensor, n=5):
    """Trung vị n mẫu hợp lệ (bỏ mẫu bão hòa / 0xFFFFFF do xung SCK bị kéo dài)."""
    vals = []
    for _ in range(n * 3):
        raw = sensor.read_raw()
        if raw in (-1, SAT_MIN, SAT_MAX):  # toàn bit 1, hoặc chạm trần
            continue
        vals.append(raw)
        if len(vals) == n:
            break
    if not vals:
        raise RuntimeError("toàn mẫu bão hòa/-1: HX711 chưa nối load cell, dây E+/E-/A+/A- lỏng hoặc nối nhầm")
    return statistics.median(vals)


def load_calib():
    try:
        return {**DEFAULT_CALIB, **load_json(CALIB_PATH)}
    except FileNotFoundError:
        return dict(DEFAULT_CALIB)


def save_calib(calib):
    path = ROOT / CALIB_PATH
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(calib, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)          # ghi nguyên tử: mất điện giữa chừng không hỏng file hiệu chuẩn


def to_grams(raw, calib):
    return (raw - calib["offset"]) / calib["scale"]


class Scale:
    """Luồng nền đọc cân liên tục; .grams() trả số mới nhất (None nếu đang lỗi / chưa có).

    Có server_url thì đẩy {grams, calibrated} lên server mỗi push_s giây để web hiện cạnh video."""

    def __init__(self, sensor, server_url=None, api_key=None, device_id=None, push_s=1.0, samples=3):
        self.sensor, self.samples, self.push_s = sensor, samples, push_s
        self.calib = load_calib()
        self._val, self._ts, self._err = None, 0.0, None
        self._raw = None
        self._msg = None                    # kết quả lệnh tare/hiệu chuẩn gần nhất, báo lên web
        self.commands = queue.Queue()       # lệnh từ web, chạy ở luồng đọc (chỉ luồng đó được đụng vào cảm biến)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if not self.calib["calibrated"]:
            log.warning("Cân chưa hiệu chuẩn (%s): số hiển thị là số đếm thô. Chạy tools/loadcell_test.py, "
                        "gõ t rồi c <gram>", CALIB_PATH)
        threading.Thread(target=self._read_loop, daemon=True, name="scale").start()
        if server_url:
            self.url = f"{server_url.rstrip('/')}/api/weight/{device_id}"
            self.http = requests.Session()
            self.http.headers["X-API-Key"] = api_key
            threading.Thread(target=self._push_loop, daemon=True, name="scale-push").start()

    def _read_loop(self):
        err_before = None
        while not self._stop.is_set():
            self._run_commands()
            try:
                raw = sample(self.sensor, self.samples)
                g = to_grams(raw, self.calib)
                with self._lock:
                    self._val, self._raw, self._ts, self._err = g, raw, time.monotonic(), None
                if err_before:
                    log.info("Cân: đọc lại được (%.1f g)", g)
                err_before = None
            except Exception as e:          # luồng cân không được chết vì lỗi bất kỳ
                with self._lock:
                    self._val, self._err = None, str(e)
                if str(e) != err_before:
                    log.warning("Cân: lỗi đọc HX711: %s", e)
                err_before = str(e)
                self._stop.wait(1.0)

    def command(self, cmd):
        """Nhận lệnh từ web (luồng khác): {"cmd": "scale_tare"} hoặc {"cmd": "scale_calib", "grams": 500}."""
        self.commands.put(cmd)

    def _say(self, ok, text):
        (log.info if ok else log.warning)("Cân (web): %s", text)
        with self._lock:
            self._msg = {"ok": ok, "text": text, "ts": time.time()}

    def _run_commands(self):
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd.get("cmd") == "scale_tare":
                    self.calib["offset"] = sample(self.sensor, 10)
                    save_calib(self.calib)
                    self._say(True, f"đã tare, offset = {self.calib['offset']:.0f}")
                elif cmd.get("cmd") == "scale_calib":
                    grams = float(cmd.get("grams"))
                    if not math.isfinite(grams) or grams <= 0:
                        raise ValueError("khối lượng phải > 0")
                    raw = sample(self.sensor, 10)
                    delta = raw - self.calib["offset"]
                    if abs(delta) < 200:
                        raise ValueError(f"chênh so với điểm 0 chỉ {delta:.0f}: chưa đặt vật lên cân, "
                                         f"chưa tare, hoặc load cell nối sai")
                    self.calib.update(scale=delta / grams, calibrated=True)
                    save_calib(self.calib)
                    self._say(True, f"đã hiệu chuẩn, hệ số = {self.calib['scale']:.3f} số đếm/gram"
                                    + (" (âm: nên đảo dây A+ A-)" if self.calib["scale"] < 0 else ""))
            except Exception as e:      # lệnh sai không được làm chết luồng cân
                self._say(False, f"lỗi: {e}")

    def grams(self):
        with self._lock:
            fresh = self._val is not None and time.monotonic() - self._ts < 3
            return round(self._val, 1) if fresh else None

    def _push_loop(self):
        ok_before = None
        while not self._stop.wait(self.push_s):
            with self._lock:
                err, raw, msg = self._err, self._raw, self._msg
            try:
                r = self.http.post(self.url, timeout=3, json={
                    "grams": self.grams(), "calibrated": self.calib["calibrated"], "error": err,
                    "raw": raw, "offset": self.calib["offset"], "scale": self.calib["scale"], "msg": msg})
                ok = r.ok
            except requests.RequestException:
                ok = False
            if ok != ok_before and ok_before is not None:
                log.log(logging.INFO if ok else logging.WARNING,
                        "Cân: %s server", "đã gửi lại được lên" if ok else "không gửi được lên")
            ok_before = ok

    def close(self):
        self._stop.set()
        self.sensor.close()
