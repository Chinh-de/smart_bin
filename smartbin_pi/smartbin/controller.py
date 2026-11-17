"""Máy trạng thái: phát hiện vật -> chụp -> phân loại -> xoay + lật khay vào thùng -> kiểm tra khay trống
-> ghi log + gửi server.

    IDLE ──(vật < trigger_cm, yên stable_ms | phím Enter)──> SETTLE ─> CAPTURE ─> CLASSIFY ─> DROP ─> CHECK
      ^                                                                                               │
      └──────────────── COOLDOWN <── LOG <── (khay trống: BIN | còn vật sau 2 lần lật: ERROR) <────────┘
    Cửa hông mở (nếu có công tắc): DOOR, bỏ qua trigger, servo ngắt xung, chờ đóng cửa."""
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
DROP_ATTEMPTS = 2       # rác chưa rơi hết thì lật lại 1 lần, vẫn còn -> báo lỗi kẹt


class Controller:
    def __init__(self, app_cfg, hw, sensor, camera, classifier, decider, servos, ind, uploader, data_dir,
                 door=None):
        self.app, self.hw = app_cfg, hw
        self.sensor, self.camera, self.clf, self.decider = sensor, camera, classifier, decider
        self.servos, self.ind, self.up, self.door = servos, ind, uploader, door
        self.data = Path(data_dir)
        self.csv_path = self.data / "logs" / "events.csv"
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_name = Path(app_cfg["model"]).name
        self.state = "IDLE"
        self.show_distance = False        # --show-distance: in khoảng cách siêu âm ~0.5 s/lần khi chờ vật
        self.manual = threading.Event()   # phím Enter (main.py --keyboard): chạy như khi siêu âm phát hiện vật

    # ---------- tiện ích ----------
    def _set(self, state):
        self.state = state
        log.debug("-> %s", state)

    def _sleep_ms(self, key):
        time.sleep(self.hw[key] / 1000)

    def _occupied(self):
        return self.sensor.distance_cm() < self.hw["trigger_cm"]

    def _clear(self):
        return self.sensor.distance_cm() > self.hw["clear_cm"]

    def _door_open(self):
        return self.door is not None and self.door.is_open()

    def _wait(self, cond, hold_s):
        """Chờ cond() đúng liên tục hold_s giây."""
        since = None
        while True:
            if cond():
                since = since or time.time()
                if time.time() - since >= hold_s:
                    return
            else:
                since = None
            self._sleep_ms("poll_ms")

    def _wait_door_closed(self):
        if not self._door_open():
            return
        prev = self.state
        self._set("DOOR")
        log.warning("Cửa hông đang mở -> tạm dừng, chờ đóng cửa")
        self.ind.door_open(True)
        try:
            self._wait(lambda: not self._door_open(), 1.0)
        finally:
            self.ind.door_open(False)
        log.info("Đã đóng cửa, chạy tiếp")
        self._set(prev)

    def _wait_trigger(self):
        """Chờ vật nằm yên stable_ms trước siêu âm, hoặc có phím bấm. Cửa mở thì không nhận.

        HC-SR04 hay mất tiếng vang (vật nhỏ, mặt nghiêng, vật mềm) và đọc vọt lên vài chục cm dù vật vẫn
        nằm đó: các lần đọc xa ngắn hơn miss_ms được bỏ qua, không làm đếm lại thời gian ổn định."""
        hold, miss = self.hw["stable_ms"] / 1000, self.hw.get("miss_ms", 400) / 1000
        since, last_near, was_near, last_print = None, 0.0, False, 0.0
        trigger = self.hw["trigger_cm"]
        while True:
            if self._door_open():
                self._wait_door_closed()
                self.manual.clear()
                since, was_near = None, False
                continue
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
            if near:
                last_near = now
                if since is None:
                    since = now
                    log.info("Siêu âm: %.1f cm -> có vật (< %s cm), chờ ổn định %.1fs", d, trigger, hold)
                if now - since >= hold:
                    log.info("Siêu âm: vật ổn định ở %.1f cm -> bắt đầu nhận diện", d)
                    return
            elif since is not None:
                if now - last_near > miss:     # xa liên tục quá miss_ms: vật thật sự đã rời đi
                    log.info("Siêu âm: %.1f cm -> vật rời đi trước khi ổn định", d)
                    since = None
                elif was_near:
                    log.debug("Siêu âm: %.1f cm (đọc hụt, bỏ qua)", d)
            was_near = near
            self._sleep_ms("poll_ms")

    # ---------- vòng chính ----------
    def run(self):
        if not self.servos.ready:       # main.py đã đưa servo về nghỉ trước khi mở webcam
            self.servos.startup()
        log.info("Sẵn sàng. Ngưỡng phát hiện < %s cm", self.hw["trigger_cm"])
        while True:
            self._set("IDLE")
            self.manual.clear()          # phím bấm lúc đang xử lý bị bỏ, không dồn thành lượt sau
            self._wait_trigger()
            try:
                self.process_one()
            except Exception:
                log.exception("Lỗi khi xử lý vật")
                try:
                    self.servos.home()          # không để khay kẹt giữa chừng
                except Exception:
                    log.exception("Không đưa được servo về vị trí nghỉ")
                self._error_until_clear()
            self._set("COOLDOWN")
            self._sleep_ms("cooldown_ms")

    def process_one(self):
        t0 = time.time()
        self._set("SETTLE")
        self._sleep_ms("settle_ms")

        self._set("CAPTURE")
        self.ind.light(True)
        try:
            self._sleep_ms("light_warmup_ms")
            frames = self.camera.capture(self.app["n_frames"], self.app["frame_interval_ms"])
        finally:
            self.ind.light(False)
        distance = round(self.sensor.distance_cm(), 1)
        log.info("Khoảng cách lúc chụp: %s cm", distance)

        self._set("CLASSIFY")
        probs, inf_ms = self.clf.predict(frames)
        d = self.decider.decide(probs)
        log.info("Nhận diện: %s", ", ".join(f"{c} {p:.2f}" for c, p in d.top3))

        result, message = self._drop(d), d.message
        if result == "ERROR":
            message = f"Rác kẹt trên khay sau {DROP_ATTEMPTS} lần lật (định thả vào thùng {d.bin_id})"

        self._set("LOG")
        try:
            self._log_event(d, result, message, frames[len(frames) // 2], distance, inf_ms,
                            (time.time() - t0) * 1000)
        except Exception:
            # vật đã xử lý xong; lỗi ghi log (đầy thẻ nhớ...) không phải lỗi cơ khí nên không bật báo lỗi
            log.exception("Không ghi được sự kiện")

        if result == "BIN":
            self.ind.ok_blink()
        else:
            log.error(message)
            self._error_until_clear()

    def _drop(self, d):
        b = d.bin
        for attempt in range(1, DROP_ATTEMPTS + 1):
            self._wait_door_closed()     # không lật khay khi tay đang ở trong thùng
            self._set("DROP")
            log.info("-> thùng %s %s (ngang %s°, lật %+d)%s%s", b["id"], b["name"], b["pan"], b["tilt_dir"],
                     f" [{d.message}]" if d.message else "", f" (lần {attempt})" if attempt > 1 else "")
            self.servos.dump(b["pan"], b["tilt_dir"])

            self._set("CHECK")
            self._sleep_ms("check_ms")   # chờ siêu âm có số đo mới sau khi khay về nghỉ
            dist = self.sensor.distance_cm()
            if dist >= self.hw["trigger_cm"]:
                return "BIN"
            log.warning("Sau khi lật vẫn còn vật trên khay (%.1f cm)", dist)
        return "ERROR"

    def _error_until_clear(self):
        """Báo lỗi tới khi khay trống liên tục error_clear_s (người lấy vật ra)."""
        self._set("ERROR")
        self.ind.error(True)
        try:
            self._wait(self._clear, self.hw["error_clear_s"])
        finally:
            self.ind.error(False)

    # ---------- log ----------
    def _log_event(self, d, result, message, frame, distance, inf_ms, total_ms):
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

        dropped = result == "BIN"
        meta = {
            "event_id": event_id, "device_id": self.app["device_id"], "timestamp": ts,
            "result": result, "bin_id": d.bin_id if dropped else None,
            "bin_name": d.bin_name if dropped else None, "message": message,
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
