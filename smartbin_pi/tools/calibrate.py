"""Hiệu chỉnh buồng chụp trên Pi: khoảng cách siêu âm, đèn, khung hình webcam, công tắc cửa.
(Góc servo chỉnh bằng tools/servo_test.py.)

    python tools/calibrate.py
Lệnh:
    d         đo khoảng cách 10 lần (khay trống / có vật)
    l         bật/tắt đèn buồng chụp
    s         chụp 1 ảnh webcam (bật đèn) -> calib.jpg, vẽ ô vuông giữa là vùng model nhìn thấy
    c         đọc công tắc cửa (nếu pins.door khác null)
    q         thoát
Ghi trigger_cm / clear_cm vào config/hardware.json."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import ROOT, load_config                                  # noqa: E402
from smartbin.hardware import DoorSwitch, Indicators, UltrasonicSensor  # noqa: E402


def main():
    cfg = load_config()
    hw, app = cfg["hw"], cfg["app"]
    ind = Indicators(hw["pins"])
    sensor = UltrasonicSensor(hw["pins"]["trig"], hw["pins"]["echo"])
    door = DoorSwitch(hw["pins"]["door"]) if hw["pins"].get("door") is not None else None
    light = False
    print(__doc__)
    try:
        while True:
            cmd = input("> ").strip().split()
            if not cmd:
                continue
            k = cmd[0]
            if k == "q":
                break
            elif k == "d":
                vals = []
                for _ in range(10):
                    vals.append(sensor.distance_cm())
                    time.sleep(0.1)
                print(f"khoảng cách: trung vị {sorted(vals)[5]:.1f} cm, min {min(vals):.1f}, max {max(vals):.1f}")
                print(f"  (hiện tại trigger_cm={hw['trigger_cm']}, clear_cm={hw['clear_cm']})")
            elif k == "l":
                light = not light
                ind.light(light)
            elif k == "s":
                import cv2
                from smartbin.camera import UsbCamera
                cam = UsbCamera(app["camera_index"], app["camera_width"], app["camera_height"])
                ind.light(True)
                time.sleep(hw["light_warmup_ms"] / 1000)
                img = cam.capture(1)[0]
                ind.light(light)
                cam.close()
                h, w = img.shape[:2]
                s = min(h, w)
                x, y = (w - s) // 2, (h - s) // 2
                cv2.rectangle(img, (x, y), (x + s - 1, y + s - 1), (0, 0, 255), 2)
                cv2.imwrite(str(ROOT / "calib.jpg"), img)
                print("đã lưu calib.jpg (khay phải nằm trong ô đỏ)")
            elif k == "c":
                print("chưa cấu hình pins.door" if door is None else
                      f"cửa: {'MỞ' if door.is_open() else 'đóng'}")
            else:
                print("lệnh không hợp lệ")
    finally:
        for x in (ind, sensor, door):
            if x:
                x.close()


if __name__ == "__main__":
    main()
