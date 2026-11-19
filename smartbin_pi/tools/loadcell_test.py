"""Kiểm tra load cell + mạch khuếch đại HX711: đọc giá trị thô, tare (về 0), hiệu chuẩn bằng vật đã biết khối lượng.

    python tools/loadcell_test.py          # trên Pi
    python tools/loadcell_test.py --mock   # chạy thử logic trên máy không có Pi

Lệnh:
    r         đọc liên tục (giá trị thô + gram), Ctrl+C để dừng
    t         tare: lấy trung vị 10 lần đọc lúc cân trống làm điểm 0
    c <gram>  hiệu chuẩn: đặt vật nặng đúng <gram> lên cân (đã tare) rồi gõ lệnh   vd: c 500
    w         đọc khối lượng 1 lần (gram)
    n         đo độ nhiễu: 30 lần đọc lúc cân đứng yên (min/max/độ lệch chuẩn)
    s         in cấu hình hiện tại
    q         thoát
(--mock có thêm: m <gram> đặt khối lượng giả lập)

Cách nối dây (HX711 <-> Raspberry Pi), đánh số chân theo header 40 chân:

    HX711 VCC  -> 3V3   (chân 1)   *** dùng 3.3 V, không dùng 5 V: để DT ra mức 3.3 V an toàn cho GPIO ***
    HX711 GND  -> GND   (chân 6)
    HX711 DT   -> GPIO20 (chân 38)     (DOUT, dữ liệu từ HX711 về Pi)
    HX711 SCK  -> GPIO21 (chân 40)     (xung clock từ Pi sang HX711)

Nối load cell (loại thanh 4 dây) vào phía còn lại của HX711:

    Dây đỏ   (E+, nguồn cấp +)   -> E+
    Dây đen  (E-, nguồn cấp -)   -> E-
    Dây trắng (tín hiệu -)       -> A-
    Dây xanh lá (tín hiệu +)     -> A+
    (B+ / B- bỏ trống. Màu dây các hãng có thể khác: nếu số đọc ra âm khi đặt vật thì đảo A+ <-> A-.)

Lưu ý lắp cơ khí: load cell dạng thanh có mũi tên chỉ chiều lực; một đầu bắt cố định vào khung,
đầu kia gắn mặt cân, lực đặt theo chiều mũi tên. Chạy trên 3.3 V thì độ phân giải thấp hơn 5 V
một chút nhưng đủ để thử.

Chân lấy từ config/hardware.json (hx711_dt, hx711_sck). Điểm 0 và hệ số hiệu chuẩn lưu ở
config/loadcell.json (tự tạo khi gõ t / c).

Đọc HX711 bằng lgpio (cùng thư viện gpiozero dùng ở bên dưới): bit-bang 24 xung. Xung SCK ở mức cao
> 60 µs thì HX711 tự tắt nguồn nên thỉnh thoảng có mẫu hỏng; script lấy trung vị nhiều mẫu để loại chúng."""
import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import ROOT, load_json  # noqa: E402

from smartbin.scale import (CALIB_PATH, SAT_MAX, SAT_MIN, FakeHx711, Hx711,  # noqa: E402
                            load_calib, sample, to_grams)

SAMPLES_DEFAULT = 5


def save_calib(calib):
    with open(ROOT / CALIB_PATH, "w", encoding="utf-8") as f:
        json.dump(calib, f, indent=2)
        f.write("\n")


def show_config(calib):
    if calib["calibrated"]:
        print(f"offset={calib['offset']:.0f} scale={calib['scale']:.3f} (số đếm / gram)")
    else:
        print(f"chưa hiệu chuẩn (offset={calib['offset']:.0f}): gram hiện chỉ là số đếm thô, gõ t rồi c <gram>")


def raw_loop(sensor, interval):
    """In giá trị thô (không lọc) mỗi `interval` giây để kiểm tra dây nối.

    Vẫn đọc liên tục mọi mẫu giữa hai lần in, nên min/max/số mẫu hỏng tính trên tất cả mẫu,
    còn dòng in ra là mẫu mới nhất (không phải mẫu cũ). interval = 0: in mọi mẫu."""
    print(f"Đọc thô, in mỗi {interval:g} s, Ctrl+C để dừng. Cách kiểm tra:")
    print("  1. Cân trống: số phải ổn định (dao động vài chục..vài trăm đơn vị là bình thường).")
    print("  2. Ấn nhẹ / đặt vật lên load cell: số phải đổi rõ (hàng nghìn trở lên).")
    print("  3. Đổi chiều lực mà số đổi ngược dấu là bình thường; ấn vào mà số giảm thì đảo A+ <-> A- nếu muốn tăng.")
    print("  Số luôn -1 / 8388607 / -8388608, hoặc báo timeout: dây sai/lỏng, xem hướng dẫn nối ở đầu file.\n")
    first = lo = hi = last = None
    n = bad = bad_since = 0
    next_print = time.monotonic()
    try:
        while True:
            try:
                raw = sensor.read_raw()
            except TimeoutError as e:
                print(f"lỗi: {e}")
                time.sleep(0.5)
                continue
            n += 1
            if raw in (-1, SAT_MIN, SAT_MAX):
                bad += 1
                bad_since += 1
            else:
                first = raw if first is None else first
                lo = raw if lo is None else min(lo, raw)
                hi = raw if hi is None else max(hi, raw)
                last = raw
            if time.monotonic() < next_print:
                continue
            next_print = time.monotonic() + interval
            if last is None:
                print(f"raw=    (chưa có mẫu hợp lệ)   mẫu hỏng/bão hòa: {bad_since}")
            else:
                note = f"   mẫu hỏng: {bad_since}" if bad_since else ""
                print(f"raw={last:>10}   so với lúc đầu {last - first:>+9}   min {lo}  max {hi}{note}")
            bad_since = 0
    except KeyboardInterrupt:
        print(f"\n{n} mẫu, {bad} mẫu hỏng" + (f", dao động {hi - lo}" if lo is not None else ""))


