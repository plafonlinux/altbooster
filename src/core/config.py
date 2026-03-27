import json
import os
import subprocess
import threading
import traceback
from pathlib import Path


CONFIG_DIR  = Path.home() / ".config" / "altbooster"
CONFIG_FILE = CONFIG_DIR / "window.json"
STATE_FILE  = CONFIG_DIR / "state.json"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

VERSION = "5.7-alpha"

DEBUG: bool = False
INITIAL_TAB: str = ""


def log_exception(context: str = "") -> None:
    if DEBUG:
        prefix = f"[DEBUG] {context}: " if context else "[DEBUG] "
        print(prefix, end="")
        traceback.print_exc()

_STATE_SAVE_DEBOUNCE_S = 0.45
_state_save_timer: threading.Timer | None = None
_state_save_timer_lock = threading.Lock()


def init_runtime(*, debug: bool = False, initial_tab: str = "") -> None:
    """Вызывать один раз при старте процесса до импорта UI (DEBUG, INITIAL_TAB)."""
    global DEBUG, INITIAL_TAB
    DEBUG = debug
    INITIAL_TAB = initial_tab

PRESETS_DIR = CONFIG_DIR / "presets"

DV_CACHE_DEFAULT = ""
DV_PROXY_DEFAULT = ""

GSETTINGS_MUTTER      = "org.gnome.mutter"
GSETTINGS_KEYBINDINGS = "org.gnome.desktop.wm.keybindings"

APT_LOCK_FILES = [
    "/var/cache/apt/archives/lock",
    "/var/lib/dpkg/lock-frontend",
    "/var/lib/apt/lists/lock",
]

_state: dict = {}
_state_lock = threading.Lock()


def load_state() -> None:
    global _state
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _state_lock:
            _state = data
    except (OSError, json.JSONDecodeError):
        with _state_lock:
            _state = {}


def save_state() -> None:
    with _state_lock:
        data = dict(_state)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def _cancel_debounced_state_save() -> None:
    global _state_save_timer
    with _state_save_timer_lock:
        if _state_save_timer is not None:
            _state_save_timer.cancel()
            _state_save_timer = None


def _debounced_state_save_fire() -> None:
    global _state_save_timer
    with _state_save_timer_lock:
        _state_save_timer = None
    save_state()


def _schedule_state_save() -> None:
    global _state_save_timer
    with _state_save_timer_lock:
        if _state_save_timer is not None:
            _state_save_timer.cancel()
        _state_save_timer = threading.Timer(_STATE_SAVE_DEBOUNCE_S, _debounced_state_save_fire)
        _state_save_timer.daemon = True
        _state_save_timer.start()


def flush_pending_state() -> None:
    """Сбросить отложенную запись state.json на диск (перед выходом из приложения)."""
    _cancel_debounced_state_save()
    save_state()


def state_get(key: str, default=None):
    with _state_lock:
        return _state.get(key, default)


def state_set(key: str, value) -> None:
    with _state_lock:
        _state[key] = value
    _schedule_state_save()


def reset_state() -> None:
    _cancel_debounced_state_save()
    with _state_lock:
        _state.clear()
    save_state()


def get_state_copy() -> dict:
    with _state_lock:
        return dict(_state)


def get_dv_cache() -> str:
    return state_get("dv_cache_path") or DV_CACHE_DEFAULT


def get_dv_proxy() -> str:
    return state_get("dv_proxy_path") or DV_PROXY_DEFAULT


def is_btrfs() -> bool:
    try:
        result = subprocess.run(
            ["findmnt", "-t", "btrfs", "-n", "-o", "TARGET"],
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
    except OSError:
        return False


