from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from .privileges import run_privileged_sync
from .gsettings import gsettings_get
from core import config


_desktop_files_cache: list[Path] | None = None
_desktop_files_lock = threading.Lock()


def _iter_xdg_desktop_files() -> list[Path]:
    global _desktop_files_cache
    with _desktop_files_lock:
        if _desktop_files_cache is not None:
            return list(_desktop_files_cache)
    home = Path.home()
    dirs = [
        home / ".local/share/applications",
        home / ".local/share/flatpak/exports/share/applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
    ]
    out: list[Path] = []
    for d in dirs:
        try:
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if p.is_file() and p.suffix.lower() == ".desktop":
                    out.append(p)
        except OSError:
            continue
    with _desktop_files_lock:
        _desktop_files_cache = out
    return out


def invalidate_desktop_files_cache() -> None:
    global _desktop_files_cache
    with _desktop_files_lock:
        _desktop_files_cache = None


def _desktop_keyword_installed(keyword: str) -> bool:
    """True if a .desktop in XDG dirs mentions keyword in filename or header (e.g. PhotoGIMP)."""
    if not keyword or not isinstance(keyword, str):
        return False
    sub = keyword.lower()
    for p in _iter_xdg_desktop_files():
        if sub in p.name.lower():
            return True
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:8192].lower()
        except OSError:
            continue
        if sub not in head:
            continue
        if "[desktop entry]" not in head:
            continue
        return True
    return False


def _eval_check_pair(kind: str, value) -> bool:
    if kind == "any_of":
        if not isinstance(value, (list, tuple)):
            return False
        return any(
            isinstance(pair, (list, tuple)) and len(pair) >= 2 and _eval_check_pair(pair[0], pair[1])
            for pair in value
        )
    try:
        if kind == "flatpak":
            return value in _get_flatpak_installed()
        if kind == "rpm":
            if subprocess.run(["rpm", "-q", value], capture_output=True, timeout=10).returncode == 0:
                return True
            return shutil.which(value) is not None
        if kind == "path":
            return os.path.exists(os.path.expanduser(value))
        if kind == "which":
            return shutil.which(value) is not None
        if kind == "desktop_keyword":
            return _desktop_keyword_installed(str(value))
    except (subprocess.TimeoutExpired, OSError, TypeError):
        return False
    return False


def is_sudo_enabled() -> bool:
    control = shutil.which("control") or "/usr/sbin/control"
    lines: list[str] = []
    run_privileged_sync([control, "sudowheel"], lambda line: lines.append(line))
    out = "".join(lines).lower()
    return "enabled" in out or "wheelonly" in out


def is_flathub_enabled() -> bool:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(["flatpak", "remotes"], capture_output=True, text=True, env=env, timeout=10)
        return "flathub" in result.stdout.lower()
    except (subprocess.TimeoutExpired, OSError):
        return False


def is_fstrim_enabled() -> bool:
    try:
        result = subprocess.run(["systemctl", "is-enabled", "fstrim.timer"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def is_fractional_scaling_enabled() -> bool:
    value = gsettings_get("org.gnome.mutter", "experimental-features")
    return "scale-monitor-framebuffer" in value


def is_system_busy() -> bool:
    try:
        if subprocess.run(["pgrep", "-f", "packagekitd"], capture_output=True, timeout=5).returncode == 0:
            return True
        for lock_file in config.APT_LOCK_FILES:
            if os.path.exists(lock_file) and subprocess.run(["fuser", lock_file], capture_output=True, timeout=5).returncode == 0:
                return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


_flatpak_list_cache: set[str] | None = None
_flatpak_list_lock = threading.Lock()


def _get_flatpak_installed() -> set[str]:
    global _flatpak_list_cache
    with _flatpak_list_lock:
        if _flatpak_list_cache is not None:
            return _flatpak_list_cache
    try:
        res = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True, text=True, timeout=15,
        )
        ids = {line.strip() for line in res.stdout.splitlines() if line.strip()}
    except (subprocess.TimeoutExpired, OSError):
        ids = set()
    with _flatpak_list_lock:
        _flatpak_list_cache = ids
    return ids


def invalidate_flatpak_cache() -> None:
    global _flatpak_list_cache
    with _flatpak_list_lock:
        _flatpak_list_cache = None


def invalidate_app_detection_caches() -> None:
    """Сброс кэшей .desktop и списка Flatpak (например после возврата в окно)."""
    invalidate_desktop_files_cache()
    invalidate_flatpak_cache()


def check_app_installed(source: dict) -> bool:
    chk = source.get("check")
    if not chk or len(chk) < 2:
        return False
    try:
        return _eval_check_pair(chk[0], chk[1])
    except (TypeError, KeyError, IndexError):
        return False


def is_vm_dirty_optimized() -> bool:
    try:
        content = Path("/etc/sysctl.d/90-dirty.conf").read_text(encoding="utf-8")
        return "67108864" in content
    except OSError:
        return False


def is_drive_menu_patched() -> bool:
    try:
        ext_path = "/usr/share/gnome-shell/extensions/drive-menu@gnome-shell-extensions.gcampax.github.com/extension.js"
        content = Path(ext_path).read_text(encoding="utf-8")
        return "this._mounts.some" in content
    except OSError:
        return False


def is_journal_optimized() -> bool:
    paths = ["/etc/systemd/journald.conf", "/etc/systemd/journald.conf.d/99-altbooster.conf"]
    for p in paths:
        try:
            if "SystemMaxUse=100M" in Path(p).read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def is_davinci_installed() -> bool:
    if os.path.exists("/opt/resolve/bin/resolve"):
        return True
    try:
        return subprocess.run(["rpm", "-q", "davinci-resolve"], capture_output=True, timeout=10).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def is_aac_installed() -> bool:
    return os.path.exists("/opt/resolve/IOPlugins/aac_encoder_plugin.dvcp.bundle")


def is_fairlight_installed() -> bool:
    try:
        pkg_ok = subprocess.run(["rpm", "-q", "alsa-plugins-pulse"], capture_output=True, timeout=10).returncode == 0
        if not pkg_ok:
            return False
        asound = "/etc/asound.conf"
        if not os.path.exists(asound):
            return False
        with open(asound, encoding="utf-8") as f:
            return "type pulse" in f.read()
    except (subprocess.TimeoutExpired, OSError):
        return False


def is_epm_installed() -> bool:
    try:
        return subprocess.run(["rpm", "-q", "eepm"], capture_output=True, timeout=10).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