def main():
    ap = argparse.ArgumentParser(description="Test load cell + HX711")
    ap.add_argument("--mock", action="store_true", help="không dùng GPIO thật")
    ap.add_argument("--chip", type=int, help="gpiochip (mặc định: tự nhận, Pi 5 = 4, còn lại 0)")
    ap.add_argument("--raw", action="store_true",
                    help="chỉ in giá trị thô liên tục (không lọc, không cần tare/hiệu chuẩn), Ctrl+C để dừng")
    ap.add_argument("--interval", type=float, default=0.5,
                    help="với --raw: số giây giữa hai dòng in (mặc định 0.5, 0 = in mọi mẫu)")
    args = ap.parse_args()
    if not math.isfinite(args.interval) or args.interval < 0:
        raise SystemExit("--interval phải >= 0")

    pins = load_json("config/hardware.json")["pins"]
    try:
        dt, sck = pins["hx711_dt"], pins["hx711_sck"]
    except KeyError as e:
        raise SystemExit(f"config/hardware.json thiếu pins.{e.args[0]}")
    sensor = FakeHx711() if args.mock else Hx711(dt, sck, args.chip)
    if args.raw:
        try:
            raw_loop(sensor, args.interval)
        finally:
            sensor.close()
        return
    calib = load_calib()

    print(__doc__)
    print(f"DT=GPIO{dt}, SCK=GPIO{sck}" + (" (mock)" if args.mock else ""))
    show_config(calib)
    try:
        while True:
            try:
                cmd = input("> ").strip().split()
            except EOFError:
                break
            if not cmd:
                continue
            k, arg = cmd[0].lower(), (cmd[1] if len(cmd) > 1 else None)
            try:
                if k == "q":
                    break
                elif k == "r":
                    print("đọc liên tục, Ctrl+C để dừng")
                    try:
                        while True:
                            raw = sample(sensor, 3)
                            print(f"raw={raw:>10.0f}   {to_grams(raw, calib):>9.1f} g", end="\r", flush=True)
                    except KeyboardInterrupt:
                        print()
                elif k == "t":
                    print("tare: giữ cân trống và đứng yên...")
                    calib["offset"] = sample(sensor, 10)
                    save_calib(calib)
                    print(f"offset = {calib['offset']:.0f} (đã lưu {CALIB_PATH})")
                elif k == "c" and arg is not None:
                    try:
                        grams = float(arg)
                    except ValueError:
                        print("khối lượng phải là số gram, vd: c 500")
                        continue
                    if not math.isfinite(grams) or grams <= 0:
                        print("khối lượng phải > 0")
                        continue
                    raw = sample(sensor, 10)
                    delta = raw - calib["offset"]
                    if abs(delta) < 200:
                        print(f"chênh so với điểm 0 chỉ {delta:.0f}: chưa đặt vật lên cân, hoặc chưa tare (t), "
                              f"hoặc load cell chưa nối đúng")
                        continue
                    calib.update(scale=delta / grams, calibrated=True)
                    save_calib(calib)
                    print(f"scale = {calib['scale']:.3f} số đếm/gram (đã lưu {CALIB_PATH})")
                    if calib["scale"] < 0:
                        print("scale âm: nên đảo dây A+ <-> A- cho số đọc dương (vẫn dùng được, kết quả đúng dấu)")
                    print(f"kiểm tra: đọc lại = {to_grams(raw, calib):.1f} g")
                elif k == "w":
                    raw = sample(sensor, 10)
                    print(f"raw={raw:.0f}  {to_grams(raw, calib):.1f} g" +
                          ("" if calib["calibrated"] else "  (chưa hiệu chuẩn)"))
                elif k == "n":
                    print("đo 30 mẫu, giữ cân đứng yên...")
                    vals = [sample(sensor, 1) for _ in range(30)]
                    sd = statistics.pstdev(vals)
                    print(f"raw: min {min(vals):.0f}, max {max(vals):.0f}, độ lệch chuẩn {sd:.1f}")
                    if calib["calibrated"]:
                        print(f"≈ {sd / abs(calib['scale']):.2f} g (độ lệch chuẩn), dao động {(max(vals) - min(vals)) / abs(calib['scale']):.2f} g")
                elif k == "s":
                    show_config(calib)
                elif k == "m" and arg is not None and args.mock:
                    try:
                        sensor.grams = float(arg)
                    except ValueError:
                        print("khối lượng phải là số")
                        continue
                    print(f"mock: khối lượng giả lập = {sensor.grams:g} g")
                else:
                    print("lệnh không hợp lệ")
            except (TimeoutError, RuntimeError) as e:
                print(f"lỗi đọc HX711: {e}")
    except KeyboardInterrupt:
        print()
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
