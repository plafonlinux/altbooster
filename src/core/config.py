import json
import os
import subprocess
import threading
import urllib.request
from pathlib import Path


CONFIG_DIR  = Path.home() / ".config" / "altbooster"
CONFIG_FILE = CONFIG_DIR / "window.json"
STATE_FILE  = CONFIG_DIR / "state.json"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

VERSION = "5.6.8-beta"

DEBUG: bool = False

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


def load_state() -> None:
    global _state
    try:
        with open(STATE_FILE) as f:
            _state = json.load(f)
    except (OSError, json.JSONDecodeError):
        _state = {}


def save_state() -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=2)
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


def state_get(key: str, default=None):
    return _state.get(key, default)


def state_set(key: str, value) -> None:
    _state[key] = value
    save_state()


def reset_state() -> None:
    _state.clear()
    save_state()


def get_state_copy() -> dict:
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


_GITHUB_API = "https://api.github.com/repos/plafonlinux/altbooster"


def _fetch_github(path: str) -> object:
    url = f"{_GITHUB_API}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "ALTBooster"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode())


def check_update(on_result):
    def _worker():
        try:
            data = _fetch_github("releases/latest")
            on_result(data.get("tag_name", "").lstrip("v"))
        except Exception:
            on_result(None)
    threading.Thread(target=_worker, daemon=True).start()


def check_update_beta(on_result):
    def _worker():
        try:
            releases = _fetch_github("releases?per_page=10")
            for r in releases:
                if r.get("prerelease"):
                    on_result(r.get("tag_name", "").lstrip("v"))
                    return
            on_result(None)
        except Exception:
            on_result(None)
    threading.Thread(target=_worker, daemon=True).start()
