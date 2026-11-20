import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_FILE = "local.json"   # thông số chỉnh từ web (/config), đè lên hardware.json / bins.json; không commit


def load_json(path):
    with open(ROOT / path, encoding="utf-8") as f:
        return json.load(f)


def apply_local(cfg, values):
    """Đè thông số trong local.json lên cấu hình gốc. Khoá dạng phẳng giống trang web:
    "servo.pan_ms" -> hardware.json["servo"], "hw.trigger_cm" -> hardware.json,
    "cls.confident_threshold" -> bins.json, "bin.2.pan" -> bins.json["bins"][id=2].
    Chỉ đè khoá ĐÃ CÓ trong file gốc (gõ sai / thùng đã xoá thì bỏ qua). Trả về các khoá bị bỏ qua."""
    hw, bins = cfg["hw"], cfg["bins"]
    by_id = {b["id"]: b for b in bins["bins"]}
    skipped = []
    for k, v in values.items():
        grp, _, rest = k.partition(".")
        if grp == "servo" and rest in hw["servo"]:
            hw["servo"][rest] = v
        elif grp == "hw" and rest in hw and not isinstance(hw[rest], dict):
            hw[rest] = v
        elif grp == "cls" and rest in bins and not isinstance(bins[rest], (dict, list)):
            bins[rest] = v
        elif grp == "bin" and rest.count(".") == 1 and rest.split(".")[0].isdigit() \
                and int(rest.split(".")[0]) in by_id and rest.split(".")[1] in by_id[int(rest.split(".")[0])]:
            bid, key = rest.split(".")
            by_id[int(bid)][key] = v
        else:
            skipped.append(k)
    return skipped


def load_config(config_dir="config"):
    d = Path(config_dir)
    cfg = {
        "app": load_json(d / "app.json"),
        "hw": load_json(d / "hardware.json"),
        "bins": load_json(d / "bins.json"),
        "local": {},
        "local_skipped": [],
    }
    if (ROOT / d / LOCAL_FILE).exists():
        local = load_json(d / LOCAL_FILE)
        if not isinstance(local, dict):
            raise SystemExit(f"{d / LOCAL_FILE} phải là object JSON (xoá file này để về cấu hình gốc)")
        cfg["local"] = local
        cfg["local_skipped"] = apply_local(cfg, local)
    return cfg
