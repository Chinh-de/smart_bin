"""Demo phân loại rác trên laptop: webcam hoặc ảnh có sẵn, đọc to hành động bằng tiếng Việt.

    python demo.py                         # webcam: SPACE = phân loại, A = tự động, M = tắt/bật tiếng, Q = thoát
    python demo.py --image anh.jpg         # 1 ảnh hoặc cả thư mục ảnh (phím bất kỳ = ảnh tiếp)
    python demo.py --image thu_muc --no-window   # chỉ in kết quả ra terminal

Dùng chung bộ phân loại + logic chọn thùng (config/bins.json) với ứng dụng trên Pi (../smartbin_pi).
"""
import argparse
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
PI_APP = HERE.parent / "smartbin_pi"
sys.path.insert(0, str(PI_APP))
from smartbin import load_json                  # noqa: E402
from smartbin.classifier import Classifier       # noqa: E402
from smartbin.decider import Decider             # noqa: E402

from speech import Speaker                       # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

VI_NAME = {
    "battery": "Pin", "biological": "Rác hữu cơ", "cardboard": "Bìa các tông", "clothes": "Quần áo",
    "glass": "Thủy tinh", "metal": "Kim loại", "paper": "Giấy", "plastic": "Nhựa",
    "shoes": "Giày dép", "trash": "Rác thải",
}
BIN_COLOR = {1: (37, 99, 235), 2: (22, 163, 74), 3: (107, 114, 128)}   # RGB
REJECT_COLOR = (220, 38, 38)


def action_text(d):
    """Câu đọc to cho một quyết định."""
    name = VI_NAME.get(d.top_class, d.top_class)
    if d.result == "REJECT":
        return f"Phát hiện {name.lower()}. {d.message}."
    where = f"Bỏ vào thùng {d.bin_name[0].lower()}{d.bin_name[1:]}."
    if d.uncertain:
        return f"Không chắc chắn. {where}"
    return f"{name}. {where}"


def all_phrases(decider, classes):
    """Mọi câu có thể đọc -> tạo sẵn giọng đọc lúc khởi động."""
    out = set()
    for i, _ in enumerate(classes):
        for p_top in (0.95, 0.5):                     # chắc chắn / không chắc
            p = np.full(len(classes), (1 - p_top) / (len(classes) - 1))
            p[i] = p_top
            out.add(action_text(decider.decide(p)))
    return sorted(out)


def load_font(size):
    for f in ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


class Overlay:
    """Vẽ chữ tiếng Việt lên khung hình (cv2.putText không có dấu)."""

    def __init__(self):
        self.f_big, self.f_small = load_font(30), load_font(20)

    def draw(self, frame_bgr, d=None, hint="", ms=None):
        h, w = frame_bgr.shape[:2]
        s = min(h, w)
        y0, x0 = (h - s) // 2, (w - s) // 2
        cv2.rectangle(frame_bgr, (x0, y0), (x0 + s - 1, y0 + s - 1), (255, 255, 255), 2)   # vùng model nhìn thấy

        img = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        dr = ImageDraw.Draw(img, "RGBA")
        if hint:
            dr.rectangle((0, 0, w, 34), fill=(0, 0, 0, 150))
            dr.text((10, 5), hint, font=self.f_small, fill="white")
        if d is not None:
            color = REJECT_COLOR if d.result == "REJECT" else BIN_COLOR.get(d.bin_id, (107, 114, 128))
            panel_h = 150
            dr.rectangle((0, h - panel_h, w, h), fill=(0, 0, 0, 170))
            title = "TỪ CHỐI" if d.result == "REJECT" else d.bin_name + (" (không chắc)" if d.uncertain else "")
            dr.rectangle((10, h - panel_h + 10, 22, h - panel_h + 46), fill=color)
            dr.text((32, h - panel_h + 8), title, font=self.f_big, fill="white")
            for k, (c, p) in enumerate(d.top3):
                y = h - panel_h + 56 + k * 30
                dr.text((14, y), f"{VI_NAME.get(c, c)} ({c})", font=self.f_small, fill="white")
                dr.rectangle((230, y + 6, 230 + int(p * (w - 330)), y + 22), fill=color)
                dr.text((240 + int(p * (w - 330)), y), f"{p * 100:.0f}%", font=self.f_small, fill="white")
            if ms is not None:
                dr.text((w - 110, h - panel_h + 14), f"{ms:.0f} ms", font=self.f_small, fill=(200, 200, 200))
        return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def show_result(d, ms, src=""):
    top3 = ", ".join(f"{c} {p:.2f}" for c, p in d.top3)
    print(f"{src:28s} {top3:45s} -> {action_text(d)}  ({ms:.0f} ms)")


