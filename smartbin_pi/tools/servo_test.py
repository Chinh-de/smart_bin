"""Kiểm tra riêng 2 servo: ngang (pan, chọn bên) + dọc (tilt, nghiêng đổ rác vào thùng).

    python tools/servo_test.py            # trên Pi
    python tools/servo_test.py --mock     # chạy thử logic trên máy không có Pi
    python tools/servo_test.py --speed 30 # tốc độ đỉnh 30 độ/giây (chậm hơn)

Lệnh:
    1..4      đưa rác vào thùng đó rồi về vị trí nghỉ
    h         về vị trí nghỉ
    p <góc>   xoay servo ngang tới góc (0..180)      vd: p 45
    v <góc>   xoay servo dọc tới góc (0..180)        vd: v 128
    d <độ>    đổi biên độ nghiêng dọc (tilt_delta)   vd: d 40
    s         in cấu hình hiện tại
    q         về vị trí nghỉ rồi thoát

Chân GPIO lấy từ config/hardware.json (servo_chute = ngang, servo_tray = dọc).
Góc/tốc độ/thời gian lấy từ config/servo_test.json.

Xung servo do bộ PWM phần cứng của chip tạo (không rung như PWM phần mềm), điều khiển qua
/sys/class/pwm, không cần thư viện hay daemon. Bật một lần: thêm vào /boot/firmware/config.txt
    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
rồi reboot (GPIO12 = kênh 0, GPIO13 = kênh 1). Đổi sang chân khác thì dùng 18/19, xem PIN_TO_PWM_CHANNEL."""
import argparse
import math
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import load_json  # noqa: E402

MIN_ANGLE, MAX_ANGLE = 0, 180
SMOOTHERSTEP_PEAK = 1.875  # đạo hàm lớn nhất của 6t^5-15t^4+10t^3 (tốc độ đỉnh / tốc độ trung bình)


PWM_ROOT = Path("/sys/class/pwm")
PWM_PERIOD_NS = 20_000_000  # 50 Hz
PIN_TO_PWM_CHANNEL = {12: 0, 13: 1, 18: 0, 19: 1}  # chân có PWM phần cứng -> kênh
OVERLAY_HINT = ("thêm vào /boot/firmware/config.txt rồi reboot:\n"
                "    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4")


class FakePwm:
    """Giả lập cho --mock: kiểm tra xung hợp lệ 500..2500 µs, 0 = ngắt."""

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


class PanTilt:
    """Hai servo qua PWM phần cứng, mỗi servo quay mượt (tăng/giảm tốc) để không giật, không quá nhanh."""

    def __init__(self, hw, cfg, mock=False):
        p = hw["pins"]
        self.pins = {"pan": p["servo_chute"], "tilt": p["servo_tray"]}
        # giới hạn xung 500..2500 µs để không đẩy servo quá biên
        self.min_us = max(500, hw["servo_min_pulse_ms"] * 1000)
        self.max_us = min(2500, hw["servo_max_pulse_ms"] * 1000)
        self.pwm = FakePwm(self.pins) if mock else SysfsPwm(self.pins, cfg.get("pwm_chip"))
        self.cfg = cfg
        self.pos = {}  # góc hiện tại (theo lệnh gần nhất)

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
        if dist < 1e-6:
            return
        duration = dist * SMOOTHERSTEP_PEAK / self.cfg["speed_dps"]
        period = 1.0 / self.cfg["update_hz"]
        t0 = time.monotonic()
        while True:
            t = (time.monotonic() - t0) / duration
            if t >= 1:
                break
            s = t * t * t * (t * (6 * t - 15) + 10)
            angle = start + (target - start) * s
            self._set(name, angle)
            time.sleep(period)
        self._set(name, target)

    def release(self):
        # ngắt xung khi đứng yên -> servo không rung/kêu, đỡ nóng
        for name in self.pins:
            self.pwm.off(name)

    def close(self):
        self.release()
        self.pwm.close()


def tilt_angle(cfg, direction):
    return cfg["tilt_rest"] + direction * cfg["tilt_sign"] * cfg["tilt_delta"]


@contextmanager
def ignore_sigint():
    """Chặn Ctrl+C lần 2 khi đang về nghỉ, để không bỏ dở lúc máng còn nghiêng."""
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old)


def go_home(pt, cfg):
    # dọc về giữa trước, rồi mới xoay ngang (tránh quét máng khi đang nghiêng)
    with ignore_sigint():
        pt.move("tilt", cfg["tilt_rest"])
        pt.move("pan", cfg["pan_rest"])
        pt.release()


def dump_to_bin(pt, cfg, b):
    pan, tilt = b["pan"], tilt_angle(cfg, b["tilt_dir"])
    print(f"thùng {b['id']}: ngang {pan}° -> dọc {tilt:g}° -> về nghỉ")
    try:
        pt.move("pan", pan)
        time.sleep(cfg["settle_s"])
        pt.move("tilt", tilt)
        time.sleep(cfg["hold_s"])
    finally:
        go_home(pt, cfg)  # lỗi/Ctrl+C giữa chừng vẫn về nghỉ


def parse_angle(arg):
    try:
        angle = float(arg)
    except (TypeError, ValueError):
        print("góc phải là số, vd: p 45")
        return None
    if not MIN_ANGLE <= angle <= MAX_ANGLE:
        print(f"góc phải trong {MIN_ANGLE}..{MAX_ANGLE}")
        return None
    return angle


