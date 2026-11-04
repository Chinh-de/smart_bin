"""Webcam USB qua OpenCV. Một luồng nền đọc liên tục để luôn có khung mới nhất
(webcam USB có bộ đệm; đọc trực tiếp khi cần sẽ nhận khung cũ)."""
import logging
import sys
import threading
import time

import cv2

log = logging.getLogger(__name__)
REOPEN_AFTER_FAILS = 40        # ~2 s liên tục không có hình thì mở lại webcam


class UsbCamera:
    def __init__(self, index=0, width=640, height=480):
        self.index, self.size = index, (width, height)
        self.backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        self.cap = self._open()
        if not self.cap.isOpened():
            raise RuntimeError(f"Không mở được webcam {index}. Kiểm tra cáp USB / lệnh: v4l2-ctl --list-devices")

        self._frame, self._seq = None, 0
        self._cond = threading.Condition()
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="camera").start()
        if self._wait_new(-1, timeout=5) is None:
            raise RuntimeError("Webcam không trả về hình")

    def _open(self):
        cap = cv2.VideoCapture(self.index, self.backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
        return cap

    def _loop(self):
        fails = 0
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                fails += 1
                if fails >= REOPEN_AFTER_FAILS:
                    # webcam rớt (nguồn USB yếu, cáp lỏng): mở lại thay vì hỏng vĩnh viễn tới khi khởi động lại
                    log.warning("Webcam không trả hình, thử mở lại")
                    self.cap.release()
                    time.sleep(1)
                    self.cap = self._open()
                    fails = 0
                else:
                    time.sleep(0.05)
                continue
            fails = 0
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
