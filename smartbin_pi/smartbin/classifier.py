"""Nạp mô hình TFLite và phân loại ảnh camera (10 lớp)."""
import json
import time

import cv2
import numpy as np


def _interpreter_cls():
    # ưu tiên LiteRT (tên mới của tflite-runtime), cuối cùng mới tới TensorFlow đầy đủ
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter
    return Interpreter


class Classifier:
    def __init__(self, model_path, labels_path, num_threads=4):
        with open(labels_path, encoding="utf-8") as f:
            meta = json.load(f)
        self.classes = meta["classes"]
        self.size = meta.get("img_size", 224)

        self.itp = _interpreter_cls()(model_path=str(model_path), num_threads=num_threads)
        try:
            self.itp.allocate_tensors()
        except RuntimeError as e:
            raise RuntimeError(f"Không nạp được {model_path} ({e}). Thử đổi sang model_fp16.tflite trong config/app.json") from e
        self.inp = self.itp.get_input_details()[0]
        self.out = self.itp.get_output_details()[0]

    def preprocess(self, frame_bgr):
        # giống lúc train: vuông giữa -> 224x224 -> RGB float32 thang 0..255 (model tự chuẩn hoá)
        h, w = frame_bgr.shape[:2]
        s = min(h, w)
        y, x = (h - s) // 2, (w - s) // 2
        img = cv2.resize(frame_bgr[y:y + s, x:x + s], (self.size, self.size), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)

    def _run(self, img):
        x = img[None]
        if self.inp["dtype"] != np.float32:          # model full-int8 có I/O nguyên
            scale, zero = self.inp["quantization"]
            x = np.round(x / scale + zero).astype(self.inp["dtype"])
        self.itp.set_tensor(self.inp["index"], x)
        self.itp.invoke()
        y = self.itp.get_tensor(self.out["index"])[0]
        if self.out["dtype"] != np.float32:
            scale, zero = self.out["quantization"]
            y = (y.astype(np.float32) - zero) * scale
        return y

    def predict(self, frames):
        """Trả về (xác suất trung bình các khung, ms suy luận trung bình mỗi khung)."""
        probs, t = [], time.perf_counter()
        for f in frames:
            probs.append(self._run(self.preprocess(f)))
        ms = (time.perf_counter() - t) * 1000 / len(frames)
        return np.mean(probs, axis=0), ms


class RandomClassifier:
    """Dùng ở chế độ giả lập khi chưa có file model."""

    def __init__(self, classes):
        self.classes = classes
        self.rng = np.random.default_rng()

    def predict(self, frames):
        p = self.rng.dirichlet(np.full(len(self.classes), 0.3))
        return p, 0.0
