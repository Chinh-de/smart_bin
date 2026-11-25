"""Cảm biến HC-SR04, đèn buồng chụp, LED, còi, công tắc cửa trên Raspberry Pi (gpiozero).
Servo không ở đây: dùng PWM phần cứng qua smartbin/servo.py."""
import logging
import statistics
import threading
import time
from collections import deque

from gpiozero import LED, Button, Buzzer, DistanceSensor

log = logging.getLogger(__name__)

# Kết quả từng lần phát xung (ping) của HC-SR04
OK = "ok"          # có tiếng vang, đo được khoảng cách
SHORT = "short"    # thấy cạnh xuống mà không thấy cạnh lên: xung ECHO quá ngắn, bắt không kịp (vật RẤT gần)
NO_ECHO = "none"   # ECHO không lên/xuống trong 100 ms: không có phản hồi (dây, nguồn, cầu chia áp)
BUSY = "busy"      # ECHO vẫn đang mức cao từ lần trước, chờ 50 ms không xuống: bỏ lần này
ERR = "err"        # lỗi bất ngờ khi đo (xem log)
STATUS_MARK = {SHORT: "ngắn", NO_ECHO: "mất", BUSY: "bận", ERR: "lỗi"}


class _PingSensor(DistanceSensor):
    """DistanceSensor của gpiozero nhưng ghi lại MỌI lần ping (cả lần hỏng) kèm thời điểm và lý do.

    Vì sao không dùng thẳng .distance của gpiozero (đã đọc mã gpiozero 2.0.1, input_devices.py):
    - _read() trả None khi đo hỏng (không có vang, xung quá ngắn...) và GPIOQueue BỎ QUA giá trị None
      (ignore={None}), không đẩy gì vào hàng đợi;
    - .distance = trung vị của queue_len lần đo HỢP LỆ GẦN NHẤT, không quan tâm chúng cũ bao lâu.
    => Khay trống đo 30 cm, đặt vật vào mà vật không dội vang (mềm, nghiêng, quá gần) thì mọi lần đo sau
       đều None, hàng đợi giữ nguyên 5 số 30 cm cũ -> .distance vẫn báo 30 cm mãi -> không bao giờ có sự kiện,
       và cũng không có dấu hiệu gì trong log. Ở đây chỉ lấy các lần đo MỚI (trong cửa sổ thời gian) và đếm
       luôn các lần hỏng để in log.

    _read() chép lại đúng logic của gpiozero 2.0.x, chỉ thêm mã trạng thái thay vì warnings.warn."""

    def __init__(self, *args, **kwargs):
        # phải gán TRƯỚC super().__init__: luồng đo của gpiozero chạy ngay trong __init__ và gọi _read()
        self.pings = deque(maxlen=200)   # (time.monotonic(), cm hoặc None, trạng thái)
        self.pings_lock = threading.Lock()
        self.read_errors = 0
        self.ever_ok = False             # đã từng đo được số hợp lệ -> dây/nguồn cảm biến đang chạy
        super().__init__(*args, **kwargs)

    def _record(self, cm, status):
        with self.pings_lock:
            self.pings.append((time.monotonic(), cm, status))
        if cm is not None:
            self.ever_ok = True

    def _read(self):
        try:
            cm, status = self._ping()
        except Exception:
            # luồng GPIOQueue chỉ bắt ReferenceError: lỗi khác sẽ giết luồng đo âm thầm -> số liệu đứng im
            self.read_errors += 1
            if self.read_errors <= 3:
                log.exception("Siêu âm: lỗi khi đo")
            cm, status = None, ERR
            time.sleep(0.1)
        self._record(cm, status)
        return None if cm is None else min(1.0, cm / 100 / self._max_distance)

    def _ping(self):
        if not hasattr(self, "_echo"):        # gpiozero khác 2.0.x: dùng _read gốc, không phân biệt lý do
            v = super()._read()
            return (None, NO_ECHO) if v is None else (v * self._max_distance * 100, OK)
        if self.pin.state:
            if not self._echo.wait(0.05):
                return None, BUSY
        self._echo.clear()
        self._echo_fall = None
        self._echo_rise = None
        with DistanceSensor.ECHO_LOCK:
            self._trigger.pin.state = True
            time.sleep(0.00001)
            self._trigger.pin.state = False
            if not self._echo.wait(0.1):
                return None, NO_ECHO
            fall, rise = self._echo_fall, self._echo_rise
            if fall is None or rise is None:
                return None, SHORT
            m = self.pin_factory.ticks_diff(fall, rise) * self.speed_of_sound / 2.0
            return m * 100, OK     # không cắt ở max_distance: số thật để in log


