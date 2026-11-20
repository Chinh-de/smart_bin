"""Máy trạng thái: phát hiện vật -> chụp -> phân loại -> xoay + lật khay vào thùng -> kiểm tra khay trống
-> ghi log + gửi server.

    IDLE ──(vật < trigger_cm, yên stable_ms | phím Enter)──> SETTLE ─> CAPTURE ─> CLASSIFY ─> DROP ─> CHECK
      ^                                                                                               │
      └──────────────── COOLDOWN <── LOG <── (khay trống: BIN | còn vật sau 2 lần lật: ERROR) <────────┘
    Cửa hông mở (nếu có công tắc): DOOR, bỏ qua trigger, servo ngắt xung, chờ đóng cửa."""
import csv
import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2

from smartbin.servo import RESEAT_DEG

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
        self._distance_log_s = None       # --show-distance ghi đè; không thì đọc hw (web chỉnh được lúc chạy)
        self.manual = threading.Event()   # phím Enter (main.py --keyboard): chạy như khi siêu âm phát hiện vật
        self.commands = queue.Queue()     # lệnh thử servo từ web (smartbin/remote.py), chạy lúc đang chờ vật

    # ---------- tiện ích ----------
    @property
    def distance_log_s(self):
        """In giá trị siêu âm định kỳ khi chờ vật (0 = tắt); --show-distance in mỗi lần đọc."""
        return self._distance_log_s if self._distance_log_s is not None else self.hw.get("distance_log_s", 1.0)

    @distance_log_s.setter
    def distance_log_s(self, v):
        self._distance_log_s = v

    def _set(self, state):
        self.state = state
        log.debug("-> %s", state)

    def _sleep_ms(self, key):
        time.sleep(self.hw[key] / 1000)

    def _occupied(self):
        return self.sensor.distance_cm() < self.hw["trigger_cm"]

    def _clear(self):
        return self.sensor.distance_cm() > self.hw["clear_cm"]

    def _sensor_detail(self, window_s=1.0):
        """Các lần ping thô gần đây (chỉ cảm biến thật có), để log thấy cả lần đo hỏng."""
        f = getattr(self.sensor, "detail", None)
        return f" | {f(window_s)}" if f else ""

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
        """Chờ có rác trên khay (theo trigger_mode), hoặc có phím bấm. Cửa mở thì không nhận."""
        if self.hw.get("trigger_mode", "hand") == "hand":
            return self._wait_hand()
        return self._wait_object()

    COMMAND_MAX_AGE_S = 15

    def command(self, cmd):
        """Nhận lệnh từ web (luồng khác): {"cmd": "bin", "bin": 2} hoặc {"cmd": "home"}."""
        self.commands.put((time.time(), cmd))

    def _run_commands(self):
        """Chạy lệnh thử servo đang chờ. Chỉ gọi lúc đang chờ vật (khay trống, servo đứng yên).
        Trả True nếu có chạy lệnh (vòng chờ nên bắt đầu đếm lại)."""
        ran = False
        while True:
            try:
                t, cmd = self.commands.get_nowait()
            except queue.Empty:
                return ran
            if time.time() - t > self.COMMAND_MAX_AGE_S:
                log.warning("Bỏ lệnh từ web quá cũ: %s", cmd)
                continue
            if self._door_open():
                log.warning("Cửa hông đang mở, bỏ lệnh từ web: %s", cmd)
                continue
            prev = self.state
            self._set("TEST")
            try:
                if cmd.get("cmd") == "bin" and cmd.get("bin") in self.decider.bins:
                    b = self.decider.bins[cmd["bin"]]
                    log.info("Web: thử thả vào thùng %s %s (ngang %s°, lật %+d)", b["id"], b["name"], b["pan"],
                             b["tilt_dir"])
                    self.servos.dump(b["pan"], b["tilt_dir"])
                elif cmd.get("cmd") == "home":
                    log.info("Web: về vị trí nghỉ (lùi ra %s° rồi quay mượt về từng servo)", RESEAT_DEG)
                    self.servos.home(reseat=True)
                else:
                    log.warning("Lệnh từ web không hợp lệ: %s", cmd)
                    continue
                log.info("Web: xong lệnh")
                ran = True
            except Exception:
                log.exception("Lỗi khi chạy lệnh từ web %s", cmd)
            finally:
                self._set(prev)

    def _poll_common(self, state):
        """Phần chung mỗi vòng chờ: cửa mở / phím bấm. Trả True nếu phím bấm (bắt đầu nhận diện ngay)."""
        if self._door_open():
            self._wait_door_closed()
            self.manual.clear()
            state.clear()
            return False
        if "hand_since" not in state and self._run_commands():   # tay đang trong khay thì không chạy servo
            state.clear()          # số đo lúc servo chạy thử không tính, đếm lại từ đầu
            return False
        if self.manual.is_set():
            self.manual.clear()
            log.info("Kích hoạt bằng phím bấm")
            return True
        return False

    def _log_distance(self, d, label, state):
        now = time.time()
        if self.distance_log_s > 0 and now - state.get("last_print", 0.0) >= self.distance_log_s:
            log.info("Siêu âm: %.1f cm (%s)%s", d, label, self._sensor_detail(max(self.distance_log_s, 0.5)))
            state["last_print"] = now

    def _wait_hand(self):
        """trigger_mode "hand": vật nhỏ, siêu âm không thấy vật mà chỉ thấy TAY lúc đặt vật vào.

            chờ tay ──(< trigger_cm)──> tay trong khay ──(> clear_cm liên tục stable_ms)──> NHẬN DIỆN
                                              └──(sau near_hold_ms vẫn chưa > clear_cm: vật đủ to, siêu âm thấy)──┘
        Khi tay ở trong: các lần đọc xa ngắn hơn stable_ms (tay di chuyển, đọc hụt) không tính là đã rút tay.
        Đọc trong khoảng trigger_cm..clear_cm: chưa chắc tay đã ra, chưa tính."""
        trig, clear = self.hw["trigger_cm"], self.hw["clear_cm"]
        release = self.hw["stable_ms"] / 1000
        near_hold = self.hw.get("near_hold_ms", 3000) / 1000
        log.info("Chờ tay đặt vật: siêu âm < %s cm, rồi rút tay ra (> %s cm liên tục %.1fs) -> nhận diện "
                 "(đang đo %.1f cm%s)", trig, clear, release, self.sensor.distance_cm(), self._sensor_detail())
        st = {}       # hand_since: lúc thấy tay; far_since: lúc bắt đầu đọc xa (tay rút ra)
        while True:
            if self._poll_common(st):
                return
            d = self.sensor.distance_cm()
            near, far = d < trig, d > clear
            now = time.time()
            if "hand_since" not in st:
                self._log_distance(d, "chờ tay" + (", GẦN" if near else ""), st)
                if near:
                    st["hand_since"] = now
                    st.pop("far_since", None)
                    log.info("Siêu âm: %.1f cm -> có tay/vật trong khay, chờ rút tay ra%s", d, self._sensor_detail())
            else:
                self._log_distance(d, "tay trong khay" if not far else "tay đã ra?", st)
                if far:
                    st.setdefault("far_since", now)
                    if now - st["far_since"] >= release:
                        log.info("Siêu âm: %.1f cm liên tục %.1fs -> đã rút tay (tay ở trong %.1fs) -> bắt đầu "
                                 "nhận diện%s", d, release, st["far_since"] - st["hand_since"], self._sensor_detail())
                        return
                else:
                    if "far_since" in st:
                        log.debug("Siêu âm: %.1f cm -> tay vẫn còn (đọc xa %.2fs, chưa đủ)", d, now - st.pop("far_since"))
                    if near_hold > 0 and now - st["hand_since"] >= near_hold:
                        log.info("Siêu âm: vẫn %.1f cm (chưa > %s cm) sau %.1fs -> có vật nằm trên khay -> bắt đầu "
                                 "nhận diện%s", d, clear, near_hold, self._sensor_detail())
                        return
            self._sleep_ms("poll_ms")

    def _wait_object(self):
        """trigger_mode "object": chờ vật nằm yên stable_ms trước siêu âm (vật đủ to để siêu âm thấy).

        HC-SR04 hay mất tiếng vang (vật nhỏ, mặt nghiêng, vật mềm) và đọc vọt lên vài chục cm dù vật vẫn
        nằm đó: các lần đọc xa ngắn hơn miss_ms được bỏ qua, không làm đếm lại thời gian ổn định."""
        hold, miss = self.hw["stable_ms"] / 1000, self.hw.get("miss_ms", 400) / 1000
        since, last_near, was_near, last_print = None, 0.0, False, 0.0
        trigger = self.hw["trigger_cm"]
        log.info("Chờ vật: siêu âm < %s cm, yên %.1fs (đang đo %.1f cm%s)", trigger, hold,
                 self.sensor.distance_cm(), self._sensor_detail())
        while True:
            if self._door_open():
                self._wait_door_closed()
                self.manual.clear()
                since, was_near = None, False
                continue
            if since is None and self._run_commands():         # có vật trên khay thì không chạy servo
                since, was_near = None, False
                continue
            if self.manual.is_set():
                self.manual.clear()
                log.info("Kích hoạt bằng phím bấm")
                return
            d = self.sensor.distance_cm()
            near = d < trigger
            now = time.time()
            if self.distance_log_s > 0 and now - last_print >= self.distance_log_s:
                log.info("Siêu âm: %.1f cm (%s, ngưỡng < %s)%s", d, "GẦN" if near else "xa", trigger,
                         self._sensor_detail(max(self.distance_log_s, 0.5)))
                last_print = now
            if near:
                last_near = now
                if since is None:
                    since = now
                    log.info("Siêu âm: %.1f cm -> có vật (< %s cm), chờ ổn định %.1fs%s", d, trigger, hold,
                             self._sensor_detail())
                if now - since >= hold:
                    log.info("Siêu âm: vật ổn định ở %.1f cm -> bắt đầu nhận diện", d)
                    return
            elif since is not None:
                if now - last_near > miss:     # xa liên tục quá miss_ms: vật thật sự đã rời đi
                    log.info("Siêu âm: %.1f cm -> vật rời đi trước khi ổn định%s", d, self._sensor_detail())
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
        log.info("Khoảng cách lúc chụp: %s cm%s", distance, self._sensor_detail())

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
                log.info("Kiểm tra khay: %.1f cm -> trống%s", dist, self._sensor_detail())
                return "BIN"
            log.warning("Sau khi lật vẫn còn vật trên khay (%.1f cm%s)", dist, self._sensor_detail())
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
