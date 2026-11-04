"""Gửi sự kiện lên web server qua hàng đợi trên đĩa.
Sự kiện được ghi vào queue/ trước, luồng nền gửi dần; mất mạng thì thử lại, có mạng gửi bù.
Server chống trùng bằng event_id nên gửi lại nhiều lần không sao."""
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
BACKOFF_S = [5, 10, 30, 60, 120, 300]


class Uploader:
    def __init__(self, server_url, api_key, device_id, queue_dir, heartbeat_s=30):
        self.url = server_url.rstrip("/")
        self.headers = {"X-API-Key": api_key}
        self.device_id = device_id
        self.queue = Path(queue_dir)
        self.failed = self.queue / "failed"
        self.failed.mkdir(parents=True, exist_ok=True)
        self.heartbeat_s = heartbeat_s
        self.started = time.time()
        self._wake = threading.Event()
        self._stop = threading.Event()
        threading.Thread(target=self._send_loop, daemon=True, name="uploader").start()
        threading.Thread(target=self._heartbeat_loop, daemon=True, name="heartbeat").start()

    def submit(self, meta, image_path):
        item = self.queue / f"{meta['timestamp'].replace(':', '')}_{meta['event_id']}.json"
        tmp = item.with_suffix(".tmp")
        tmp.write_text(json.dumps({"meta": meta, "image": str(image_path)}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, item)    # ghi nguyên tử: luồng gửi không đọc phải file dở
        self._wake.set()

    def pending(self):
        return len(list(self.queue.glob("*.json")))

    def _post(self, item):
        data = json.loads(item.read_text(encoding="utf-8"))
        with open(data["image"], "rb") as img:
            r = requests.post(f"{self.url}/api/events", headers=self.headers, timeout=10,
                              data={"meta": json.dumps(data["meta"], ensure_ascii=False)},
                              files={"image": ("image.jpg", img, "image/jpeg")})
        if r.ok:
            return True
        if 400 <= r.status_code < 500:
            # lỗi dữ liệu/khoá API: gửi lại cũng vô ích -> cất sang failed/ để xem sau
            log.error("Server từ chối %s: %s %s", item.name, r.status_code, r.text[:200])
            shutil.move(str(item), self.failed / item.name)
            return True
        raise requests.HTTPError(f"{r.status_code}")

    def _send_loop(self):
        fails = 0
        while not self._stop.is_set():
            items = sorted(self.queue.glob("*.json"))
            if not items:
                self._wake.wait(timeout=10)
                self._wake.clear()
                continue
            try:
                if self._post(items[0]):
                    items[0].unlink(missing_ok=True)
                fails = 0
            except (requests.RequestException, OSError) as e:
                wait = BACKOFF_S[min(fails, len(BACKOFF_S) - 1)]
                fails += 1
                log.warning("Gửi thất bại (%s), còn %d sự kiện chờ, thử lại sau %ds", e, len(items), wait)
                self._stop.wait(wait)

    def _heartbeat_loop(self):
        while not self._stop.wait(self.heartbeat_s):
            try:
                requests.post(f"{self.url}/api/heartbeat", headers=self.headers, timeout=5, json={
                    "device_id": self.device_id,
                    "uptime_s": int(time.time() - self.started),
                    "cpu_temp_c": _cpu_temp(),
                    "pending": self.pending(),
                })
            except requests.RequestException:
                pass

    def close(self):
        self._stop.set()
        self._wake.set()


def _cpu_temp():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None
