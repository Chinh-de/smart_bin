"""Webcam USB qua OpenCV. Một luồng nền đọc liên tục để luôn có khung mới nhất
(webcam USB có bộ đệm; đọc trực tiếp khi cần sẽ nhận khung cũ)."""
import sys
import threading
import time

import cv2


class UsbCamera:
    def __init__(self, index=0, width=640, height=480):
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"Không mở được webcam {index}. Kiểm tra cáp USB / lệnh: v4l2-ctl --list-devices")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        self._frame, self._seq = None, 0
        self._cond = threading.Condition()
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="camera").start()
        if self._wait_new(-1, timeout=5) is None:
            raise RuntimeError("Webcam không trả về hình")

    def _loop(self):
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            with self._cond:
                self._frame, self._seq = frame, self._seq + 1
                self._cond.notify_all()

    def _wait_new(self, after_seq, timeout=2.0):
        with self._cond:
            # phải có khung thật: lúc khởi động _seq = 0 nhưng _frame vẫn là None
            if not self._cond.wait_for(lambda: self._frame is not None and self._seq > after_seq, timeout=timeout):
                return None
            return self._seq, self._frame.copy()

    def capture(self, n=5, interval_ms=80):
        """Chụp n khung MỚI (chụp sau thời điểm gọi hàm)."""
        with self._cond:
            seq = self._seq
        frames = []
        for _ in range(n):
            got = self._wait_new(seq)
            if got is None:
                raise RuntimeError("Webcam ngừng trả hình")
            seq, frame = got
            frames.append(frame)
            time.sleep(interval_ms / 1000)
        return frames

    def close(self):
        self._running = False
        self.cap.release()
