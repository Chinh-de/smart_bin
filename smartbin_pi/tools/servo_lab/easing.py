"""Test servo theo cơ chế thư viện ServoEasing (ArminJo, Arduino): https://github.com/ArminJo/ServoEasing

Cơ chế:
- Tốc độ tính bằng độ/giây. Thời gian quay = quãng quay / tốc độ.
- Cứ 20 ms (đúng nhịp servo 50 Hz) tính lại vị trí THEO THỜI GIAN đã trôi qua, không cộng bước cố định,
  nên vòng lặp có trễ thì vị trí vẫn đuổi kịp.
- Vị trí = đầu + (đích − đầu) · easing(x), x = thời gian đã qua / tổng thời gian (0 → 1).
- Nhiều kiểu easing, mỗi kiểu có in / out / inout. Kiểu "precision" luôn tới đích từ phía dưới
  (vượt quá 5° rồi quay lại khi đi xuống) để khử độ rơ bánh răng.

    python tools/servo_lab/run.py easing              # 1 servo ở GPIO12
    python tools/servo_lab/run.py easing 12 13        # servo 1 = GPIO12, servo 2 = GPIO13

Lệnh:
    90            servo 1 quay tới 90°
    2 45          servo 2 quay tới 45°
    v 90          đặt tốc độ 90°/s (SG90 tối đa ~450°/s; dưới ~50°/s dễ khựng vì vùng chết)
    e quad        kiểu easing: linear quad cubic quartic sine circ back bounce precision
    m inout       chế độ: in / out / inout
    q             thoát
"""
import math
import sys
import time

from gpiozero import AngularServo

REFRESH = 0.02          # 20 ms, như SERVO_REFRESH_INTERVAL_MICROS của ServoEasing
REST = 90.0


# ---- các hàm easing "in" chuẩn (x: 0 → 1), giống Robert Penner / ServoEasing ----
def _bounce_out(x):
    n1, d1 = 7.5625, 2.75
    if x < 1 / d1:
        return n1 * x * x
    if x < 2 / d1:
        x -= 1.5 / d1
        return n1 * x * x + 0.75
    if x < 2.5 / d1:
        x -= 2.25 / d1
        return n1 * x * x + 0.9375
    x -= 2.625 / d1
    return n1 * x * x + 0.984375


EASE_IN = {
    "linear":  lambda x: x,
    "quad":    lambda x: x * x,
    "cubic":   lambda x: x ** 3,
    "quartic": lambda x: x ** 4,
    "sine":    lambda x: 1 - math.cos(x * math.pi / 2),
    "circ":    lambda x: 1 - math.sqrt(max(0.0, 1 - x * x)),
    "back":    lambda x: 2.70158 * x ** 3 - 1.70158 * x * x,
    "bounce":  lambda x: 1 - _bounce_out(1 - x),
}


def apply_mode(f_in, mode, x):
    if mode == "in":
        return f_in(x)
    if mode == "out":
        return 1 - f_in(1 - x)
    return f_in(2 * x) / 2 if x < 0.5 else 1 - f_in(2 - 2 * x) / 2      # inout


pins = [int(a) for a in sys.argv[1:]] or [12]
servos = [AngularServo(p, min_angle=0, max_angle=180,
                       min_pulse_width=0.0005, max_pulse_width=0.0025) for p in pins]
pos = [REST] * len(servos)
speed = 90.0
easing = "quad"
mode = "inout"


def ease_to(i, target):
    """Quay servo i tới target với tốc độ speed (độ/giây), tính vị trí theo thời gian."""
    start = pos[i]
    path = [(start, target)]
    if easing == "precision" and target < start:          # đi xuống: vượt quá 5° rồi quay lên
        under = max(0.0, target - 5)
        path = [(start, under), (under, target)]
    for a, b in path:
        f = EASE_IN["linear"] if easing == "precision" else EASE_IN[easing]
        dur = abs(b - a) / speed
        t0 = time.monotonic()
        while True:
            x = 1.0 if dur == 0 else min(1.0, (time.monotonic() - t0) / dur)
            angle = a + (b - a) * apply_mode(f, mode, x)
            servos[i].angle = min(180.0, max(0.0, angle))      # back/bounce có thể vượt biên
            if x >= 1.0:
                break
            time.sleep(REFRESH)
    pos[i] = target
    time.sleep(0.1)
    servos[i].detach()


for s in servos:                    # khởi đầu: chưa biết vị trí thật nên đặt thẳng về 90°
    s.angle = REST
time.sleep(0.6)
for s in servos:
    s.detach()

print(", ".join(f"servo {i + 1} = GPIO{p}" for i, p in enumerate(pins)))
print(f"tốc độ {speed:g}°/s, easing {easing} {mode}. Gõ lệnh, q để thoát.")
try:
    while True:
        parts = input("lệnh> ").strip().lower().split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == "q":
            break
        if cmd == "v" and len(parts) == 2:
            try:
                speed = max(1.0, float(parts[1]))
                print(f"tốc độ = {speed:g}°/s")
            except ValueError:
                print("dùng: v 90")
            continue
        if cmd == "e" and len(parts) == 2:
            if parts[1] in EASE_IN or parts[1] == "precision":
                easing = parts[1]
                print(f"easing = {easing} {mode}")
            else:
                print("chọn: " + " ".join(list(EASE_IN) + ["precision"]))
            continue
        if cmd == "m" and len(parts) == 2:
            if parts[1] in ("in", "out", "inout"):
                mode = parts[1]
                print(f"easing = {easing} {mode}")
            else:
                print("chọn: in / out / inout")
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
        ease_to(idx, angle)
        print(f"servo {idx + 1}: {angle:g}°  ({time.monotonic() - t0:.2f} s)")
except (KeyboardInterrupt, EOFError):
    pass
finally:
    for s in servos:
        s.detach()
        s.close()