class UltrasonicSensor:
    NO_DATA_CM = 999.0      # không có lần đo hợp lệ nào gần đây: coi như khay trống (và in cảnh báo)
    LOST_CM = 0.0           # mất vang (lost_as_object): coi như vật nằm sát cảm biến -> < trigger_cm = CÓ VẬT
    LOST = (NO_ECHO, SHORT) # lần ping hỏng được tính là "mất vang"
    WINDOW_S = 0.5          # chỉ lấy trung vị các lần đo trong 0.5 s gần nhất (~5-8 lần, gpiozero đo ~60 ms/lần)
    WARN_EVERY_S = 10.0

    def __init__(self, trig, echo, window_ms=None, lost_as_object=False):
        """lost_as_object=True: trong cửa sổ đo, số lần mất vang (mất/ngắn) nhiều hơn số lần đo được
        -> trả LOST_CM (= có vật). Chỉ áp dụng khi cảm biến ĐÃ TỪNG đo được số hợp lệ từ lúc chạy
        (chứng tỏ dây/nguồn ổn), để cảm biến hỏng/lỏng dây ngay từ đầu không bị hiểu thành có vật."""
        self.trig, self.echo = trig, echo
        self.lost_as_object = lost_as_object
        if window_ms:
            self.WINDOW_S = window_ms / 1000
        # partial=True: không chặn chờ hàng đợi đầy (không đọc .distance của gpiozero nữa, xem _PingSensor)
        self.s = _PingSensor(echo=echo, trigger=trig, max_distance=2.0, queue_len=1, partial=True)
        self._last_warn = 0.0
        log.info("Siêu âm HC-SR04: TRIG GPIO%s, ECHO GPIO%s, pin factory %s, mất vang = %s", trig, echo,
                 type(self.s.pin_factory).__name__, "CÓ VẬT" if lost_as_object else "khay trống")

    def recent(self, window_s=None):
        """Các lần ping trong window_s giây gần nhất: [(tuổi_s, cm|None, trạng thái)], cũ trước."""
        now = time.monotonic()
        w = self.WINDOW_S if window_s is None else window_s
        with self.s.pings_lock:
            pings = list(self.s.pings)
        return [(now - t, cm, st) for t, cm, st in pings if now - t <= w]

    def _is_lost(self, pings):
        """Mất vang chiếm đa số trong cửa sổ đo (và cảm biến đã từng đo được -> không phải lỗi dây từ đầu)."""
        if not (self.lost_as_object and self.s.ever_ok):
            return False
        lost = sum(st in self.LOST for _, _, st in pings)
        ok = sum(cm is not None for _, cm, _ in pings)
        return lost > ok

    def distance_cm(self):
        pings = self.recent()
        if self._is_lost(pings):
            return self.LOST_CM
        vals = [cm for _, cm, _ in pings if cm is not None]
        if vals:
            return statistics.median(vals)
        now = time.monotonic()
        if now - self._last_warn >= self.WARN_EVERY_S:
            self._last_warn = now
            log.warning("Siêu âm: %.1f s gần nhất không có lần đo hợp lệ nào (%s) -> tạm coi khay trống. %s",
                        self.WINDOW_S, self.describe(pings) or "không có lần ping nào: luồng đo không chạy?",
                        "Ngắn = vật quá sát cảm biến; mất = không có vang (kiểm tra dây TRIG/ECHO, nguồn 5V, "
                        "cầu chia áp ở ECHO, hoặc vật mềm/nghiêng hút sóng); bận = ECHO kẹt mức cao.")
        return self.NO_DATA_CM

    def peek_cm(self):
        """Trung vị các lần đo hợp lệ gần đây (None nếu không có) để HIỂN THỊ: chỉ đọc, không in cảnh báo,
        không áp dụng lost_as_object -> luồng khác (đồng bộ lên web) gọi được mà không ảnh hưởng điều khiển."""
        vals = [cm for _, cm, _ in self.recent() if cm is not None]
        return statistics.median(vals) if vals else None

    @staticmethod
    def describe(pings):
        """Chuỗi log các lần ping: '30.2 30.4 mất 30.1 ngắn' (cũ trước)."""
        return " ".join(f"{cm:.1f}" if cm is not None else STATUS_MARK.get(st, st) for _, cm, st in pings)

    def detail(self, window_s=1.0):
        """Tóm tắt các lần ping trong window_s giây (cho log định kỳ)."""
        pings = self.recent(window_s)
        bad = {}
        for _, cm, st in pings:
            if cm is None:
                bad[STATUS_MARK.get(st, st)] = bad.get(STATUS_MARK.get(st, st), 0) + 1
        tail = f", hỏng: {', '.join(f'{k} {v}' for k, v in bad.items())}" if bad else ""
        if self._is_lost(self.recent()):
            tail += " -> MẤT VANG, coi là CÓ VẬT"
        return f"{len(pings)} lần/{window_s:g}s [{self.describe(pings)}]{tail}"

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