def run_images(paths, clf, decider, speaker, overlay, window):
    for p in paths:
        img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"{p.name}: không đọc được")
            continue
        probs, ms = clf.predict([img])
        d = decider.decide(probs)
        show_result(d, ms, p.name)
        if window:
            scale = 640 / max(img.shape[:2])
            view = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img.copy()
            cv2.imshow("Smartbin demo", overlay.draw(view, d, f"{p.name}  ·  phím bất kỳ: ảnh tiếp, Q: thoát", ms))
            speaker.say(action_text(d))
            if cv2.waitKey(0) & 0xFF in (ord("q"), 27):
                break
        else:
            speaker.say(action_text(d), wait=True)


def run_webcam(index, clf, decider, speaker, overlay, n_frames, auto_interval):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY)
    if not cap.isOpened():
        sys.exit(f"Không mở được webcam {index}. Thử --camera 1")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    last, last_ms, last_t, auto = None, None, 0.0, False
    burst = []                                     # các khung đang gom cho 1 lần phân loại
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            frame = cv2.flip(frame, 1)             # như soi gương, dễ đặt vật vào khung

            if burst is not None and len(burst) > 0:
                burst.append(frame.copy())
                if len(burst) >= n_frames:         # đủ khung -> trung bình xác suất như trên Pi
                    probs, last_ms = clf.predict(burst)
                    last, last_t, burst = decider.decide(probs), time.time(), []
                    show_result(last, last_ms, "webcam")
                    speaker.say(action_text(last))

            hint = f"SPACE: phân loại · A: tự động {'BẬT' if auto else 'TẮT'} · " \
                   f"M: tiếng {'BẬT' if speaker.enabled else 'TẮT'} · Q: thoát"
            if burst:
                hint = "Đang phân loại..."
            showing = last if last is not None and time.time() - last_t < 4 else None
            cv2.imshow("Smartbin demo", overlay.draw(frame.copy(), showing, hint, last_ms))

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("m"):
                speaker.enabled = not speaker.enabled
            if key == ord("a"):
                auto = not auto
            want = key == ord(" ") or (auto and time.time() - last_t > auto_interval)
            if want and not burst:
                burst = [frame.copy()]
    finally:
        cap.release()
        cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", help="ảnh hoặc thư mục ảnh (bỏ trống = dùng webcam)")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--model", default=str(HERE.parent / "Model_training" / "models" / "model_int8.tflite"))
    ap.add_argument("--labels", help="mặc định: labels.json cùng thư mục với model")
    ap.add_argument("--bins", default=str(PI_APP / "config" / "bins.json"))
    ap.add_argument("--frames", type=int, default=5, help="số khung trung bình mỗi lần phân loại (webcam)")
    ap.add_argument("--auto-interval", type=float, default=3.0, help="giây giữa 2 lần phân loại ở chế độ tự động")
    ap.add_argument("--voice", default="vi-VN-HoaiMyNeural", help="hoặc vi-VN-NamMinhNeural (giọng nam)")
    ap.add_argument("--mute", action="store_true")
    ap.add_argument("--no-window", action="store_true", help="(--image) chỉ in ra terminal")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    model = Path(args.model)
    labels = Path(args.labels) if args.labels else model.with_name("labels.json")
    if not model.exists():
        sys.exit(f"Không thấy model: {model}")
    clf = Classifier(model, labels)
    decider = Decider(load_json(args.bins), clf.classes)
    print(f"Model: {model.name} ({load_json(labels).get('int8_mode', '')})")

    speaker = Speaker(HERE / "audio_cache", voice=args.voice, enabled=not args.mute)
    if not args.mute:
        speaker.prepare(all_phrases(decider, clf.classes))
    overlay = Overlay()

    if args.image:
        src = Path(args.image)
        paths = sorted(f for f in src.rglob("*") if f.suffix.lower() in IMG_EXT) if src.is_dir() else [src]
        run_images(paths, clf, decider, speaker, overlay, window=not args.no_window)
        if not args.no_window:
            cv2.destroyAllWindows()
    else:
        run_webcam(args.camera, clf, decider, speaker, overlay, args.frames, args.auto_interval)


if __name__ == "__main__":
    main()
