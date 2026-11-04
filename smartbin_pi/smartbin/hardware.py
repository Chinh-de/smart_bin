"""Cảm biến HC-SR04, servo, đèn, còi trên Raspberry Pi (gpiozero)."""
from gpiozero import LED, AngularServo, Buzzer, DistanceSensor


class UltrasonicSensor:
    def __init__(self, trig, echo):
        # queue_len=5: gpiozero lấy trung vị 5 lần đo -> lọc nhiễu
        self.s = DistanceSensor(echo=echo, trigger=trig, max_distance=2.0, queue_len=5)

    def distance_cm(self):
        return self.s.distance * 100

    def close(self):
        self.s.close()


class Actuators:
    def __init__(self, hw):
        p = hw["pins"]
        pulse = dict(min_angle=0, max_angle=180,
                     min_pulse_width=hw["servo_min_pulse_ms"] / 1000,
                     max_pulse_width=hw["servo_max_pulse_ms"] / 1000)
        self.chute_servo = AngularServo(p["servo_chute"], **pulse)
        self.tray_servo = AngularServo(p["servo_tray"], **pulse)
        self.light_led = LED(p["light"])
        self.led_ok = LED(p["led_ok"])
        self.led_err = LED(p["led_err"])
        self.buzzer = Buzzer(p["buzzer"])

    def set_chute(self, angle):
        self.chute_servo.angle = angle

    def set_tray(self, angle):
        self.tray_servo.angle = angle

    def release_servos(self):
        # ngắt xung khi đứng yên -> servo không rung/kêu, đỡ nóng
        self.chute_servo.detach()
        self.tray_servo.detach()

    def light(self, on):
        self.light_led.value = on

    def ok_blink(self):
        self.led_ok.blink(on_time=0.3, off_time=0.2, n=2)

    def reject_alarm(self, on):
        if on:
            self.led_err.blink(on_time=0.3, off_time=0.3)
            self.buzzer.beep(on_time=0.2, off_time=0.8)
        else:
            self.led_err.off()
            self.buzzer.off()

    def error(self, on):
        if on:
            self.led_err.blink(on_time=0.1, off_time=0.1)
        else:
            self.led_err.off()

    def close(self):
        for d in (self.chute_servo, self.tray_servo, self.light_led, self.led_ok, self.led_err, self.buzzer):
            d.close()
