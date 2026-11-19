"""Cụm thả rác 2 servo: A ngang (pan, xoay khay sang 45°/135°) + B dọc (tilt, lật khay đổ rác).

Xung servo do bộ PWM phần cứng của chip tạo (không rung như PWM phần mềm), điều khiển qua
/sys/class/pwm, không cần thư viện hay daemon. Bật một lần: thêm vào /boot/firmware/config.txt
    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
rồi reboot (GPIO12 = kênh 0, GPIO13 = kênh 1). Đổi sang chân khác thì dùng 18/19, xem PIN_TO_PWM_CHANNEL.

Logic điều khiển giống hệt tools/servo_test.py (bản đã test chạy ổn trên Pi):
- khởi động: đặt thẳng từng servo về vị trí nghỉ (lần lượt, chờ 1.2 s mỗi cái), rồi chạy 1 chu trình
  xoay + lật vào 1 thùng và về nghỉ bằng đường quay mượt như lúc chạy thật, rồi ngắt xung;
- thả rác: xoay ngang -> chờ settle_s -> lật dọc -> chờ hold_s -> về nghỉ;
- về nghỉ: dọc về ngang trước, rồi mới xoay ngang về giữa, ngắt xung (không cho Ctrl+C cắt ngang);
- quay mượt từng servo một (smootherstep), đứng yên thì ngắt xung."""
import math
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path

MIN_ANGLE, MAX_ANGLE = 0, 180
SMOOTHERSTEP_PEAK = 1.875  # đạo hàm lớn nhất của 6t^5-15t^4+10t^3 (tốc độ đỉnh / tốc độ trung bình)
STARTUP_WAIT_S = 1.2       # chưa biết servo đang ở đâu: chờ đủ thời gian quay tối đa rồi mới ngắt xung

PWM_ROOT = Path("/sys/class/pwm")
PWM_PERIOD_NS = 20_000_000  # 50 Hz
PIN_TO_PWM_CHANNEL = {12: 0, 13: 1, 18: 0, 19: 1}  # chân có PWM phần cứng -> kênh
OVERLAY_HINT = ("thêm vào /boot/firmware/config.txt rồi reboot:\n"
                "    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4")
SERVO_KEYS = ("min_pulse_ms", "max_pulse_ms", "pan_rest", "tilt_rest", "tilt_delta", "tilt_sign",
              "speed_dps", "update_hz", "settle_s", "hold_s")


class FakePwm:
    """Giả lập (--mock/--bench): kiểm tra xung hợp lệ 500..2500 µs, 0 = ngắt."""

    def __init__(self, pins):
        self.pulses = {name: 0 for name in pins}

    def set_pulse_us(self, name, us):
        if not 500 <= us <= 2500:
            raise ValueError(f"xung {us} µs ngoài 500..2500")
        self.pulses[name] = us

    def off(self, name):
        self.pulses[name] = 0

    def close(self):
        pass


class SysfsPwm:
    """PWM phần cứng qua /sys/class/pwm (kernel overlay pwm-2chan), chu kỳ 20 ms."""

    def __init__(self, pins, chip=None):
        self.chip_dir = self._find_chip(chip)
        self.dirs, self.on = {}, set()
        channels = {}
        for name, pin in pins.items():
            if pin not in PIN_TO_PWM_CHANNEL:
                raise SystemExit(f"GPIO{pin} không có PWM phần cứng (dùng GPIO 12, 13, 18 hoặc 19)")
            channels[name] = PIN_TO_PWM_CHANNEL[pin]
        if len(set(channels.values())) != len(channels):
            raise SystemExit("hai servo trùng kênh PWM phần cứng, dùng GPIO12 + GPIO13 (hoặc 18 + 19)")
        for name, ch in channels.items():
            self.dirs[name] = self._open(ch)

    @staticmethod
    def _find_chip(chip):
        if chip is not None:
            d = PWM_ROOT / f"pwmchip{chip}"
            if not d.is_dir():
                raise SystemExit(f"không thấy {d}")
            return d
        for d in sorted(PWM_ROOT.glob("pwmchip*")):
            try:
                if int((d / "npwm").read_text()) >= 2:
                    return d
            except (OSError, ValueError):
                continue
        raise SystemExit("chưa bật PWM phần cứng, " + OVERLAY_HINT)

    @staticmethod
    def _write(path, value):
        # ngay sau export, udev cần chút thời gian mới cấp quyền ghi cho file
        for attempt in range(20):
            try:
                path.write_text(str(value))
                return
            except PermissionError:
                if attempt == 19:
                    raise SystemExit(f"không ghi được {path}: thêm user vào nhóm gpio "
                                     f"(sudo usermod -aG gpio $USER, đăng nhập lại) hoặc chạy bằng sudo")
                time.sleep(0.05)
            except OSError as e:
                raise SystemExit(f"PWM sysfs lỗi khi ghi {path}: {e}")

    def _open(self, channel):
        d = self.chip_dir / f"pwm{channel}"
        if not d.is_dir():
            self._write(self.chip_dir / "export", channel)
            for _ in range(20):
                if d.is_dir():
                    break
                time.sleep(0.05)
            else:
                raise SystemExit(f"export kênh PWM {channel} thất bại ({d} không xuất hiện)")
        # đưa về trạng thái an toàn trước khi đặt lại chu kỳ (period không được nhỏ hơn duty cũ)
        self._write(d / "enable", 0)
        self._write(d / "duty_cycle", 0)
        self._write(d / "period", PWM_PERIOD_NS)
        return d

    def set_pulse_us(self, name, us):
        if not 500 <= us <= 2500:
            raise ValueError(f"xung {us} µs ngoài 500..2500")
        d = self.dirs[name]
        self._write(d / "duty_cycle", int(us * 1000))
        if name not in self.on:
            self._write(d / "enable", 1)
            self.on.add(name)

    def off(self, name):
        if name in self.on:
            self._write(self.dirs[name] / "enable", 0)
            self.on.discard(name)

    def close(self):
        for name in list(self.on):
            self.off(name)


