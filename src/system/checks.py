"""
checks.py — Проверки состояния системы.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .privileges import get_sudo_password
from .gsettings import gsettings_get
import config

def is_sudo_enabled() -> bool:
    """Проверяет статус sudo через control sudowheel в ALT Linux."""

    # Способ 1: Проверка через sudo -n (неинтерактивно).
    # Работает если в системе ещё действует кэш sudo-сессии.
    try:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        res = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True, env=env, timeout=2)
        if res.returncode == 0:
            return True
        # Если sudo настроен, но требует пароль, он вернёт ошибку "a password is required".
        # Если не настроен — "user is not in the sudoers file".
        if "password is required" in res.stderr.lower():
            return True
    except Exception:
        pass

    # Способ 2: Если пароль уже введён в приложении, проверяем control sudowheel.
    # Это единственный надёжный способ: `control sudowheel` читает реальный /etc/sudoers,
    # поэтому не даёт ложных срабатываний при отключении sudo через wheel-группу.
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

    # Способ 3 (НЕ используем): проверка членства в группе wheel ненадёжна,
    # потому что `control sudowheel disabled` не удаляет пользователя из группы,
    # а лишь убирает её из /etc/sudoers — это приводило к ложному "Активировано".

    return False

def is_flathub_enabled() -> bool:
    """Проверяет, включен ли репозиторий Flathub."""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    result = subprocess.run(["flatpak", "remotes"], capture_output=True, text=True, env=env)
    return "flathub" in result.stdout.lower()

def is_fstrim_enabled() -> bool:
    """Проверяет, включен ли таймер fstrim."""
    result = subprocess.run(["systemctl", "is-enabled", "fstrim.timer"], capture_output=True)
    return result.returncode == 0

def is_fractional_scaling_enabled() -> bool:
    """Проверяет, включено ли дробное масштабирование."""
    value = gsettings_get("org.gnome.mutter", "experimental-features")
    return "scale-monitor-framebuffer" in value

def is_system_busy() -> bool:
    """Проверяет занятость пакетного менеджера."""
    try:
        if subprocess.run(["pgrep", "-f", "packagekitd"], capture_output=True).returncode == 0:
            return True
        for lock_file in config.APT_LOCK_FILES:
            if os.path.exists(lock_file) and subprocess.run(["fuser", lock_file], capture_output=True).returncode == 0:
                return True
    except OSError:
        pass
    return False

def check_app_installed(source: dict) -> bool:
    """Проверяет, установлено ли приложение."""
    kind, value = source["check"]
    if kind == "flatpak":
        res = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True)
        return value in res.stdout
    if kind == "rpm":
        return subprocess.run(["rpm", "-q", value], capture_output=True).returncode == 0
    if kind == "path":
        return os.path.exists(os.path.expanduser(value))
    if kind == "which":
        return shutil.which(value) is not None
    return False

def is_vm_dirty_optimized() -> bool:
    """Проверяет, оптимизированы ли параметры vm dirty."""
    try:
        content = Path("/etc/sysctl.d/90-dirty.conf").read_text(encoding="utf-8")
        return "67108864" in content
    except OSError:
        return False

def is_drive_menu_patched() -> bool:
    """Проверяет, пропатчено ли расширение drive-menu."""
    try:
        ext_path = "/usr/share/gnome-shell/extensions/drive-menu@gnome-shell-extensions.gcampax.github.com/extension.js"
        content = Path(ext_path).read_text(encoding="utf-8")
        return "GLib.timeout_add_seconds" in content
    except OSError:
        return False

def is_journal_optimized() -> bool:
    """Проверяет, оптимизирован ли журнал systemd."""
    paths = ["/etc/systemd/journald.conf", "/etc/systemd/journald.conf.d/99-altbooster.conf"]
    for p in paths:
        try:
            if "SystemMaxUse=100M" in Path(p).read_text(encoding="utf-8"): return True
        except OSError: continue
    return False

def is_davinci_installed() -> bool:
    """Проверяет, установлен ли DaVinci Resolve."""
    return os.path.exists("/opt/resolve/bin/resolve") or subprocess.run(["rpm", "-q", "davinci-resolve"], capture_output=True).returncode == 0

def is_aac_installed() -> bool:
    """Проверяет, установлен ли кодек AAC для DaVinci Resolve."""
    return os.path.exists("/opt/resolve/IOPlugins/aac_encoder_plugin.dvcp.bundle")

def is_fairlight_installed() -> bool:
    """Проверяет, установлен ли плагин Fairlight для DaVinci Resolve."""
    return subprocess.run(["rpm", "-q", "alsa-plugins-pulse"], capture_output=True).returncode == 0

def is_epm_installed() -> bool:
    """Проверяет, установлен ли пакетный менеджер eepm."""
    return subprocess.run(["rpm", "-q", "eepm"], capture_output=True).returncode == 0
