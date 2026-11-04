"""Thùng rác phân loại thông minh — chương trình chính trên Raspberry Pi.

    python main.py                      # chạy thật (webcam USB + GPIO)
    python main.py --mock --images DIR  # giả lập trên laptop: Enter = đặt/lấy vật, ảnh lấy từ DIR
"""
import argparse
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

from smartbin import ROOT, load_config, load_json
from smartbin.controller import Controller
from smartbin.decider import Decider
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
    raise KeyboardInterrupt        # systemctl stop gửi SIGTERM: đi qua khối finally để tắt đèn, nhả servo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="giả lập cảm biến/servo/camera")
    ap.add_argument("--images", help="(mock) thư mục ảnh thay cho camera")
    ap.add_argument("--no-upload", action="store_true", help="không gửi lên web server")
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

    if args.mock:
        from smartbin.mock import MockActuators, MockCamera, MockSensor
        sensor = MockSensor(hw["trigger_cm"])
        camera = MockCamera(args.images)
        act = MockActuators(sensor)
        hw = {**hw, **{k: min(v, 300) for k, v in hw.items() if k.endswith("_ms")}}   # chạy nhanh hơn khi giả lập
    else:
        from smartbin.camera import UsbCamera
        from smartbin.hardware import Actuators, UltrasonicSensor
        sensor = UltrasonicSensor(hw["pins"]["trig"], hw["pins"]["echo"])
        camera = UsbCamera(app["camera_index"], app["camera_width"], app["camera_height"])
        act = Actuators(hw)

    uploader = None if args.no_upload else \
        Uploader(app["server_url"], app["api_key"], app["device_id"], ROOT / "queue", app["heartbeat_s"])

    ctl = Controller(app, hw, sensor, camera, clf, decider, act, uploader, ROOT)
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        ctl.run()
    except KeyboardInterrupt:
        log.info("Dừng.")
    finally:
        for x in (uploader, camera, act, sensor):
            if x:
                try:
                    x.close()
                except Exception:       # một thiết bị lỗi khi đóng không được làm bỏ qua các thiết bị còn lại
                    log.exception("Lỗi khi đóng %s", type(x).__name__)


if __name__ == "__main__":
    main()
