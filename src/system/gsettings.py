from __future__ import annotations

import subprocess
from typing import Sequence


def run_gsettings(args: Sequence[str]) -> bool:
    try:
        result = subprocess.run(
            ["gsettings", *args],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def gsettings_get(schema: str, key: str) -> str:
    try:
        result = subprocess.run(
            ["gsettings", "get", schema, key],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""
