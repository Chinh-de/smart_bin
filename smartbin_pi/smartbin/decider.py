"""Từ xác suất 10 lớp -> thùng hoặc từ chối, theo config/bins.json."""
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Decision:
    result: str                     # "BIN" | "REJECT"
    bin_id: int | None
    bin_name: str | None
    angle: float | None
    message: str | None
    top_class: str
    top_prob: float
    top3: list = field(default_factory=list)
    bin_probs: dict = field(default_factory=dict)
    uncertain: bool = False


class Decider:
    def __init__(self, cfg, classes):
        self.classes = list(classes)
        self.bins = {b["id"]: b for b in cfg["bins"]}
        self.reject = cfg.get("reject", [])
        self.conf_th = cfg["confident_threshold"]
        self.uncertain_bin = cfg["uncertain_bin"]

        covered = {c for b in cfg["bins"] for c in b["classes"]} | {r["class"] for r in self.reject}
        for c in set(self.classes) - covered:
            log.warning("Lớp '%s' không thuộc thùng nào -> sẽ tính vào thùng %s", c, self.uncertain_bin)
        for c in covered - set(self.classes):
            raise ValueError(f"bins.json có lớp '{c}' không có trong labels.json")

    def decide(self, probs):
        p = dict(zip(self.classes, map(float, probs)))
        ranked = sorted(p.items(), key=lambda kv: -kv[1])
        base = dict(top_class=ranked[0][0], top_prob=round(ranked[0][1], 4),
                    top3=[[c, round(v, 4)] for c, v in ranked[:3]])

        # 1. từ chối (pin): kiểm tra trước tiên, ngưỡng thấp -> ưu tiên an toàn
        for r in self.reject:
            if p[r["class"]] >= r["threshold"]:
                return Decision("REJECT", None, None, None, r["message"], **base)

        # 2. cộng xác suất theo thùng; lớp không thuộc thùng nào tính vào uncertain_bin
        bin_probs = {bid: 0.0 for bid in self.bins}
        for c, v in p.items():
            if any(c == r["class"] for r in self.reject):
                continue
            bid = next((b["id"] for b in self.bins.values() if c in b["classes"]), self.uncertain_bin)
            bin_probs[bid] += v
        bid = max(bin_probs, key=bin_probs.get)

        # 3. không chắc chắn -> thùng mặc định
        uncertain = bin_probs[bid] < self.conf_th
        if uncertain:
            bid = self.uncertain_bin
        b = self.bins[bid]
        return Decision("BIN", bid, b["name"], b["angle"], None,
                        bin_probs={str(k): round(v, 4) for k, v in bin_probs.items()},
                        uncertain=uncertain, **base)
