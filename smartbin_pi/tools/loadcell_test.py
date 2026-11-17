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
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import ROOT, load_json  # noqa: E402

CALIB_PATH = "config/loadcell.json"
GAIN_PULSES = 1          # 1 xung thêm = kênh A, gain 128 (2 = kênh B gain 32, 3 = kênh A gain 64)
READY_TIMEOUT_S = 1.0    # HX711 mặc định 10 mẫu/giây, đợi tối đa 1 s cho 1 mẫu
SAMPLES_DEFAULT = 5
SAT_MIN, SAT_MAX = -0x800000, 0x7FFFFF
DEFAULT_CALIB = {"offset": 0.0, "scale": 1.0, "calibrated": False}


class FakeHx711:
    """Giả lập cho --mock: raw = nền + hệ số * khối lượng + nhiễu."""

    COUNTS_PER_GRAM = 420.0

    def __init__(self):
        self.grams = 0.0
        self.base = 84000

    def read_raw(self):
        time.sleep(0.01)
        return round(self.base + self.COUNTS_PER_GRAM * self.grams + random.gauss(0, 60))

    def close(self):
        pass


class Hx711:
    """HX711 qua lgpio, bit-bang."""

    def __init__(self, dt, sck, chip=None):
        try:
            import lgpio
        except ImportError:
            raise SystemExit("thiếu lgpio: sudo apt install -y python3-lgpio "
                             "(venv phải tạo bằng --system-site-packages, xem README)")
        self.lgpio = lgpio
        self.dt, self.sck = dt, sck
        if chip is None:
            chip = 4 if "Raspberry Pi 5" in self._model() else 0
        try:
            self.h = lgpio.gpiochip_open(chip)
            lgpio.gpio_claim_output(self.h, sck, 0)
            lgpio.gpio_claim_input(self.h, dt)
        except lgpio.error as e:
            raise SystemExit(f"không mở được GPIO (chip {chip}, DT=GPIO{dt}, SCK=GPIO{sck}): {e}\n"
                             f"Chân đang bị chương trình khác giữ? (main.py/calibrate.py/servo_test.py) "
                             f"Pi 5 thử --chip 4, Pi cũ --chip 0.")
        lgpio.gpio_write(self.h, sck, 0)  # SCK thấp = HX711 hoạt động (cao > 60 µs = tắt)

    @staticmethod
    def _model():
        try:
            return Path("/proc/device-tree/model").read_bytes().decode(errors="replace")
        except OSError:
            return ""

    def _wait_ready(self):
        """DT xuống thấp = có mẫu mới."""
        lg, deadline = self.lgpio, time.monotonic() + READY_TIMEOUT_S
        while lg.gpio_read(self.h, self.dt):
            if time.monotonic() > deadline:
                raise TimeoutError("DT luôn ở mức cao: kiểm tra dây VCC/GND/DT/SCK của HX711")
            time.sleep(0.001)

    def read_raw(self):
        """1 mẫu 24 bit có dấu."""
        lg, h, sck, dt = self.lgpio, self.h, self.sck, self.dt
        self._wait_ready()
        value = 0
        for _ in range(24):
            lg.gpio_write(h, sck, 1)
            lg.gpio_write(h, sck, 0)
            value = (value << 1) | lg.gpio_read(h, dt)
        for _ in range(GAIN_PULSES):  # chọn kênh/gain cho lần đọc kế tiếp
            lg.gpio_write(h, sck, 1)
            lg.gpio_write(h, sck, 0)
        return value - (1 << 24) if value & 0x800000 else value

    def close(self):
        try:
            self.lgpio.gpio_write(self.h, self.sck, 1)  # SCK cao > 60 µs: đưa HX711 vào chế độ tiết kiệm điện
            time.sleep(0.0002)
            self.lgpio.gpiochip_close(self.h)
        except Exception:
            pass


def sample(sensor, n=SAMPLES_DEFAULT):
    """Trung vị n mẫu hợp lệ (bỏ mẫu bão hòa / 0xFFFFFF do xung SCK bị kéo dài)."""
    vals = []
    for _ in range(n * 3):
        raw = sensor.read_raw()
        if raw in (-1, SAT_MIN, SAT_MAX):  # toàn bit 1, hoặc chạm trần
            continue
        vals.append(raw)
        if len(vals) == n:
            break
    if not vals:
        raise RuntimeError("toàn mẫu bão hòa/-1: HX711 chưa nối load cell, dây E+/E-/A+/A- lỏng hoặc nối nhầm")
    return statistics.median(vals)


def load_calib():
    try:
        return {**DEFAULT_CALIB, **load_json(CALIB_PATH)}
    except FileNotFoundError:
        return dict(DEFAULT_CALIB)


def save_calib(calib):
    with open(ROOT / CALIB_PATH, "w", encoding="utf-8") as f:
        json.dump(calib, f, indent=2)
        f.write("\n")


def to_grams(raw, calib):
    return (raw - calib["offset"]) / calib["scale"]


def show_config(calib):
    if calib["calibrated"]:
        print(f"offset={calib['offset']:.0f} scale={calib['scale']:.3f} (số đếm / gram)")
    else:
        print(f"chưa hiệu chuẩn (offset={calib['offset']:.0f}): gram hiện chỉ là số đếm thô, gõ t rồi c <gram>")


def main():
    ap = argparse.ArgumentParser(description="Test load cell + HX711")
    ap.add_argument("--mock", action="store_true", help="không dùng GPIO thật")
    ap.add_argument("--chip", type=int, help="gpiochip (mặc định: tự nhận, Pi 5 = 4, còn lại 0)")
    args = ap.parse_args()

    pins = load_json("config/hardware.json")["pins"]
    try:
        dt, sck = pins["hx711_dt"], pins["hx711_sck"]
    except KeyError as e:
        raise SystemExit(f"config/hardware.json thiếu pins.{e.args[0]}")
    sensor = FakeHx711() if args.mock else Hx711(dt, sck, args.chip)
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
