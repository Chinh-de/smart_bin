"""Kiểm tra riêng 2 servo: ngang (pan, chọn bên) + dọc (tilt, nghiêng đổ rác vào thùng).
Dùng đúng code điều khiển của main.py (smartbin/servo.py, quay mượt kiểu ServoEaser).

    python tools/servo_test.py                          # trên Pi
    python tools/servo_test.py --mock                   # chạy thử logic trên máy không có Pi
    python tools/servo_test.py --pan-ms 800 --tilt-ms 600 --easing sine

Lệnh:
    1..4        đưa rác vào thùng đó rồi về vị trí nghỉ
    h           về vị trí nghỉ
    p <góc>     xoay servo ngang tới góc (0..180)          vd: p 45
    v <góc>     xoay servo dọc tới góc (0..180)            vd: v 128
    d <độ>      đổi biên độ nghiêng dọc (tilt_delta)       vd: d 40
    t <ms> <ms> đổi thời gian quay ngang / dọc             vd: t 600 500
    e <kiểu>    đổi easing: linear quad cubic sine         vd: e sine
    s           in cấu hình hiện tại
    q           về vị trí nghỉ rồi thoát

Cấu hình ở config/hardware.json -> "servo", góc từng thùng ở config/bins.json, cộng thông số đã chỉnh trên web
(config/local.json). Thay đổi bằng lệnh ở đây KHÔNG được lưu; muốn lưu thì chỉnh ở trang web /config hoặc sửa
file. Không chạy cùng lúc với main.py."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import load_config  # noqa: E402
from smartbin.servo import EASINGS, MAX_ANGLE, MIN_ANGLE, STARTUP_WAIT_S, PanTilt, tilt_angle, validate  # noqa: E402


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


def try_set(cfg, bins, **changes):
    """Đổi cấu hình, sai thì trả lại như cũ."""
    old = {k: cfg[k] for k in changes}
    cfg.update(changes)
    try:
        validate(cfg, bins)
    except SystemExit as e:
        cfg.update(old)
        print(e)
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Test servo ngang + dọc")
    ap.add_argument("--mock", action="store_true", help="không dùng PWM thật")
    ap.add_argument("--pan-ms", type=float, help="ghi đè pan_ms (thời gian xoay ngang)")
    ap.add_argument("--tilt-ms", type=float, help="ghi đè tilt_ms (thời gian lật dọc)")
    ap.add_argument("--easing", choices=list(EASINGS), help="ghi đè easing")
    args = ap.parse_args()

    conf = load_config()
    hw = conf["hw"]
    cfg, bins = hw["servo"], conf["bins"]["bins"]
    for k, v in (("pan_ms", args.pan_ms), ("tilt_ms", args.tilt_ms), ("easing", args.easing)):
        if v is not None:
            cfg[k] = v
    validate(cfg, bins)
    by_id = {b["id"]: b for b in bins}

    pt = PanTilt(hw["pins"], cfg, mock=args.mock)
    print(__doc__)
    print(f"easing {cfg['easing']}, ngang {cfg['pan_ms']:g} ms, dọc {cfg['tilt_ms']:g} ms. Lần chạy đầu servo sẽ "
          f"nhảy thẳng về vị trí nghỉ (ngang {cfg['pan_rest']}°, dọc {cfg['tilt_rest']}°) - giữ tay/đồ ra khỏi máng.")
    try:
        pt.snap("tilt", cfg["tilt_rest"])
        pt.snap("pan", cfg["pan_rest"])
        time.sleep(STARTUP_WAIT_S)  # chưa biết servo đang ở đâu: chờ đủ thời gian quay tối đa rồi mới ngắt xung
        pt.release()
        while True:
            try:
                cmd = input("> ").strip().split()
            except EOFError:
                break
            if not cmd:
                continue
            k, args_ = cmd[0].lower(), cmd[1:]
            arg = args_[0] if args_ else None
            if k == "q":
                break
            elif k.isascii() and k.isdigit() and int(k) in by_id:
                b = by_id[int(k)]
                t0 = time.monotonic()
                print(f"thùng {b['id']}: ngang {b['pan']}° -> dọc {tilt_angle(cfg, b['tilt_dir']):g}° -> về nghỉ")
                pt.dump(b["pan"], b["tilt_dir"])
                print(f"xong ({time.monotonic() - t0:.2f} s)")
            elif k == "h":
                pt.home()
            elif k in ("p", "v") and arg is not None:
                angle = parse_angle(arg)
                if angle is not None:
                    if k == "p" and not pt.at_rest:
                        pt.move("tilt", cfg["tilt_rest"])  # không xoay ngang khi khay đang nghiêng
                    pt.move("pan" if k == "p" else "tilt", angle)
                    time.sleep(0.3)
                    if pt.at_rest:   # đang nghiêng thì giữ xung, tránh khay tụt; về nghỉ (h/q) mới ngắt
                        pt.release()
            elif k == "d" and arg is not None:
                try:
                    if try_set(cfg, bins, tilt_delta=float(arg)):
                        print(f"tilt_delta = {cfg['tilt_delta']:g}°")
                except ValueError:
                    print("độ phải là số, vd: d 40")
            elif k == "t" and len(args_) == 2:
                try:
                    if try_set(cfg, bins, pan_ms=float(args_[0]), tilt_ms=float(args_[1])):
                        print(f"ngang {cfg['pan_ms']:g} ms, dọc {cfg['tilt_ms']:g} ms")
                except ValueError:
                    print("dùng: t 600 500")
            elif k == "e" and arg is not None:
                if arg in EASINGS:
                    cfg["easing"] = arg
                    print(f"easing = {arg}")
                else:
                    print("chọn: " + " ".join(EASINGS))
            elif k == "s":
                print(f"easing={cfg['easing']} pan_ms={cfg['pan_ms']:g} tilt_ms={cfg['tilt_ms']:g} "
                      f"update_hz={cfg['update_hz']} tilt_delta={cfg['tilt_delta']:g} tilt_sign={cfg['tilt_sign']}")
                for b in bins:
                    print(f"  thùng {b['id']}: ngang {b['pan']}°, dọc {tilt_angle(cfg, b['tilt_dir']):g}°")
            else:
                print("lệnh không hợp lệ")
    except KeyboardInterrupt:
        print("\nngắt, về vị trí nghỉ...")
    finally:
        pt.close()       # về nghỉ (nếu đã snap xong) rồi ngắt xung


if __name__ == "__main__":
    main()
