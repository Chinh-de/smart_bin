"""Phân loại ảnh có sẵn, không cần camera/GPIO. Dùng để kiểm tra model trên Pi.

    python tools/test_image.py anh1.jpg anh2.jpg ...
    python tools/test_image.py thu_muc_anh/
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartbin import ROOT, load_config            # noqa: E402
from smartbin.classifier import Classifier        # noqa: E402
from smartbin.decider import Decider              # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cfg = load_config()
    app = cfg["app"]
    clf = Classifier(ROOT / app["model"], ROOT / app["labels"], app["num_threads"])
    dec = Decider(cfg["bins"], clf.classes)

    paths = []
    for a in sys.argv[1:]:
        p = Path(a)
        paths += sorted(f for f in p.rglob("*") if f.suffix.lower() in IMG_EXT) if p.is_dir() else [p]

    times = []
    for p in paths:
        img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"{p.name}: không đọc được")
            continue
        probs, ms = clf.predict([img])
        times.append(ms)
        d = dec.decide(probs)
        target = f"TỪ CHỐI ({d.message})" if d.result == "REJECT" else \
            f"{d.bin_name}{' [không chắc]' if d.uncertain else ''}"
        top3 = ", ".join(f"{c} {v:.2f}" for c, v in d.top3)
        print(f"{p.name:30s} {top3:45s} -> {target}  ({ms:.0f} ms)")
    if times:
        # bỏ lần đầu (khởi động) khi tính trung bình
        print(f"\nSuy luận trung bình: {np.mean(times[1:] or times):.1f} ms/ảnh")


if __name__ == "__main__":
    main()
