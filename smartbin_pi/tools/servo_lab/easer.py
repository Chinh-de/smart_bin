"""Test servo theo cơ chế thư viện ServoEaser (todbot, Arduino): https://github.com/todbot/ServoEaser

Cơ chế (theo ServoEaser.cpp):
- easeTo(góc, thời_gian_ms): quay tới góc trong đúng khoảng thời gian cho trước (không theo tốc độ).
- update() được gọi mỗi frameTime (20 ms): currPos = easingFunc(t, startPos, changePos, duration),
  hàm easing kiểu Robert Penner (t: thời gian đã qua, b: điểm đầu, c: quãng đổi, d: tổng thời gian).
  Mặc định easeInOutCubic.
- "Move list": danh sách (góc, thời gian) chạy nối tiếp, có thể lặp. Không chặn: nhiều servo cùng chạy
  vì chỉ cần gọi update() của từng servo trong cùng một vòng lặp.

    python tools/servo_lab/run.py easer              # 1 servo ở GPIO12
    python tools/servo_lab/run.py easer 12 13        # servo 1 = GPIO12 (xoay), servo 2 = GPIO13 (lật)

Lệnh:
    90 600          servo 1 quay tới 90° trong 600 ms
    2 45 500        servo 2 quay tới 45° trong 500 ms
    m 45:600 90:500 135:600 90:500
                    chạy move list trên servo 1 (góc:ms ...)
    bins            (cần 2 servo) chạy thử 4 thùng: xoay → lật → về, hai servo nối nhau như máy thật
    e cubic         đổi easing: linear quad cubic sine
    q               thoát
"""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from smartbin.servo import SysfsPwm  # noqa: E402

# GPIO12/13 đã bị overlay pwm-2chan giữ (PWM phần cứng) nên gpiozero không claim được
# ("xGpioHandleRequest: Invalid argument"). Dùng /sys/class/pwm giống smartbin/servo.py.
MIN_US, MAX_US = 500, 2500

FRAME = 0.02            # frameTime 20 ms
REST = 90.0


# ---- easing kiểu Penner: f(t, b, c, d) ----
def ease_linear(t, b, c, d):
    return c * t / d + b


def ease_in_out_quad(t, b, c, d):
    t /= d / 2
    if t < 1:
        return c / 2 * t * t + b
    t -= 1
    return -c / 2 * (t * (t - 2) - 1) + b


def ease_in_out_cubic(t, b, c, d):          # mặc định của ServoEaser
    t /= d / 2
    if t < 1:
        return c / 2 * t * t * t + b
    t -= 2
    return c / 2 * (t * t * t + 2) + b


def ease_in_out_sine(t, b, c, d):
    return -c / 2 * (math.cos(math.pi * t / d) - 1) + b


EASINGS = {"linear": ease_linear, "quad": ease_in_out_quad, "cubic": ease_in_out_cubic, "sine": ease_in_out_sine}


class ServoEaser:
    """Bản Python của ServoEaser: không chặn, gọi update() đều đặn."""

    def __init__(self, servo, start=REST, easing=ease_in_out_cubic):
        self.servo = servo
        self.easing = easing
        self.curr = start
        self.moves, self.idx = [], 0
        self.start_pos = start
        self.change = 0.0
        self.dur = 0.0
        self.t0 = 0.0
        self.running = False

    def ease_to(self, pos, dur_ms):
        self.play([(pos, dur_ms)])

    def play(self, moves):
        self.moves, self.idx = list(moves), 0
        self._next()

    def _next(self):
        pos, dur_ms = self.moves[self.idx]
        self.start_pos = self.curr
        self.change = pos - self.curr
        self.dur = max(dur_ms / 1000, 1e-3)
        self.t0 = time.monotonic()
        self.running = True

    def update(self):
        if not self.running:
            return
        t = time.monotonic() - self.t0
        if t >= self.dur:
            self.curr = self.start_pos + self.change
            self.servo.angle = self.curr
            self.idx += 1
            if self.idx < len(self.moves):
                self._next()
            else:
                self.running = False        # "servo arrived"
            return
        self.curr = self.easing(t, self.start_pos, self.change, self.dur)
        self.servo.angle = min(180.0, max(0.0, self.curr))