@contextmanager
def ignore_sigint():
    """Chặn Ctrl+C lần 2 khi đang về nghỉ, để không bỏ dở lúc máng còn nghiêng (như servo_test.py)."""
    if threading.current_thread() is not threading.main_thread():
        yield           # signal.signal chỉ gọi được ở luồng chính
        return
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old)


def validate(servo, bins):
    """Kiểm tra mục "servo" của hardware.json và góc của từng thùng trong bins.json."""
    try:
        nums = [servo[k] for k in SERVO_KEYS]
        poses = [(b["id"], b["pan"], b["tilt_dir"]) for b in bins]
    except KeyError as e:
        raise SystemExit(f"cấu hình servo thiếu khoá {e} (hardware.json -> servo, bins.json -> bins[].pan/tilt_dir)")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in nums):
        raise SystemExit("hardware.json -> servo: các giá trị phải là số hữu hạn")
    if servo["speed_dps"] <= 0 or not 10 <= servo["update_hz"] <= 200:
        raise SystemExit("speed_dps phải > 0 và update_hz trong 10..200")
    if servo["tilt_delta"] < 0 or servo["tilt_sign"] not in (-1, 1):
        raise SystemExit("tilt_delta phải >= 0 (đổi chiều bằng tilt_sign = 1 hoặc -1)")
    if not 0.5 <= servo["min_pulse_ms"] < servo["max_pulse_ms"] <= 2.5:
        raise SystemExit("cần 0.5 <= min_pulse_ms < max_pulse_ms <= 2.5")
    for name in ("pan_rest", "tilt_rest"):
        if not MIN_ANGLE <= servo[name] <= MAX_ANGLE:
            raise SystemExit(f"{name}={servo[name]:g} ngoài {MIN_ANGLE}..{MAX_ANGLE}")
    if not poses or any(d not in (-1, 1) for _, _, d in poses):
        raise SystemExit("bins: cần ít nhất 1 thùng, tilt_dir phải là 1 hoặc -1")
    for bid, pan, d in poses:
        for a in (pan, tilt_angle(servo, d)):
            if not MIN_ANGLE <= a <= MAX_ANGLE:
                raise SystemExit(f"thùng {bid}: góc {a:g} ngoài {MIN_ANGLE}..{MAX_ANGLE}")


def tilt_angle(servo, direction):
    return servo["tilt_rest"] + direction * servo["tilt_sign"] * servo["tilt_delta"]


