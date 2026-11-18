"""Cảm biến HC-SR04, đèn buồng chụp, LED, còi, công tắc cửa trên Raspberry Pi (gpiozero).
Servo không ở đây: dùng PWM phần cứng qua smartbin/servo.py."""
import logging

from gpiozero import LED, Button, Buzzer, DistanceSensor

log = logging.getLogger(__name__)


class UltrasonicSensor:
    NO_DATA_CM = 999.0      # chưa đo được lần nào (không có tiếng vang): coi như khay trống

    def __init__(self, trig, echo):
        # queue_len=5: gpiozero lấy trung vị 5 lần đo -> lọc nhiễu.
        # partial=True: KHÔNG chặn chờ đủ 5 lần đo. Mặc định gpiozero đợi hàng đợi đầy mới trả số; lần đo
        # không có tiếng vang bị bỏ, nên nếu HC-SR04 không phản hồi (lỏng dây, sai chân) thì .distance treo
        # mãi -> cả vòng chính đứng, phím Enter cũng không chạy.
        self.s = DistanceSensor(echo=echo, trigger=trig, max_distance=2.0, queue_len=5, partial=True)
        self._warned = False

    def distance_cm(self):
        q = getattr(self.s, "_queue", None)
        if q is not None and not q.queue:      # partial=True mà hàng đợi rỗng thì gpiozero trả 0 (= có vật!)
            if not self._warned:
                log.warning("Siêu âm chưa đo được lần nào (không có tiếng vang): kiểm tra dây TRIG/ECHO, "
                            "nguồn 5V, cầu chia áp ở ECHO. Tạm coi như khay trống.")
                self._warned = True
            return self.NO_DATA_CM
        return self.s.distance * 100

    def close(self):
        self.s.close()


class Indicators:
    """Đèn LED trần buồng chụp (qua transistor), LED xanh/đỏ, còi."""

    def __init__(self, pins):
        self.light_led = LED(pins["light"])
        self.led_ok = LED(pins["led_ok"])
        self.led_err = LED(pins["led_err"])
        self.buzzer = Buzzer(pins["buzzer"])

    def light(self, on):
        self.light_led.value = on

    def ok_blink(self):
        self.led_ok.blink(on_time=0.3, off_time=0.2, n=2)

    def error(self, on):
        """Lỗi (rác kẹt trên khay, lỗi phần cứng): LED đỏ nháy nhanh + còi bíp."""
        if on:
            self.led_err.blink(on_time=0.1, off_time=0.1)
            self.buzzer.beep(on_time=0.2, off_time=0.8)
        else:
            self.led_err.off()
            self.buzzer.off()

    def door_open(self, on):
        """Cửa hông đang mở: LED đỏ sáng liên tục, không còi."""
        self.led_err.value = on

    def close(self):
        for d in (self.light_led, self.led_ok, self.led_err, self.buzzer):
            d.close()


class DoorSwitch:
    """Công tắc lưỡi gà (reed) ở các cửa hông. Cửa đóng = nam châm gần = reed đóng, kéo chân xuống GND.
    Nhiều cửa thì nối các reed NỐI TIẾP: chỉ cần 1 cửa mở là mạch hở -> báo mở."""

    def __init__(self, pin):
        self.b = Button(pin, pull_up=True, bounce_time=0.05)

    def is_open(self):
        return not self.b.is_pressed

    def close(self):
        self.b.close()
