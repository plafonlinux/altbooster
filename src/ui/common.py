"""Общие утилиты UI-модулей."""

import json
from pathlib import Path

_MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"


def load_module(name: str) -> dict:
    """Загружает JSON-модуль из директории modules/."""
    with open(_MODULES_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)
