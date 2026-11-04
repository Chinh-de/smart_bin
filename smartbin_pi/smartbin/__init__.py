import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_json(path):
    with open(ROOT / path, encoding="utf-8") as f:
        return json.load(f)


def load_config(config_dir="config"):
    d = Path(config_dir)
    return {
        "app": load_json(d / "app.json"),
        "hw": load_json(d / "hardware.json"),
        "bins": load_json(d / "bins.json"),
    }