def validate(cfg):
    try:
        nums = [cfg[k] for k in ("pan_rest", "tilt_rest", "tilt_delta", "tilt_sign",
                                 "speed_dps", "update_hz", "settle_s", "hold_s")]
        bins = [(b["id"], b["pan"], b["tilt_dir"]) for b in cfg["bins"]]
    except KeyError as e:
        raise SystemExit(f"servo_test.json thiếu khoá {e}")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in nums):
        raise SystemExit("servo_test.json: các giá trị cấu hình phải là số hữu hạn")
    if cfg["speed_dps"] <= 0 or not 10 <= cfg["update_hz"] <= 200:
        raise SystemExit("speed_dps phải > 0 và update_hz trong 10..200")
    if cfg["tilt_delta"] < 0 or cfg["tilt_sign"] not in (-1, 1):
        raise SystemExit("tilt_delta phải >= 0 (đổi chiều bằng tilt_sign = 1 hoặc -1)")
    for name in ("pan_rest", "tilt_rest"):
        if not MIN_ANGLE <= cfg[name] <= MAX_ANGLE:
            raise SystemExit(f"{name}={cfg[name]:g} ngoài {MIN_ANGLE}..{MAX_ANGLE}")
    if not bins or any(d not in (-1, 1) for _, _, d in bins):
        raise SystemExit("bins: cần ít nhất 1 thùng, tilt_dir phải là 1 hoặc -1")
    for b in cfg["bins"]:
        for a in (b["pan"], tilt_angle(cfg, b["tilt_dir"])):
            if not MIN_ANGLE <= a <= MAX_ANGLE:
                raise SystemExit(f"thùng {b['id']}: góc {a:g} ngoài {MIN_ANGLE}..{MAX_ANGLE}")


def main():
    ap = argparse.ArgumentParser(description="Test servo ngang + dọc")
    ap.add_argument("--mock", action="store_true", help="không dùng PWM thật")
    ap.add_argument("--speed", type=float, help="ghi đè speed_dps (độ/giây)")
    args = ap.parse_args()

    hw = load_json("config/hardware.json")
    cfg = load_json("config/servo_test.json")
    if args.speed is not None:
        cfg["speed_dps"] = args.speed
    validate(cfg)
    bins = {b["id"]: b for b in cfg["bins"]}

    pt = PanTilt(hw, cfg, mock=args.mock)
    print(__doc__)
    print(f"Tốc độ {cfg['speed_dps']:g}°/s. Lần chạy đầu servo sẽ nhảy thẳng về vị trí nghỉ "
          f"(ngang {cfg['pan_rest']}°, dọc {cfg['tilt_rest']}°) - giữ tay/đồ ra khỏi máng.")
    try:
        pt.snap("tilt", cfg["tilt_rest"])
        pt.snap("pan", cfg["pan_rest"])
        time.sleep(1.2)  # chưa biết servo đang ở đâu: chờ đủ thời gian quay tối đa rồi mới ngắt xung
        pt.release()
        while True:
            try:
                cmd = input("> ").strip().split()
            except EOFError:
                break
            if not cmd:
                continue
            k, arg = cmd[0].lower(), (cmd[1] if len(cmd) > 1 else None)
            if k == "q":
                break
            elif k.isascii() and k.isdigit() and int(k) in bins:
                dump_to_bin(pt, cfg, bins[int(k)])
            elif k == "h":
                go_home(pt, cfg)
            elif k in ("p", "v") and arg is not None:
                angle = parse_angle(arg)
                if angle is not None:
                    if k == "p" and pt.pos["tilt"] != cfg["tilt_rest"]:
                        pt.move("tilt", cfg["tilt_rest"])  # không xoay ngang khi khay đang nghiêng
                    pt.move("pan" if k == "p" else "tilt", angle)
                    time.sleep(0.3)
                    # đang nghiêng thì giữ xung, tránh khay tụt; về nghỉ (h/q) mới ngắt
                    if pt.pos["tilt"] == cfg["tilt_rest"]:
                        pt.release()
            elif k == "d" and arg is not None:
                try:
                    delta = float(arg)
                except ValueError:
                    print("độ phải là số, vd: d 40")
                    continue
                old = cfg["tilt_delta"]
                cfg["tilt_delta"] = delta
                try:
                    validate(cfg)
                except SystemExit as e:
                    cfg["tilt_delta"] = old
                    print(e)
                    continue
                print(f"tilt_delta = {delta:g}°")
            elif k == "s":
                print(f"tilt_delta={cfg['tilt_delta']:g} tilt_sign={cfg['tilt_sign']} "
                      f"speed_dps={cfg['speed_dps']:g}")
                for b in cfg["bins"]:
                    print(f"  thùng {b['id']}: ngang {b['pan']}°, dọc {tilt_angle(cfg, b['tilt_dir']):g}°")
            else:
                print("lệnh không hợp lệ")
    except KeyboardInterrupt:
        print("\nngắt, về vị trí nghỉ...")
    finally:
        try:
            if len(pt.pos) == 2:  # snap lúc khởi động đã thành công
                go_home(pt, cfg)
        finally:
            pt.close()


if __name__ == "__main__":
    main()
