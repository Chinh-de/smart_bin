"""Kiểm tra riêng 2 servo: A ngang (pan, chọn bên) + B dọc (tilt, nghiêng đổ rác vào thùng).

    python tools/servo_test.py            # trên Pi
    python tools/servo_test.py --mock     # chạy thử logic trên máy không có Pi
    python tools/servo_test.py --speed 30 # tốc độ đỉnh 30 độ/giây (chậm hơn)

Lệnh:
    1..4      đưa rác vào thùng đó rồi về vị trí nghỉ
    h         về vị trí nghỉ
    p <góc>   xoay servo ngang tới góc (0..180)      vd: p 45
    v <góc>   xoay servo dọc tới góc (0..180)        vd: v 128
    d <độ>    đổi biên độ nghiêng dọc (tilt_delta)   vd: d 40
    s         in cấu hình hiện tại
    q         về vị trí nghỉ rồi thoát

Dùng đúng cấu hình của main.py: chân + góc nghỉ + tốc độ ở config/hardware.json (pins.servo_pan/servo_tilt,
mục "servo"), góc từng thùng ở config/bins.json (pan, tilt_dir). Tìm được giá trị tốt thì ghi lại vào đó.
Không chạy cùng lúc với main.py. Cần bật PWM phần cứng, xem smartbin/servo.py."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import load_config  # noqa: E402
from smartbin.servo import MAX_ANGLE, MIN_ANGLE, PanTilt, tilt_angle, validate  # noqa: E402


def read_command():
    """Đọc 1 dòng lệnh; phím gõ trong lúc servo đang chạy bị bỏ, không dồn thành chu trình tiếp theo."""
    if sys.stdin.isatty():
        try:
            import termios
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except (ImportError, OSError, ValueError):
            pass
    return input("> ").strip().split()


def parse_angle(arg):
    try:
        angle = float(arg)
    except (TypeError, ValueError):
        print("góc phải là số, vd: p 45")
        return None
    if not MIN_ANGLE <= angle <= MAX_ANGLE:
        print(f"góc phải trong {MIN_ANGLE}..{MAX_ANGLE}")
        return None
    return angle


def main():
    ap = argparse.ArgumentParser(description="Test servo ngang + dọc")
    ap.add_argument("--mock", action="store_true", help="không dùng PWM thật")
    ap.add_argument("--speed", type=float, help="ghi đè speed_dps (độ/giây)")
    args = ap.parse_args()

    cfg = load_config()
    hw, bin_list = cfg["hw"], cfg["bins"]["bins"]
    sc = hw["servo"]
    if args.speed is not None:
        sc["speed_dps"] = args.speed
    validate(sc, bin_list)
    bins = {b["id"]: b for b in bin_list}

    pt = PanTilt(hw["pins"], sc, mock=args.mock)
    print(__doc__)
    print("Chờ servo chạy xong rồi mới gõ lệnh tiếp: phím gõ lúc servo đang chạy sẽ bị bỏ.")
    print(f"Tốc độ {sc['speed_dps']:g}°/s. Lần chạy đầu servo sẽ nhảy thẳng về vị trí nghỉ "
          f"(ngang {sc['pan_rest']}°, dọc {sc['tilt_rest']}°) - giữ tay/đồ ra khỏi khay.")
    try:
        pt.startup()
        while True:
            try:
                cmd = read_command()
            except EOFError:
                break
            if not cmd:
                continue
            k, arg = cmd[0].lower(), (cmd[1] if len(cmd) > 1 else None)
            if k == "q":
                break
            elif k.isascii() and k.isdigit() and int(k) in bins:
                b = bins[int(k)]
                print(f"thùng {b['id']} {b['name']}: ngang {b['pan']}° -> dọc "
                      f"{tilt_angle(sc, b['tilt_dir']):g}° -> về nghỉ")
                pt.dump(b["pan"], b["tilt_dir"])
            elif k == "h":
                pt.home()
            elif k in ("p", "v") and arg is not None:
                angle = parse_angle(arg)
                if angle is not None:
                    if k == "p" and not pt.at_rest:
                        pt.move("tilt", sc["tilt_rest"])  # không xoay ngang khi khay đang nghiêng
                    pt.move("pan" if k == "p" else "tilt", angle)
                    time.sleep(0.3)
                    # đang nghiêng thì giữ xung, tránh khay tụt; về nghỉ (h/q) mới ngắt
                    if pt.at_rest:
                        pt.release()
            elif k == "d" and arg is not None:
                try:
                    delta = float(arg)
                except ValueError:
                    print("độ phải là số, vd: d 40")
                    continue
                old = sc["tilt_delta"]
                sc["tilt_delta"] = delta
                try:
                    validate(sc, bin_list)
                except SystemExit as e:
                    sc["tilt_delta"] = old
                    print(e)
                    continue
                print(f"tilt_delta = {delta:g}° (ghi vào config/hardware.json -> servo nếu muốn giữ)")
            elif k == "s":
                print(f"pan_rest={sc['pan_rest']} tilt_rest={sc['tilt_rest']} tilt_delta={sc['tilt_delta']:g} "
                      f"tilt_sign={sc['tilt_sign']} speed_dps={sc['speed_dps']:g}")
                for b in bin_list:
                    print(f"  thùng {b['id']} {b['name']}: ngang {b['pan']}°, "
                          f"dọc {tilt_angle(sc, b['tilt_dir']):g}°")
            else:
                print("lệnh không hợp lệ")
    except KeyboardInterrupt:
        print("\nngắt, về vị trí nghỉ...")
    finally:
        pt.close()   # về nghỉ (nếu đã khởi động) rồi ngắt xung


if __name__ == "__main__":
    main()
