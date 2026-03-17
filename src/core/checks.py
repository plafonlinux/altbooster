from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .privileges import get_sudo_password, run_privileged_sync
from .gsettings import gsettings_get
from core import config

def is_sudo_enabled() -> bool:
    try:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        res = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True, env=env, timeout=2)
        if sys.stdin.isatty() and res.returncode == 0:
            return True
        if "password is required" in res.stderr.lower():
            return True
    except Exception:
        pass

    password = get_sudo_password()
    if password:
        try:
            env = os.environ.copy()
            env["LC_ALL"] = "C"
            res = subprocess.run(
                ["sudo", "-S", "/usr/sbin/control", "sudowheel"],
                input=password + "\n",
                capture_output=True,
                text=True,
                timeout=2,
                env=env,
            )
            out = (res.stdout + res.stderr).lower()
            if "enabled" in out or "wheelonly" in out:
                return True
        except Exception:
            pass

    if not sys.stdin.isatty():
        control = shutil.which("control") or "/usr/sbin/control"
        lines: list[str] = []
        run_privileged_sync([control, "sudowheel"], lambda line: lines.append(line))
        out = "".join(lines).lower()
        if "enabled" in out or "wheelonly" in out:
            return True

    return False


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


def check_app_installed(source: dict) -> bool:
    kind, value = source["check"]
    try:
        if kind == "flatpak":
            res = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application"],
                capture_output=True, text=True, timeout=15,
            )
            return value in res.stdout
        if kind == "rpm":
            if subprocess.run(["rpm", "-q", value], capture_output=True, timeout=10).returncode == 0:
                return True
            return shutil.which(value) is not None
        if kind == "path":
            return os.path.exists(os.path.expanduser(value))
        if kind == "which":
            return shutil.which(value) is not None
    except (subprocess.TimeoutExpired, OSError):
        return False
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