class PanTilt:
    """Hai servo qua PWM phần cứng, mỗi servo quay mượt (tăng/giảm tốc) để không giật, không quá nhanh.

    servo: mục "servo" của hardware.json (dict dùng chung, đổi giá trị lúc chạy được, vd tilt_delta)."""

    def __init__(self, pins, servo, mock=False):
        self.pins = {"pan": pins["servo_pan"], "tilt": pins["servo_tilt"]}
        # giới hạn xung 500..2500 µs để không đẩy servo quá biên
        self.min_us = max(500, servo["min_pulse_ms"] * 1000)
        self.max_us = min(2500, servo["max_pulse_ms"] * 1000)
        self.pwm = FakePwm(self.pins) if mock else SysfsPwm(self.pins, servo.get("pwm_chip"))
        self.cfg = servo
        self.pos = {}           # góc hiện tại (theo lệnh gần nhất)
        self.on_moved = None    # callback(name, angle) sau mỗi lần quay xong (giả lập dùng)

    # ---------- mức thấp ----------
    def _set(self, name, angle):
        us = self.min_us + (angle - MIN_ANGLE) / (MAX_ANGLE - MIN_ANGLE) * (self.max_us - self.min_us)
        self.pwm.set_pulse_us(name, round(us))
        self.pos[name] = angle

    def snap(self, name, angle):
        """Đặt thẳng góc, không làm mượt. Chỉ dùng lúc khởi động (chưa biết servo đang ở đâu)."""
        self._set(name, angle)

    def move(self, name, target):
        """Quay mượt tới góc target: tăng tốc dần, giảm tốc dần (smootherstep).

        Tốc độ đỉnh (giữa đoạn) = speed_dps. Góc tính theo đồng hồ chứ không đếm bước,
        nên sleep trễ nhẹ cũng không làm chuyển động bị khựng."""
        target = max(MIN_ANGLE, min(MAX_ANGLE, target))
        start = self.pos[name]
        dist = abs(target - start)
        if dist >= 1e-6:
            duration = dist * SMOOTHERSTEP_PEAK / self.cfg["speed_dps"]
            period = 1.0 / self.cfg["update_hz"]
            t0 = time.monotonic()
            while True:
                t = (time.monotonic() - t0) / duration
                if t >= 1:
                    break
                s = t * t * t * (t * (6 * t - 15) + 10)
                self._set(name, start + (target - start) * s)
                time.sleep(period)
            self._set(name, target)
        if self.on_moved:
            self.on_moved(name, target)

    def release(self):
        # ngắt xung khi đứng yên -> servo không rung/kêu, đỡ nóng
        for name in self.pins:
            self.pwm.off(name)

    # ---------- chu trình ----------
    @property
    def ready(self):
        return len(self.pos) == 2

    @property
    def at_rest(self):
        return self.pos.get("tilt") == self.cfg["tilt_rest"]

    def startup(self, pan=None, tilt_dir=None):
        """Đưa servo về vị trí nghỉ lúc mới bật (chưa biết servo đang ở đâu).

        Chỉ nhảy thẳng (snap) về nghỉ thì trên máy thật khay KHÔNG về đúng chỗ, còn sau một chu trình
        thả rác (quay mượt tới thùng rồi home()) thì về đúng. Nên: snap từng servo một (không để 2 servo
        cùng kéo dòng lớn một lúc), rồi chạy 1 chu trình giống hệt lúc thả rác vào thùng (pan, tilt_dir)
        để kết thúc bằng đúng đường home() đã chạy ổn. pan=None: chỉ snap như cũ."""
        self.snap("tilt", self.cfg["tilt_rest"])
        time.sleep(STARTUP_WAIT_S)
        self.snap("pan", self.cfg["pan_rest"])
        time.sleep(STARTUP_WAIT_S)
        if pan is None:
            self.release()
            return
        self.dump(pan, tilt_dir)        # dump() luôn kết thúc bằng home() + ngắt xung

    def home(self):
        """Dọc về ngang trước, rồi mới xoay ngang về giữa (tránh quét khay khi đang nghiêng), ngắt xung."""
        with ignore_sigint():
            self.move("tilt", self.cfg["tilt_rest"])
            self.move("pan", self.cfg["pan_rest"])
            self.release()

    def dump(self, pan, tilt_dir):
        """Xoay khay tới pan, lật theo tilt_dir để rác trượt xuống thùng, rồi về nghỉ.
        Lỗi / Ctrl+C giữa chừng vẫn về nghỉ."""
        try:
            self.move("pan", pan)
            time.sleep(self.cfg["settle_s"])
            self.move("tilt", tilt_angle(self.cfg, tilt_dir))
            time.sleep(self.cfg["hold_s"])
        finally:
            self.home()

    def close(self):
        try:
            if self.ready:
                self.home()
        except KeyboardInterrupt:
            pass        # đang tắt rồi: Ctrl+C lần nữa lúc về nghỉ không cần báo lại
        finally:
            self.release()
            self.pwm.close()
