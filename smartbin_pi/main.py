"""Thùng rác phân loại 4 ngăn — chương trình chính trên Raspberry Pi.

    python main.py                      # chạy thật (webcam USB + HC-SR04 + 2 servo PWM phần cứng)
    python main.py --keyboard           # như trên, thêm Enter = kích hoạt nhận diện
    python main.py --bench              # chỉ webcam + model, không cần GPIO
    python main.py --mock --images DIR  # giả lập trên laptop: Enter = đặt/lấy vật, ảnh lấy từ DIR
"""
import argparse
import logging
import logging.handlers
import signal
import sys
import threading
from pathlib import Path

from smartbin import ROOT, load_config, load_json
from smartbin.controller import Controller
from smartbin.decider import Decider
from smartbin.servo import PanTilt
from smartbin.servo import validate as validate_servo
from smartbin.uploader import Uploader


def setup_logging(data_dir, verbose):
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  # xoay vòng: app.log không phình mãi trên thẻ nhớ
                  logging.handlers.RotatingFileHandler(data_dir / "logs" / "app.log", maxBytes=2_000_000,
                                                       backupCount=3, encoding="utf-8")],
    )


def _on_sigterm(*_):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)   # SIGTERM lần 2 không được cắt ngang lúc đưa servo về nghỉ
    raise KeyboardInterrupt        # systemctl stop gửi SIGTERM: đi qua khối finally để tắt đèn, đưa servo về nghỉ


