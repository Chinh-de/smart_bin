"""Test servo cơ bản bằng gpiozero: nhập góc, servo quay tới góc đó.

    python tools/servo_basic.py            # servo ở GPIO12
    python tools/servo_basic.py 13         # servo ở GPIO13

Gõ góc 0..180 rồi Enter. Gõ q để thoát.

Lưu ý: nếu đã bật dtoverlay=pwm-2chan trong /boot/firmware/config.txt thì GPIO12/13 bị PWM phần cứng
giữ, gpiozero sẽ báo lỗi "xGpioHandleRequest: Invalid argument". Khi đó tắt dòng overlay (thêm # ở đầu)
rồi reboot, hoặc cắm dây tín hiệu servo sang chân khác (vd GPIO17) và chạy: python tools/servo_basic.py 17"""
import sys

from gpiozero import AngularServo

pin = int(sys.argv[1]) if len(sys.argv) > 1 else 12
servo = AngularServo(pin, min_angle=0, max_angle=180,
                     min_pulse_width=0.0005, max_pulse_width=0.0025)
print(f"Servo ở GPIO{pin}. Nhập góc 0..180, q để thoát.")

try:
    while True:
        s = input("góc> ").strip()
        if s == "q":
            break
        try:
            angle = float(s)
        except ValueError:
            print("nhập số, vd: 90")
            continue
        if not 0 <= angle <= 180:
            print("góc phải trong 0..180")
            continue
        servo.angle = angle
        print(f"-> {angle:g}°")
except (KeyboardInterrupt, EOFError):
    pass
finally:
    servo.detach()
    servo.close()
