"""Đẩy video trực tiếp từ webcam lên web server (từng khung JPEG qua HTTP).
Chỉ mã hoá + gửi khi server báo đang có người xem; không ai xem thì chỉ hỏi server mỗi giây một lần."""
import logging
import threading
import time

import cv2
import requests

log = logging.getLogger(__name__)
IDLE_POLL_S = 1.0      # không ai xem: hỏi server mỗi giây
RETRY_S = 5.0          # lỗi mạng: thử lại sau


class Streamer:
    def __init__(self, server_url, api_key, device_id, camera, fps=5, width=480, quality=70):
        self.base = f"{server_url.rstrip('/')}/api/stream/{device_id}"
        self.camera, self.width, self.quality = camera, width, quality
        self.period = 1.0 / max(float(fps), 0.5)
        self.http = requests.Session()          # giữ kết nối: gửi nhiều khung nhỏ không phải bắt tay lại
        self.http.headers["X-API-Key"] = api_key
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="streamer").start()

    def _encode(self, frame):
        h, w = frame.shape[:2]
        if w > self.width:
            frame = cv2.resize(frame, (self.width, round(h * self.width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buf.tobytes() if ok else None

    def _watchers(self, r):
        r.raise_for_status()
        return r.json().get("watch", 0) > 0

    def _loop(self):
        watching, last_seq, ok_before = False, -1, None
        while not self._stop.is_set():
            t0, ok = time.time(), True
            try:
                if watching:
                    got = self.camera.latest()
                    if got and got[0] != last_seq:          # webcam chưa có khung mới thì bỏ qua lượt này
                        last_seq = got[0]
                        jpg = self._encode(got[1])
                        if jpg:
                            watching = self._watchers(self.http.post(
                                self.base + "/frame", data=jpg, timeout=5, headers={"Content-Type": "image/jpeg"}))
                else:
                    watching = self._watchers(self.http.get(self.base + "/want", timeout=5))
            except (requests.RequestException, ValueError) as e:
                ok, watching, why = False, False, type(e).__name__
                if isinstance(e, requests.HTTPError) and e.response is not None:
                    why = f"HTTP {e.response.status_code}" + (" (sai api_key?)" if e.response.status_code == 401 else "")
            except Exception:                              # luồng video không được chết vì lỗi bất kỳ
                log.exception("Lỗi streamer")
                ok, watching, why = False, False, "lỗi nội bộ"
            if ok != ok_before:
                if not ok:
                    log.warning("Stream: không gửi được tới server: %s", why)
                elif ok_before is False:
                    log.info("Stream: đã kết nối lại server")
                ok_before = ok
            wait = (self.period - (time.time() - t0)) if watching else (IDLE_POLL_S if ok else RETRY_S)
            self._stop.wait(max(wait, 0.01))

    def close(self):
        self._stop.set()