def _keyboard_trigger(event):
    print(">>> Nhấn Enter để chụp + nhận diện ngay (như khi siêu âm phát hiện vật)")
    while True:
        try:
            input()
        except EOFError:
            return
        event.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="giả lập cảm biến/servo/camera")
    ap.add_argument("--images", help="(mock) thư mục ảnh thay cho camera")
    ap.add_argument("--no-upload", action="store_true", help="không gửi lên web server")
    ap.add_argument("--keyboard", action="store_true", help="nhấn Enter để kích hoạt nhận diện (test, ngoài siêu âm)")
    ap.add_argument("--bench", action="store_true",
                    help="chỉ test nhận diện: webcam + model thật, KHÔNG cần siêu âm/servo/GPIO; nhấn Enter để chụp")
    ap.add_argument("--show-distance", action="store_true",
                    help="in khoảng cách siêu âm mỗi lần đọc (mặc định ~1 s/lần, hardware.json -> distance_log_s)")
    ap.add_argument("--no-scale", action="store_true", help="không đọc cân load cell")
    ap.add_argument("--config", default="config")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # in tiếng Việt trên console Windows
    cfg = load_config(args.config)
    app, hw = cfg["app"], cfg["hw"]
    setup_logging(ROOT, args.verbose)
    log = logging.getLogger("main")

    model, labels = ROOT / app["model"], ROOT / app["labels"]
    if model.exists():
        from smartbin.classifier import Classifier
        clf = Classifier(model, labels, app["num_threads"])
        log.info("Model: %s", model.name)
    elif args.mock:
        from smartbin.classifier import RandomClassifier
        classes = load_json(app["labels"])["classes"] if labels.exists() else \
            ["battery", "biological", "cardboard", "clothes", "glass", "metal", "paper", "plastic", "shoes", "trash"]
        clf = RandomClassifier(classes)
        log.warning("Chưa có %s -> dùng bộ phân loại NGẪU NHIÊN để thử luồng", model)
    else:
        sys.exit(f"Không thấy model {model}. Chép model_int8.tflite + labels.json từ Kaggle vào models/")

    decider = Decider(cfg["bins"], clf.classes)
    validate_servo(hw["servo"], cfg["bins"]["bins"])

    fake_io = args.mock or args.bench
    if fake_io:
        hw = {**hw, **{k: min(v, 300) for k, v in hw.items() if k.endswith("_ms")}}   # chạy nhanh hơn khi giả lập
    servos = PanTilt(hw["pins"], hw["servo"], mock=fake_io)
    sc = hw["servo"]
    # chu trình khởi động thả vào thùng 1 (lỡ trên khay còn vật thì vào đó)
    warm = decider.bins[1]
    log.info("Servo khởi động: về nghỉ (ngang %s°, dọc %s°), chạy thử 1 lượt vào thùng %s rồi về nghỉ",
             sc["pan_rest"], sc["tilt_rest"], warm["id"])
    servos.startup(warm["pan"], warm["tilt_dir"])   # trước khi mở webcam: servo không phải chia nguồn với webcam
    door = None
    if args.mock:
        from smartbin.mock import MockCamera, MockIndicators, MockSensor, attach_mock_servo
        sensor = MockSensor(hw["trigger_cm"])
        camera = MockCamera(args.images)
        ind = MockIndicators()
        attach_mock_servo(servos, sensor)
    elif args.bench:
        from smartbin.camera import UsbCamera
        from smartbin.mock import MockIndicators, NullSensor, attach_mock_servo
        sensor = NullSensor()
        camera = UsbCamera(app["camera_index"], app["camera_width"], app["camera_height"])
        ind = MockIndicators()       # servo/đèn/còi chỉ in ra màn hình
        attach_mock_servo(servos, sensor)
        log.warning("Chế độ --bench: không dùng siêu âm/servo, chỉ nhận diện bằng webcam")
    else:
        from smartbin.camera import UsbCamera
        from smartbin.hardware import DoorSwitch, Indicators, UltrasonicSensor
        sensor = UltrasonicSensor(hw["pins"]["trig"], hw["pins"]["echo"], hw.get("distance_window_ms"),
                                  hw.get("lost_as_object", False))
        camera = UsbCamera(app["camera_index"], app["camera_width"], app["camera_height"])
        ind = Indicators(hw["pins"])
        if hw["pins"].get("door") is not None:
            door = DoorSwitch(hw["pins"]["door"])
            log.info("Công tắc cửa hông: GPIO%s", hw["pins"]["door"])

    uploader = None if args.no_upload else \
        Uploader(app["server_url"], app["api_key"], app["device_id"], ROOT / "queue", app["heartbeat_s"])

    streamer = None
    sc = app.get("stream", {})
    if uploader and sc.get("enabled", True) and hasattr(camera, "latest"):
        from smartbin.streamer import Streamer
        streamer = Streamer(app["server_url"], app["api_key"], app["device_id"], camera,
                            sc.get("fps", 5), sc.get("width", 480), sc.get("quality", 70))

    scale = None
    if hw["pins"].get("hx711_dt") is not None and not args.no_scale:
        from smartbin.scale import FakeHx711, Hx711, Scale
        try:
            hx = FakeHx711() if fake_io else Hx711(hw["pins"]["hx711_dt"], hw["pins"]["hx711_sck"])
            scale = Scale(hx, *((app["server_url"], app["api_key"], app["device_id"]) if uploader else ()))
            log.info("Cân HX711: DT GPIO%s, SCK GPIO%s", hw["pins"]["hx711_dt"], hw["pins"]["hx711_sck"])
        except SystemExit as e:          # cân là phụ: lỗi cân không được làm dừng thùng rác
            log.error("Không mở được cân, chạy tiếp không có cân: %s", e)

    ctl = Controller(app, hw, sensor, camera, clf, decider, servos, ind, uploader, ROOT, door)
    if args.show_distance:
        ctl.distance_log_s = hw["poll_ms"] / 1000
    if (args.keyboard or args.bench) and not args.mock:   # --mock đã dùng Enter để đặt/lấy vật
        threading.Thread(target=_keyboard_trigger, args=(ctl.manual,), daemon=True, name="keys").start()
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        ctl.run()
    except KeyboardInterrupt:
        log.info("Dừng.")
    finally:
        # servo đóng trước: về vị trí nghỉ rồi ngắt xung
        for x in (servos, streamer, scale, uploader, camera, ind, door, sensor):
            if x:
                try:
                    x.close()
                except Exception:       # một thiết bị lỗi khi đóng không được làm bỏ qua các thiết bị còn lại
                    log.exception("Lỗi khi đóng %s", type(x).__name__)


if __name__ == "__main__":
    main()
