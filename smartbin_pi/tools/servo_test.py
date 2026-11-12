"""Kiểm tra riêng 2 servo: ngang (pan, chọn bên) + dọc (tilt, nghiêng đổ rác vào thùng).

    python tools/servo_test.py            # trên Pi
    python tools/servo_test.py --mock     # chạy thử logic trên máy không có GPIO
    python tools/servo_test.py --speed 25 # quay chậm hơn (độ/giây)

Lệnh:
    1..4      đưa rác vào thùng đó rồi về vị trí nghỉ
    h         về vị trí nghỉ
    p <góc>   xoay servo ngang tới góc (0..180)      vd: p 45
    v <góc>   xoay servo dọc tới góc (0..180)        vd: v 128
    d <độ>    đổi biên độ nghiêng dọc (tilt_delta)   vd: d 40
    s         in cấu hình hiện tại
    q         về vị trí nghỉ rồi thoát

Chân GPIO lấy từ config/hardware.json (servo_chute = ngang, servo_tray = dọc).
Góc/tốc độ/thời gian lấy từ config/servo_test.json."""
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


class PanTilt:
    """Hai servo, mỗi lần chỉ quay từng bước nhỏ để không giật/quá nhanh."""

    def __init__(self, hw, cfg):
        from gpiozero import AngularServo  # import muộn để --mock đặt pin factory trước
        p = hw["pins"]
        pulse = dict(min_angle=MIN_ANGLE, max_angle=MAX_ANGLE,
                     min_pulse_width=hw["servo_min_pulse_ms"] / 1000,
                     max_pulse_width=hw["servo_max_pulse_ms"] / 1000)
        self.servos = {"pan": AngularServo(p["servo_chute"], **pulse),
                       "tilt": AngularServo(p["servo_tray"], **pulse)}
        self.cfg = cfg
        self.pos = {}  # góc hiện tại (theo lệnh gần nhất); gpiozero trả None sau detach

    def snap(self, name, angle):
        """Đặt thẳng góc, không làm mượt. Chỉ dùng lúc khởi động (chưa biết servo đang ở đâu)."""
        self.servos[name].angle = angle
        self.pos[name] = angle

    def move(self, name, target):
        """Quay từ từ tới góc target với tốc độ speed_dps."""
        target = max(MIN_ANGLE, min(MAX_ANGLE, target))
        cur = self.pos[name]
        step = self.cfg["step_deg"]
        delay = step / self.cfg["speed_dps"]
        while abs(target - cur) > 1e-6:
            cur += max(-step, min(step, target - cur))
            self.servos[name].angle = cur
            self.pos[name] = cur
            time.sleep(delay)

    def release(self):
        # ngắt xung khi đứng yên -> servo không rung/kêu, đỡ nóng
        for s in self.servos.values():
            s.detach()

    def close(self):
        for s in self.servos.values():
            s.close()


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
                                 "speed_dps", "step_deg", "settle_s", "hold_s")]
        bins = [(b["id"], b["pan"], b["tilt_dir"]) for b in cfg["bins"]]
    except KeyError as e:
        raise SystemExit(f"servo_test.json thiếu khoá {e}")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in nums):
        raise SystemExit("servo_test.json: các giá trị cấu hình phải là số hữu hạn")
    if cfg["speed_dps"] <= 0 or cfg["step_deg"] <= 0:
        raise SystemExit("speed_dps và step_deg phải > 0")
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
    ap.add_argument("--mock", action="store_true", help="không dùng GPIO thật")
    ap.add_argument("--speed", type=float, help="ghi đè speed_dps (độ/giây)")
    args = ap.parse_args()

    hw = load_json("config/hardware.json")
    cfg = load_json("config/servo_test.json")
    if args.speed is not None:
        cfg["speed_dps"] = args.speed
    validate(cfg)
    bins = {b["id"]: b for b in cfg["bins"]}

    if args.mock:
        from gpiozero import Device
        from gpiozero.pins.mock import MockFactory, MockPWMPin
        Device.pin_factory = MockFactory(pin_class=MockPWMPin)

    pt = PanTilt(hw, cfg)
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
