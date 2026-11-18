"""Thử các cách cho servo quay chậm, mượt. Mỗi cách là một file trong thư mục này.

    python tools/servo_lab/run.py                     # liệt kê các cách
    python tools/servo_lab/run.py <cách> [chân ...]   # chạy một cách, mặc định 1 servo ở GPIO12
    python tools/servo_lab/run.py <cách> -h           # xem hướng dẫn và lệnh của cách đó

Ví dụ:
    python tools/servo_lab/run.py vspeed 12 13
    python tools/servo_lab/run.py easing 12 13
"""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

METHODS = {
    "smooth":   "bioerrorlog: góc theo hàm sin/cos, nhịp 10 ms, chỉnh bằng bước pha (s)",
    "easing":   "ServoEasing: tốc độ °/s, nhịp 20 ms tính theo thời gian, 9 kiểu easing + precision",
    "varspeed": "VarSpeedServo: mỗi 20 ms nhích speed × 0,5 µs, đều, speed 1..255",
    "easer":    "ServoEaser: tới góc trong số ms cho trước, move list, lệnh 'bins' chạy thử 4 thùng",
    "vspeed":   "VarSpeedPython: N bước đều theo thời gian, làm tròn 1° để tránh vùng chết",
    "piservo":  "piServoCtl: PWM phần cứng qua pigpio + quay mượt (cần cài pigpio, piServoCtl)",
}


def usage():
    print(__doc__)
    print("Các cách:")
    for name, desc in METHODS.items():
        print(f"  {name:<9} {desc}")


if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    usage()
    sys.exit(0)

name = sys.argv[1]
if name not in METHODS:
    print(f"Không có cách '{name}'.\n")
    usage()
    sys.exit(1)

FILES = {"piservo": "piservoctl_hw"}   # không đặt file trùng tên thư viện piservo, kẻo import nhầm
path = HERE / f"{FILES.get(name, name)}.py"
args = sys.argv[2:]
if args and args[0] in ("-h", "--help"):
    print(path.read_text(encoding="utf-8").split('"""')[1])      # docstring đầu file
    sys.exit(0)
for a in args:
    if not a.isdigit():
        sys.exit(f"Chân GPIO phải là số, vd: python tools/servo_lab/run.py {name} 12 13")

sys.argv = [str(path)] + args          # file con đọc chân từ sys.argv[1:] như khi chạy trực tiếp
runpy.run_path(str(path), run_name="__main__")
