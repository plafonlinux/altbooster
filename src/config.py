"""
config.py — конфигурация, состояние сессии и данные приложений/задач.

Состояние хранится в ~/.config/altbooster/state.json и загружается
один раз при старте через load_state(). Все изменения сохраняются
немедленно через state_set().
"""

import json
import subprocess
import threading
import urllib.request
from pathlib import Path


# ── Пути к файлам конфигурации ────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / ".config" / "altbooster"
CONFIG_FILE = CONFIG_DIR / "window.json"
STATE_FILE  = CONFIG_DIR / "state.json"

VERSION = "5.6"


# ── Пути к кэшу DaVinci Resolve по умолчанию ─────────────────────────────────

DV_CACHE_DEFAULT = "/mnt/datassd/DaVinci Resolve/Work Folders/CacheClip"
DV_PROXY_DEFAULT = "/mnt/datassd/DaVinci Resolve/Work Folders/ProxyMedia"

# Gsettings-схемы — чтобы не повторять строки по всему коду
GSETTINGS_MUTTER      = "org.gnome.mutter"
GSETTINGS_KEYBINDINGS = "org.gnome.desktop.wm.keybindings"


# ── Блокировки APT ────────────────────────────────────────────────────────────

APT_LOCK_FILES = [
    "/var/cache/apt/archives/lock",
    "/var/lib/dpkg/lock-frontend",
    "/var/lib/apt/lists/lock",
]


# ── Состояние сессии ──────────────────────────────────────────────────────────

_state: dict = {}


def load_state() -> None:
    """Загружает сохранённое состояние из файла. Вызывается один раз при старте."""
    global _state
    try:
        with open(STATE_FILE) as f:
            _state = json.load(f)
    except (OSError, json.JSONDecodeError):
        _state = {}


def save_state() -> None:
    """Записывает текущее состояние на диск."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except OSError:
        pass


def state_get(key: str, default=None):
    """Читает значение из состояния сессии."""
    return _state.get(key, default)


def state_set(key: str, value) -> None:
    """Записывает значение в состояние и сохраняет на диск."""
    _state[key] = value
    save_state()


def reset_state() -> None:
    """Полностью сбрасывает сохранённое состояние."""
    _state.clear()
    save_state()


# ── Пути к кэшу DaVinci (с учётом пользовательских настроек) ─────────────────

def get_dv_cache() -> str:
    return state_get("dv_cache_path") or DV_CACHE_DEFAULT


def get_dv_proxy() -> str:
    return state_get("dv_proxy_path") or DV_PROXY_DEFAULT


# ── Определение файловой системы ──────────────────────────────────────────────

def is_btrfs() -> bool:
    """Возвращает True если в системе есть хотя бы один Btrfs-раздел."""
    try:
        result = subprocess.run(
            ["findmnt", "-t", "btrfs", "-n", "-o", "TARGET"],
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
    except OSError:
        return False


def check_update(on_result):
    """Проверяет наличие новой версии на GitHub. Вызывает on_result(version_str | None)."""
    def _worker():
        try:
            url = "https://api.github.com/repos/plafonlinux/altbooster/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "ALTBooster"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                tag = data.get("tag_name", "").lstrip("v")
                on_result(tag)
        except Exception:
            on_result(None)
    threading.Thread(target=_worker, daemon=True).start()
