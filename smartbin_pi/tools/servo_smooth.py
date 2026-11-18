"""Test servo quay mượt bằng gpiozero, theo cách của bioerrorlog
(https://en.bioerrorlog.work/entry/raspberry-pi-multi-servo): góc tính bằng hàm sin/cos,
cập nhật mỗi 10 ms, mỗi nhịp tăng pha t thêm vài độ.

    python tools/servo_smooth.py              # 1 servo ở GPIO12
    python tools/servo_smooth.py 12 13        # servo 1 ở GPIO12, servo 2 ở GPIO13

Lệnh:
    90          servo 1 quay mượt tới 90°
    2 45        servo 2 quay mượt tới 45°
    s 4         đổi bước pha (1..30). Nhỏ = chậm, lớn = nhanh. Mặc định 2 (như bài viết)
    demo        cho các servo lắc qua lại liên tục theo hình sin (lệch pha 30°), Ctrl+C để dừng
    q           thoát

Mỗi lần quay, pha t chạy 0 → 180° với bước s, mỗi bước nghỉ 10 ms, góc = đầu + (đích − đầu)·(1 − cos t)/2:
chậm lúc bắt đầu, nhanh ở giữa, chậm lúc dừng. Thời gian quay ≈ 180 / s × 10 ms (s = 2 → 0,9 s),
không phụ thuộc quãng quay. Quay xong thì ngắt xung (detach) để servo không rung.

Lưu ý: lần quay đầu tiên sau khi bật, code chưa biết servo đang ở đâu nên đặt thẳng về 90° (có thể giật một cái).
Nếu đã bật dtoverlay=pwm-2chan trong /boot/firmware/config.txt thì GPIO12/13 bị PWM phần cứng giữ,
gpiozero sẽ báo lỗi "xGpioHandleRequest: Invalid argument" (xem tools/servo_basic.py)."""
import math
import sys
import time

from gpiozero import AngularServo

DT = 0.01            # 10 ms mỗi nhịp, như bài viết
REST = 90.0

pins = [int(a) for a in sys.argv[1:]] or [12]
servos = [AngularServo(p, min_angle=0, max_angle=180,
                       min_pulse_width=0.0005, max_pulse_width=0.0025) for p in pins]
pos = [REST] * len(servos)      # góc hiện tại do code tự nhớ (sau detach gpiozero không còn biết)
step = 2                        # bước pha t, độ mỗi nhịp


def move(i, target, step):
    """Quay servo i từ pos[i] tới target theo đường cong (1 − cos t)/2, t: 0 → 180°."""
    start, s = pos[i], servos[i]
    if abs(target - start) < 0.5:
        s.angle = target
    else:
        for t in range(step, 180 + step, step):
            k = (1 - math.cos(math.radians(min(t, 180)))) / 2
            s.angle = start + (target - start) * k
            time.sleep(DT)
    pos[i] = target
    time.sleep(0.1)             # chờ servo ổn định rồi mới ngắt xung
    s.detach()


def demo():
    """Giống bài viết: góc = 90 + sin(t + i·30°)·60, t tăng theo bước, mỗi nhịp 10 ms."""
    print("demo: Ctrl+C để dừng")
    t = 0
    try:
        while True:
            for i, s in enumerate(servos):
                s.angle = REST + math.sin(math.radians((t % 360) + i * 30)) * 60
            t += step
            time.sleep(DT)
    except KeyboardInterrupt:
        print()
    for i in range(len(servos)):            # đưa về giữa cho nhẹ nhàng
        pos[i] = servos[i].angle if servos[i].angle is not None else REST
        move(i, REST, step)


for s in servos:                            # khởi đầu: đặt thẳng về 90°
    s.angle = REST
time.sleep(0.6)
for s in servos:
    s.detach()

names = ", ".join(f"servo {i + 1} = GPIO{p}" for i, p in enumerate(pins))
print(f"{names}. Bước pha s = {step} (≈ {180 / step * DT:.1f} s mỗi lần quay). Gõ lệnh, q để thoát.")

try:
    while True:
        parts = input("lệnh> ").strip().split()
        if not parts:
            continue
        if parts[0] == "q":
            break
        if parts[0] == "demo":
            demo()
            continue
        if parts[0] == "s":
            try:
                v = int(parts[1])
                if not 1 <= v <= 30:
                    raise ValueError
            except (IndexError, ValueError):
                print("dùng: s <1..30>, vd: s 4")
                continue
            step = v
            print(f"bước pha = {step}, mỗi lần quay ≈ {180 / step * DT:.2f} s")
            continue
        try:
            if len(parts) == 1:
                idx, angle = 0, float(parts[0])
            else:
                idx, angle = int(parts[0]) - 1, float(parts[1])
        except ValueError:
            print("nhập góc (vd: 90) hoặc số servo + góc (vd: 2 45)")
            continue
        if not 0 <= idx < len(servos):
            print(f"chỉ có servo 1..{len(servos)}")
            continue
        if not 0 <= angle <= 180:
            print("góc phải trong 0..180")
            continue
        t0 = time.monotonic()
        move(idx, angle, step)
        print(f"servo {idx + 1}: {angle:g}°  ({time.monotonic() - t0:.2f} s)")
except (KeyboardInterrupt, EOFError):
    pass
finally:
    for s in servos:
        s.detach()
        s.close()
