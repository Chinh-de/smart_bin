"""Từ xác suất 10 lớp -> thùng, theo config/bins.json.

1. priority: lớp nguy hiểm (pin) chỉ cần vượt ngưỡng thấp là vào thẳng thùng chỉ định (ưu tiên an toàn).
2. cộng xác suất các lớp theo thùng, chọn thùng lớn nhất.
3. tổng xác suất thùng đó < confident_threshold -> uncertain_bin."""
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Decision:
    bin: dict                       # mục trong bins.json (id, name, pan, tilt_dir, classes)
    top_class: str
    top_prob: float
    top3: list = field(default_factory=list)
    bin_probs: dict = field(default_factory=dict)
    uncertain: bool = False
    message: str | None = None      # ghi chú lý do (ưu tiên / không chắc chắn)

    @property
    def bin_id(self):
        return self.bin["id"]

    @property
    def bin_name(self):
        return self.bin["name"]


class Decider:
    def __init__(self, cfg, classes):
        self.classes = list(classes)
        self.bins = {b["id"]: b for b in cfg["bins"]}
        if len(self.bins) != len(cfg["bins"]):
            raise ValueError("bins.json: id thùng bị trùng")
        self.priority = cfg.get("priority", [])
        self.conf_th = cfg["confident_threshold"]
        self.uncertain_bin = cfg["uncertain_bin"]

        if self.uncertain_bin not in self.bins:
            raise ValueError(f"bins.json: uncertain_bin={self.uncertain_bin} không có trong bins")
        self.class_bin = {}
        for b in cfg["bins"]:
            for c in b["classes"]:
                if c in self.class_bin:
                    raise ValueError(f"bins.json: lớp '{c}' nằm ở 2 thùng {self.class_bin[c]} và {b['id']}")
                self.class_bin[c] = b["id"]
        for r in self.priority:
            if r["bin"] not in self.bins:
                raise ValueError(f"bins.json: priority '{r['class']}' trỏ tới thùng {r['bin']} không tồn tại")
        for c in (set(self.class_bin) | {r["class"] for r in self.priority}) - set(self.classes):
            raise ValueError(f"bins.json có lớp '{c}' không có trong labels.json")
        for c in set(self.classes) - set(self.class_bin):
            log.warning("Lớp '%s' không thuộc thùng nào -> tính vào thùng %s", c, self.uncertain_bin)

    def decide(self, probs):
        p = dict(zip(self.classes, map(float, probs)))
        ranked = sorted(p.items(), key=lambda kv: -kv[1])
        base = dict(top_class=ranked[0][0], top_prob=round(ranked[0][1], 4),
                    top3=[[c, round(v, 4)] for c, v in ranked[:3]])

        bin_probs = {bid: 0.0 for bid in self.bins}
        for c, v in p.items():
            bin_probs[self.class_bin.get(c, self.uncertain_bin)] += v
        base["bin_probs"] = {str(k): round(v, 4) for k, v in bin_probs.items()}

        for r in self.priority:
            if p[r["class"]] >= r["threshold"]:
                return Decision(self.bins[r["bin"]], **base,
                                message=f"Ưu tiên: {r['class']} {p[r['class']]:.2f} ≥ {r['threshold']}")

        bid = max(bin_probs, key=bin_probs.get)
        if bin_probs[bid] < self.conf_th:
            return Decision(self.bins[self.uncertain_bin], **base, uncertain=True,
                            message=f"Không chắc chắn ({bin_probs[bid]:.2f} < {self.conf_th}) -> thùng mặc định")
        return Decision(self.bins[bid], **base)
