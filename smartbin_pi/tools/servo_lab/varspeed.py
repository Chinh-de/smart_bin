"""Test servo theo cơ chế thư viện VarSpeedServo (Arduino): https://github.com/netlabtoolkit/VarSpeedServo

Cơ chế (theo VarSpeedServo.cpp):
- write(góc, speed): speed 1..255, 0 = nhảy thẳng tới đích (tốc độ tối đa).
- Mỗi chu kỳ 20 ms, độ rộng xung tiến về đích thêm `speed` tick, 1 tick = 0,5 µs.
  → mỗi 20 ms nhích speed × 0,5 µs. Chạy tuyến tính, không tăng tốc / giảm tốc.
- Quy đổi với xung 0,5–2,5 ms cho 0–180°: 1 µs ≈ 0,09°, nên
      speed 1   ≈ 2,25°/s     speed 50  ≈ 112°/s
      speed 20  ≈ 45°/s       speed 127 ≈ 286°/s (trên mức này SG90 gần như không nhanh hơn)

    python tools/servo_lab/run.py varspeed              # 1 servo ở GPIO12
    python tools/servo_lab/run.py varspeed 12 13        # servo 1 = GPIO12, servo 2 = GPIO13

Lệnh:
    90          servo 1 quay tới 90°
    2 45        servo 2 quay tới 45°
    v 30        đặt speed 0..255 (0 = tối đa, 1 = chậm nhất)
    q           thoát
"""
import sys
import time

from gpiozero import AngularServo

REFRESH = 0.02                  # 20 ms
TICK_US = 0.5                   # 1 tick timer Arduino 16 MHz / 8 = 0,5 µs
MIN_US, MAX_US = 500, 2500
REST = 90.0

pins = [int(a) for a in sys.argv[1:]] or [12]
servos = [AngularServo(p, min_angle=0, max_angle=180,
                       min_pulse_width=MIN_US / 1e6, max_pulse_width=MAX_US / 1e6) for p in pins]
pos_us = [MIN_US + REST / 180 * (MAX_US - MIN_US)] * len(servos)    # vị trí theo µs, như ticks trong thư viện
speed = 40


def us_to_angle(us):
    return (us - MIN_US) / (MAX_US - MIN_US) * 180


def write(i, angle, speed):
    """Giống VarSpeedServo::write(value, speed, wait=true)."""
    target = MIN_US + angle / 180 * (MAX_US - MIN_US)
    if speed == 0:
        servos[i].angle = angle
        pos_us[i] = target
        time.sleep(0.5)
    else:
        step = speed * TICK_US
        cur = pos_us[i]
        next_t = time.monotonic()
        while cur != target:
            if target > cur:
                cur = min(target, cur + step)
            else:
                cur = max(target, cur - step)
            servos[i].angle = us_to_angle(cur)
            next_t += REFRESH                       # giữ nhịp 20 ms đều như ngắt timer
            time.sleep(max(0.0, next_t - time.monotonic()))
        pos_us[i] = target
    time.sleep(0.1)
    servos[i].detach()


for s in servos:
    s.angle = REST
time.sleep(0.6)
for s in servos:
    s.detach()

print(", ".join(f"servo {i + 1} = GPIO{p}" for i, p in enumerate(pins)))
print(f"speed = {speed} (≈ {speed * TICK_US / REFRESH * 0.09:.0f}°/s). Gõ lệnh, q để thoát.")
try:
    while True:
        parts = input("lệnh> ").strip().lower().split()
        if not parts:
            continue
        if parts[0] == "q":
            break
        if parts[0] == "v" and len(parts) == 2:
            try:
                v = int(parts[1])
                if not 0 <= v <= 255:
                    raise ValueError
                speed = v
                print("speed = 0 (tối đa)" if v == 0 else f"speed = {v} (≈ {v * TICK_US / REFRESH * 0.09:.0f}°/s)")
            except ValueError:
                print("dùng: v <0..255>")
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
        write(idx, angle, speed)
        print(f"servo {idx + 1}: {angle:g}°  ({time.monotonic() - t0:.2f} s)")
except (KeyboardInterrupt, EOFError):
    pass
finally:
    for s in servos:
        s.detach()
        s.close()
