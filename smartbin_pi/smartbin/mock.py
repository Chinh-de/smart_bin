"""Giả lập phần cứng để chạy thử trên laptop.
- Cảm biến: nhấn Enter = đặt / lấy vật trên khay.
- Camera: lần lượt lấy ảnh trong một thư mục (mỗi lần chụp = 1 ảnh).
- Servo: PanTilt(mock=True) (xung giả); attach_mock_servo() in góc và cho vật rơi khi khay lật.
- Đèn/LED/còi: in ra màn hình."""
import itertools
import logging
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("mock")
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class MockSensor:
    def __init__(self, trigger_cm):
        self.near, self.far = trigger_cm / 2, 100.0
        self.present = False
        threading.Thread(target=self._keyboard, daemon=True, name="mock-keys").start()
        print(">>> GIẢ LẬP: nhấn Enter để đặt/lấy vật trên khay")

    def _keyboard(self):
        while True:
            try:
                input()
            except EOFError:
                return
            self.present = not self.present
            print(f">>> khay: {'CÓ VẬT' if self.present else 'TRỐNG'}")

    def distance_cm(self):
        return self.near if self.present else self.far

    def close(self):
        pass


class NullSensor:
    """Không có siêu âm (chế độ --bench): khay luôn trống, kích hoạt bằng phím."""
    present = False

    def distance_cm(self):
        return 100.0

    def close(self):
        pass


def attach_mock_servo(pantilt, sensor):
    """In góc servo; khi khay lật khỏi vị trí nghỉ thì vật trên khay rơi xuống."""
    def moved(name, angle):
        print(f">>> servo {'A ngang' if name == 'pan' else 'B dọc'} -> {angle:g}°")
        if name == "tilt" and not pantilt.at_rest and sensor.present:
            sensor.present = False
            print(">>> (vật đã rơi khỏi khay)")
    pantilt.on_moved = moved


class MockCamera:
    def __init__(self, image_dir=None):
        files = sorted(p for p in Path(image_dir).rglob("*") if p.suffix.lower() in IMG_EXT) if image_dir else []
        if image_dir and not files:
            raise RuntimeError(f"Không có ảnh trong {image_dir}")
        self.files = itertools.cycle(files) if files else None
        if not files:
            log.warning("Không có --images: camera giả trả về ảnh xám")
        self._cur, self._tick = np.full((480, 640, 3), 128, np.uint8), 0
        cv2.putText(self._cur, "MOCK CAMERA", (180, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)

    def capture(self, n=5, interval_ms=0):
        if self.files is None:
            return [np.full((480, 640, 3), 128, np.uint8)] * n
        path = next(self.files)
        img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)   # đọc được đường dẫn tiếng Việt
        print(f">>> camera giả: {path.name}")
        self._cur = img
        return [img] * n

    def latest(self):
        self._tick += 1
        return self._tick, self._cur

    def close(self):
        pass


class MockIndicators:
    def light(self, on):
        print(f">>> đèn buồng chụp {'BẬT' if on else 'TẮT'}")

    def ok_blink(self):
        print(">>> LED xanh nháy")

    def error(self, on):
        print(">>> LỖI: LED đỏ nháy + còi (nhấn Enter để lấy vật ra)" if on else ">>> hết lỗi")

    def door_open(self, on):
        print(f">>> LED đỏ {'sáng (cửa mở)' if on else 'tắt'}")

    def close(self):
        pass
