"""Máy trạng thái: phát hiện vật -> chụp -> phân loại -> thả/từ chối -> ghi log + gửi server."""
import csv
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2

log = logging.getLogger(__name__)

CSV_FIELDS = ["timestamp", "event_id", "result", "bin_id", "bin_name", "top_class", "top_prob",
              "top3", "uncertain", "distance_cm", "inference_ms", "total_ms", "image"]


class Controller:
    def __init__(self, app_cfg, hw, sensor, camera, classifier, decider, act, uploader, data_dir):
        self.app, self.hw = app_cfg, hw
        self.sensor, self.camera, self.clf, self.decider, self.act, self.up = \
            sensor, camera, classifier, decider, act, uploader
        self.data = Path(data_dir)
        self.csv_path = self.data / "logs" / "events.csv"
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_name = Path(app_cfg["model"]).name
        self.state = "IDLE"
        self.show_distance = False        # --show-distance: in khoảng cách siêu âm ~0.5 s/lần khi chờ vật
        self.manual = threading.Event()   # đặt bởi phím bấm (main.py --keyboard): chạy nhận diện như khi siêu âm phát hiện vật

    # ---------- tiện ích ----------
    def _set(self, state):
        self.state = state
        log.debug("-> %s", state)

    def _sleep_ms(self, key):
        time.sleep(self.hw[key] / 1000)

    def _clear(self):
        return self.sensor.distance_cm() > self.hw["clear_cm"]

    def _wait(self, cond, hold_s, timeout_s=None):
        """Chờ cond() đúng liên tục hold_s giây. Trả False nếu quá timeout_s."""
        t0, since = time.time(), None
        while True:
            if cond():
                since = since or time.time()
                if time.time() - since >= hold_s:
                    return True
            else:
                since = None
            if timeout_s is not None and time.time() - t0 > timeout_s:
                return False
            self._sleep_ms("poll_ms")

    def _wait_trigger(self):
        """Chờ vật nằm yên stable_ms trước siêu âm, hoặc có phím bấm."""
        hold, since, was_near, last_print = self.hw["stable_ms"] / 1000, None, False, 0.0
        trigger = self.hw["trigger_cm"]
        while True:
            if self.manual.is_set():
                self.manual.clear()
                log.info("Kích hoạt bằng phím bấm")
                return
            d = self.sensor.distance_cm()
            near = d < trigger
            now = time.time()
            if self.show_distance and now - last_print >= 0.5:
                log.info("Siêu âm: %.1f cm", d)
                last_print = now
            if near != was_near:       # chỉ log khi vào/ra ngưỡng, không spam mỗi lần đọc
                log.info("Siêu âm: %.1f cm -> %s", d, f"có vật (< {trigger} cm), chờ ổn định {hold:.1f}s"
                         if near else "vật rời đi trước khi ổn định")
                was_near = near
            if near:
                since = since or now
                if now - since >= hold:
                    log.info("Siêu âm: vật ổn định ở %.1f cm -> bắt đầu nhận diện", d)
                    return
            else:
                since = None
            self._sleep_ms("poll_ms")

    def _rest(self):
        self.act.set_chute(self.hw["chute_rest_angle"])
        self.act.set_tray(self.hw["tray_rest_angle"])
        time.sleep(0.5)
        self.act.release_servos()

    # ---------- vòng chính ----------
    def run(self):
        self._rest()
        log.info("Sẵn sàng. Ngưỡng phát hiện < %s cm", self.hw["trigger_cm"])
        while True:
            self._set("IDLE")
            self.manual.clear()          # phím bấm lúc đang xử lý bị bỏ, không dồn thành lượt sau
            self._wait_trigger()
            try:
                self.process_one()
            except Exception:
                log.exception("Lỗi khi xử lý vật")
                self._error_until_clear()
                try:
                    self._rest()                # đưa khay/máng về vị trí nghỉ, không để kẹt giữa chừng
                except Exception:
                    log.exception("Không đưa được servo về vị trí nghỉ")
            self._set("COOLDOWN")
            self._sleep_ms("cooldown_ms")

    def process_one(self):
        t0 = time.time()
        self._set("SETTLE")
        self._sleep_ms("settle_ms")

        self._set("CAPTURE")
        self.act.light(True)
        self._sleep_ms("light_warmup_ms")
        try:
            frames = self.camera.capture(self.app["n_frames"], self.app["frame_interval_ms"])
        finally:
            self.act.light(False)
        distance = round(self.sensor.distance_cm(), 1)
        log.info("Khoảng cách lúc chụp: %s cm", distance)

        self._set("CLASSIFY")
        probs, inf_ms = self.clf.predict(frames)
        d = self.decider.decide(probs)
        log.info("Nhận diện: %s", ", ".join(f"{c} {p:.2f}" for c, p in d.top3))

        if d.result == "BIN":
            result = self._drop(d)
        else:
            result = self._reject(d)

        self._set("LOG")
        try:
            self._log_event(d, result, frames[len(frames) // 2], distance, inf_ms, (time.time() - t0) * 1000)
        except Exception:
            # vật đã xử lý xong; lỗi ghi log (đầy thẻ nhớ...) không phải lỗi cơ khí nên không bật báo lỗi
            log.exception("Không ghi được sự kiện")
        self._rest()

    def _drop(self, d):
        self._set("ROUTE")
        log.info("-> %s (thùng %s, %s°)%s", d.bin_name, d.bin_id, d.angle, " [không chắc chắn]" if d.uncertain else "")
        self.act.set_chute(d.angle)
        self._sleep_ms("route_ms")

        self._set("DROP")
        for attempt in range(2):                        # lần 2 = lắc lại khi rác bị kẹt
            self.act.set_tray(self.hw["tray_tilt_angle"])
            self._sleep_ms("drop_ms")
            self.act.set_tray(self.hw["tray_rest_angle"])
            self._sleep_ms("after_drop_ms")
            d = self.sensor.distance_cm()
            if d > self.hw["clear_cm"]:
                log.info("Khay trống (siêu âm %.1f cm > %s cm)", d, self.hw["clear_cm"])
                self.act.ok_blink()
                return "BIN"
            log.warning("Vật vẫn còn trên khay (lần %d, siêu âm %.1f cm <= %s cm)", attempt + 1, d, self.hw["clear_cm"])
        self._error_until_clear()
        return "ERROR"

    def _reject(self, d):
        self._set("REJECT")
        log.warning("TỪ CHỐI: %s", d.message)
        self.act.reject_alarm(True)
        try:
            self._wait(self._clear, 1.0, timeout_s=self.hw["reject_timeout_s"])
        finally:
            self.act.reject_alarm(False)
        return "REJECT"

    def _error_until_clear(self):
        self._set("ERROR")
        self.act.error(True)
        self._wait(self._clear, self.hw["error_clear_s"])
        self.act.error(False)

    # ---------- log ----------
    def _log_event(self, d, result, frame, distance, inf_ms, total_ms):
        event_id = uuid.uuid4().hex
        ts = datetime.now().astimezone().isoformat(timespec="seconds")

        img_dir = self.data / "captures" / d.top_class
        img_dir.mkdir(parents=True, exist_ok=True)
        img_path = img_dir / f"{event_id}.jpg"
        w = self.app["save_image_width"]
        if frame.shape[1] > w:
            frame = cv2.resize(frame, (w, round(frame.shape[0] * w / frame.shape[1])), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            buf.tofile(str(img_path))

        meta = {
            "event_id": event_id, "device_id": self.app["device_id"], "timestamp": ts,
            "result": result, "bin_id": d.bin_id if result == "BIN" else None,
            "bin_name": d.bin_name if result == "BIN" else None,
            "message": d.message,
            "top_class": d.top_class, "top_prob": d.top_prob, "top3": d.top3,
            "bin_probs": d.bin_probs, "uncertain": d.uncertain,
            "distance_cm": distance, "inference_ms": round(inf_ms, 1), "total_ms": round(total_ms),
            "model": self.model_name,
        }

        new = not self.csv_path.exists()
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, CSV_FIELDS, extrasaction="ignore")
            if new:
                wr.writeheader()
            wr.writerow({**meta, "top3": json.dumps(d.top3), "image": str(img_path)})

        if self.up:
            self.up.submit(meta, img_path)
        log.info("Đã ghi sự kiện %s (%s, %.0f ms)", event_id[:8], result, total_ms)
