"""Test servo theo cơ chế VarSpeedPython (bản Python của VarSpeedServo): https://github.com/pvanallen/VarSpeedPython

Cơ chế (theo varspeed/varspeed.py):
- Vspeed.move(vị_trí_mới, time_secs, steps, easing): chia quãng quay thành `steps` bước đều nhau
  về thời gian (mỗi bước cách nhau time_secs / steps), vị trí mỗi bước lấy từ hàm easing.
- Không chặn: gọi move() lặp lại trong vòng lặp chính, nó trả về (position, running, changed).
  Chỉ ghi ra servo khi changed = True.
- result="int": làm tròn vị trí thành số nguyên → mỗi lần ghi servo nhích ít nhất 1°,
  nên tự tránh được các bước quá nhỏ nằm trong vùng chết.

    python tools/servo_lab/run.py vspeed              # 1 servo ở GPIO12
    python tools/servo_lab/run.py vspeed 12 13        # servo 1 = GPIO12, servo 2 = GPIO13

Lệnh:
    90                      servo 1 tới 90° với thông số hiện tại
    2 45                    servo 2 tới 45°
    t 0.6                   thời gian mỗi lần quay (giây)
    n 30                    số bước
    e QuadEaseInOut         easing: LinearInOut QuadEaseInOut CubicEaseInOut SineEaseInOut QuadEaseOut
    r int | float           làm tròn vị trí (int như mặc định của thư viện) hoặc giữ số thực
    seq                     chạy thử một chuỗi (sequence) trên servo 1: 45 → 135 → 90
    q                       thoát
"""
import math
import sys
import time

from gpiozero import AngularServo

REST = 90.0


# ---- easing: f(step) với start, end, duration (= số bước), giống easing_functions của thư viện ----
def _mk(fn):
    def factory(start, end, duration):
        return lambda step: start + (end - start) * fn(min(1.0, step / duration))
    return factory


def _inout(f):
    return lambda x: f(2 * x) / 2 if x < 0.5 else 1 - f(2 - 2 * x) / 2


EASINGS = {
    "LinearInOut":    _mk(lambda x: x),
    "QuadEaseInOut":  _mk(_inout(lambda x: x * x)),
    "CubicEaseInOut": _mk(_inout(lambda x: x ** 3)),
    "SineEaseInOut":  _mk(lambda x: (1 - math.cos(math.pi * x)) / 2),
    "QuadEaseOut":    _mk(lambda x: 1 - (1 - x) ** 2),
}


class Vspeed:
    """Rút gọn từ VarSpeedPython: chỉ phần move() không chặn."""

    def __init__(self, init_position=REST, result="int"):
        self.position = init_position
        self.last_position = init_position
        self.new_position = init_position
        self.result = result
        self.started = False

    def move(self, new_position, time_secs=0.6, steps=30, easing="QuadEaseInOut"):
        if not self.started or new_position != self.new_position:
            self.new_position = new_position
            self.t0 = time.monotonic()
            self.step_delay = time_secs / steps
            self.steps, self.step = steps, 0
            self.ease = EASINGS[easing](self.position, new_position, steps)
            self.started = True
        running = True
        new_step = min(int((time.monotonic() - self.t0) / self.step_delay), self.steps)
        if new_step > self.step:
            self.step = new_step
            self.position = self.ease(self.step)
            if self.step >= self.steps:
                self.position = self.new_position
                running = False
                self.started = False
        position = round(self.position) if self.result == "int" else self.position
        changed = position != self.last_position
        self.position = self.last_position = position
        return position, running, changed


pins = [int(a) for a in sys.argv[1:]] or [12]
servos = [AngularServo(p, min_angle=0, max_angle=180,
                       min_pulse_width=0.0005, max_pulse_width=0.0025) for p in pins]
for s in servos:
    s.angle = REST
time.sleep(0.6)
for s in servos:
    s.detach()
vs = [Vspeed() for _ in servos]
cfg = {"time": 0.6, "steps": 30, "easing": "QuadEaseInOut"}


def go(i, angle):
    """Gọi move() liên tục (mỗi 5 ms), ghi servo khi vị trí đổi; trả về số lần ghi."""
    writes = 0
    while True:
        p, running, changed = vs[i].move(angle, cfg["time"], cfg["steps"], cfg["easing"])
        if changed:
            servos[i].angle = p
            writes += 1
        if not running:
            break
        time.sleep(0.005)
    servos[i].angle = angle
    time.sleep(0.1)
    servos[i].detach()
    return writes


print(", ".join(f"servo {i + 1} = GPIO{p}" for i, p in enumerate(pins)))
print(f"{cfg['time']} s, {cfg['steps']} bước, {cfg['easing']}, làm tròn {vs[0].result}. Gõ lệnh, q để thoát.")
try:
    while True:
        parts = input("lệnh> ").strip().split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == "q":
            break
        try:
            if cmd == "t":
                cfg["time"] = max(0.05, float(parts[1])); print(f"thời gian = {cfg['time']} s"); continue
            if cmd == "n":
                cfg["steps"] = max(1, int(parts[1])); print(f"số bước = {cfg['steps']}"); continue
        except (ValueError, IndexError):
            print("dùng: t 0.6  hoặc  n 30"); continue
        if cmd == "e":
            if len(parts) == 2 and parts[1] in EASINGS:
                cfg["easing"] = parts[1]; print(f"easing = {parts[1]}")
            else:
                print("chọn: " + " ".join(EASINGS))
            continue
        if cmd == "r":
            if len(parts) == 2 and parts[1] in ("int", "float"):
                for v in vs:
                    v.result = parts[1]
                print(f"làm tròn = {parts[1]}")
            else:
                print("dùng: r int  hoặc  r float")
            continue
        if cmd == "seq":
            for a in (45, 135, 90):
                n = go(0, a)
                print(f"servo 1: {a}° ({n} lần ghi)")
            continue
        try:
            idx, angle = (0, float(parts[0])) if len(parts) == 1 else (int(parts[0]) - 1, float(parts[1]))
        except ValueError:
            print("nhập góc (vd: 90) hoặc số servo + góc (vd: 2 45)")
            continue
        if not 0 <= idx < len(servos) or not 0 <= angle <= 180:
            print(f"servo 1..{len(servos)}, góc 0..180")
            continue
        t0 = time.monotonic()
        n = go(idx, angle)
        print(f"servo {idx + 1}: {angle:g}°  ({time.monotonic() - t0:.2f} s, {n} lần ghi)")
except (KeyboardInterrupt, EOFError):
    pass
finally:
    for s in servos:
        s.detach()
        s.close()
