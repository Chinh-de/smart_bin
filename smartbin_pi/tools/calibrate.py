"""Hiệu chỉnh phần cứng trên Pi: góc servo từng thùng, góc lật khay, khoảng cách khay.

    python tools/calibrate.py
Lệnh:
    c <góc>   xoay servo máng (0..180)       vd: c 90
    t <góc>   xoay servo khay (0..180)       vd: t 100
    b <id>    xoay máng tới thùng id theo bins.json
    d         đo khoảng cách 10 lần (khay trống / có vật)
    l         bật/tắt đèn chiếu
    s         chụp 1 ảnh webcam -> calib.jpg
    q         thoát
Ghi các giá trị phù hợp vào config/hardware.json và config/bins.json."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import ROOT, load_config                     # noqa: E402
from smartbin.hardware import Actuators, UltrasonicSensor  # noqa: E402


def main():
    cfg = load_config()
    hw, bins = cfg["hw"], {b["id"]: b for b in cfg["bins"]["bins"]}
    act = Actuators(hw)
    sensor = UltrasonicSensor(hw["pins"]["trig"], hw["pins"]["echo"])
    light = False
    print(__doc__)
    try:
        while True:
            cmd = input("> ").strip().split()
            if not cmd:
                continue
            k, arg = cmd[0], (cmd[1] if len(cmd) > 1 else None)
            if k == "q":
                break
            elif k in ("c", "t") and arg:
                try:
                    angle = float(arg)
                except ValueError:
                    print("góc phải là số, vd: c 90")
                    continue
                (act.set_chute if k == "c" else act.set_tray)(angle)
                time.sleep(0.6)
                act.release_servos()
            elif k == "b" and arg and arg.isdigit() and int(arg) in bins:
                b = bins[int(arg)]
                act.set_chute(b["angle"])
                time.sleep(0.6)
                act.release_servos()
                print(f"{b['name']}: {b['angle']}°")
            elif k == "d":
                vals = []
                for _ in range(10):
                    vals.append(sensor.distance_cm())
                    time.sleep(0.1)
                print(f"khoảng cách: trung vị {sorted(vals)[5]:.1f} cm, min {min(vals):.1f}, max {max(vals):.1f}")
                print(f"  (hiện tại trigger_cm={hw['trigger_cm']}, clear_cm={hw['clear_cm']})")
            elif k == "l":
                light = not light
                act.light(light)
            elif k == "s":
                import cv2
                from smartbin.camera import UsbCamera
                app = cfg["app"]
                cam = UsbCamera(app["camera_index"], app["camera_width"], app["camera_height"])
                cv2.imwrite(str(ROOT / "calib.jpg"), cam.capture(1)[0])
                cam.close()
                print("đã lưu calib.jpg")
            else:
                print("lệnh không hợp lệ")
    finally:
        act.close()
        sensor.close()


if __name__ == "__main__":
    main()