def run(easers):
    """Vòng lặp update() mỗi 20 ms tới khi mọi servo đến nơi, rồi ngắt xung."""
    next_t = time.monotonic()
    while any(e.running for e in easers):
        for e in easers:
            e.update()
        next_t += FRAME
        time.sleep(max(0.0, next_t - time.monotonic()))
    time.sleep(0.1)
    for e in easers:
        e.servo.detach()


class HwServo:
    """Giống AngularServo (0..180°, 500..2500 µs) nhưng chạy trên kênh PWM phần cứng."""

    def __init__(self, pwm, name):
        self.pwm, self.name = pwm, name

    @property
    def angle(self):
        return None

    @angle.setter
    def angle(self, a):
        a = min(180.0, max(0.0, a))
        self.pwm.set_pulse_us(self.name, MIN_US + (MAX_US - MIN_US) * a / 180)

    def detach(self):
        self.pwm.off(self.name)

    def close(self):
        self.pwm.off(self.name)


pins = [int(a) for a in sys.argv[1:]] or [12]
pwm = SysfsPwm({f"s{i}": p for i, p in enumerate(pins)})
servos = [HwServo(pwm, f"s{i}") for i in range(len(pins))]
for s in servos:
    s.angle = REST
time.sleep(0.6)
for s in servos:
    s.detach()
easers = [ServoEaser(s) for s in servos]

# thứ tự giống máy thật: thùng (xoay, lật)
BINS = [(1, 45, 45), (2, 135, 45), (3, 45, 135), (4, 135, 135)]

print(", ".join(f"servo {i + 1} = GPIO{p}" for i, p in enumerate(pins)))
print("easing = cubic (mặc định ServoEaser). Gõ lệnh, q để thoát.")
try:
    while True:
        parts = input("lệnh> ").strip().lower().split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == "q":
            break
        if cmd == "e" and len(parts) == 2:
            if parts[1] in EASINGS:
                for e in easers:
                    e.easing = EASINGS[parts[1]]
                print(f"easing = {parts[1]}")
            else:
                print("chọn: " + " ".join(EASINGS))
            continue
        if cmd == "m" and len(parts) > 1:
            try:
                moves = [(float(a), float(b)) for a, b in (p.split(":") for p in parts[1:])]
                if not all(0 <= a <= 180 and b > 0 for a, b in moves):
                    raise ValueError
            except ValueError:
                print("dùng: m 45:600 90:500  (góc:ms)")
                continue
            easers[0].play(moves)
            run(easers)
            print(f"servo 1 xong move list, đang ở {easers[0].curr:g}°")
            continue
        if cmd == "bins":
            if len(easers) < 2:
                print("cần 2 servo: python tools/servo_lab/run.py easer 12 13")
                continue
            for b, rot, tilt in BINS:
                print(f"thùng {b}: xoay {rot}°, lật {tilt}°")
                easers[0].ease_to(rot, 500); run(easers)
                easers[1].ease_to(tilt, 400); run(easers)
                time.sleep(0.3)
                easers[1].ease_to(REST, 400); run(easers)
                easers[0].ease_to(REST, 500); run(easers)
            continue
        try:
            if len(parts) == 2:
                idx, angle, ms = 0, float(parts[0]), float(parts[1])
            else:
                idx, angle, ms = int(parts[0]) - 1, float(parts[1]), float(parts[2])
        except (ValueError, IndexError):
            print("nhập: góc ms (vd: 90 600) hoặc servo góc ms (vd: 2 45 500)")
            continue
        if not 0 <= idx < len(servos) or not 0 <= angle <= 180 or ms <= 0:
            print(f"servo 1..{len(servos)}, góc 0..180, ms > 0")
            continue
        t0 = time.monotonic()
        easers[idx].ease_to(angle, ms)
        run(easers)
        print(f"servo {idx + 1}: {angle:g}°  ({time.monotonic() - t0:.2f} s)")
except (KeyboardInterrupt, EOFError):
    pass
finally:
    for s in servos:
        s.detach()
        s.close()
    pwm.close()
