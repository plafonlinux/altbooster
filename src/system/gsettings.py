"""
gsettings.py — Обертки для утилиты gsettings.
"""

from __future__ import annotations

import subprocess
from typing import Sequence

def run_gsettings(args: Sequence[str]) -> bool:
    """Выполняет команду gsettings."""
    result = subprocess.run(["gsettings", *args], capture_output=True, text=True)
    return result.returncode == 0

def gsettings_get(schema: str, key: str) -> str:
    """Возвращает значение ключа gsettings."""
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
