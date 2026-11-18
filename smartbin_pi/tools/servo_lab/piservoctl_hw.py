"""Test servo bằng thư viện piServoCtl (PWM phần cứng qua pigpio): https://github.com/naoto64/piServoCtl

Cơ chế:
- piServoCtl dùng PWM phần cứng của Pi (qua daemon pigpio), xung đều, không rung như PWM phần mềm.
  Thư viện chỉ có write(góc) / read() / stop(), KHÔNG có điều chỉnh tốc độ.
- Vì vậy file này tự thêm quay mượt kiểu ServoEasing: mỗi 20 ms tính lại góc theo thời gian,
  easing quad in-out, tốc độ theo độ/giây. Xung phần cứng + chia bước = mượt nhất trong các cách thử.

Cài đặt (một lần):
    sudo apt install pigpio python3-pigpio
    sudo systemctl enable --now pigpiod        # hoặc: sudo pigpiod
    pip install piServoCtl

Chạy:
    python tools/servo_lab/run.py piservo              # 1 servo ở GPIO12
    python tools/servo_lab/run.py piservo 12 13        # servo 1 = GPIO12, servo 2 = GPIO13 (2 chân PWM phần cứng)

Lệnh:
    90          servo 1 quay tới 90°
    2 45        servo 2 quay tới 45°
    v 90        tốc độ °/s (0 = nhảy thẳng tới đích bằng write())
    q           thoát

Lưu ý:
- Chỉ dùng chân có PWM phần cứng: GPIO12, 13, 18, 19. GPIO12 và 18 chung kênh PWM0, GPIO13 và 19 chung PWM1,
  nên 2 servo phải ở 2 kênh khác nhau (vd 12 + 13).
- Nếu đã bật dtoverlay=pwm-2chan trong /boot/firmware/config.txt thì nên tắt (thêm #) rồi reboot,
  để kernel và pigpio không cùng giành khối PWM.
- pigpio không chạy trên Raspberry Pi 5.
"""
import sys
import time

try:
    from piservo import Servo
except ImportError:
    sys.exit("Chưa cài piServoCtl: pip install piServoCtl  (và chạy: sudo pigpiod)")

REFRESH = 0.02
REST = 90.0

pins = [int(a) for a in sys.argv[1:]] or [12]
try:
    servos = [Servo(p, min_value=0, max_value=180, min_pulse=0.5, max_pulse=2.5) for p in pins]
except Exception as e:                      # thường do pigpiod chưa chạy
    sys.exit(f"Không mở được servo ({e}). Đã chạy 'sudo pigpiod' chưa?")
pos = [REST] * len(servos)
speed = 90.0


def ease(x):                                 # quad in-out
    return 2 * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 2 / 2


def ease_to(i, target):
    start = pos[i]
    servos[i].start()
    if speed <= 0:
        servos[i].write(target)
        time.sleep(0.5)
    else:
        dur = abs(target - start) / speed
        t0 = time.monotonic()
        while True:
            x = 1.0 if dur == 0 else min(1.0, (time.monotonic() - t0) / dur)
            servos[i].write(start + (target - start) * ease(x))
            if x >= 1.0:
                break
            time.sleep(REFRESH)
    pos[i] = target
    time.sleep(0.1)
    servos[i].stop()                         # ngắt xung khi đứng yên


for s in servos:
    s.start()
    s.write(REST)
time.sleep(0.6)
for s in servos:
    s.stop()

print(", ".join(f"servo {i + 1} = GPIO{p}" for i, p in enumerate(pins)))
print(f"tốc độ {speed:g}°/s (PWM phần cứng). Gõ lệnh, q để thoát.")
try:
    while True:
        parts = input("lệnh> ").strip().lower().split()
        if not parts:
            continue
        if parts[0] == "q":
            break
        if parts[0] == "v" and len(parts) == 2:
            try:
                speed = max(0.0, float(parts[1]))
                print("tốc độ = tối đa (write thẳng)" if speed == 0 else f"tốc độ = {speed:g}°/s")
            except ValueError:
                print("dùng: v 90")
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
        s.stop()
