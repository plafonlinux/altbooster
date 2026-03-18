import json
from pathlib import Path

_MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"


def load_module(name: str) -> dict:
    try:
        with open(_MODULES_DIR / f"{name}.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ALT Booster] Не удалось загрузить модуль '{name}': {e}")
        return {}
