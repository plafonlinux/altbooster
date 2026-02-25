"""Встроенные функции для вкладки «AMD Radeon»."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from gi.repository import GLib

import backend
import config

_OVERCLOCK_PARAMS = "amdgpu.ppfeaturemask=0xffffffff radeon.cik_support=0 amdgpu.cik_support=1"
_GRUB_CONF = "/etc/sysconfig/grub2"


def check_overclock(_page: Any, _arg: Any) -> bool:
    try:
        return "amdgpu.ppfeaturemask=0xffffffff" in Path(_GRUB_CONF).read_text()
    except OSError:
        return False


def enable_overclock(page, _arg: Any) -> bool:
    cmd = [
        "bash", "-c",
        f"set -e; CONF={_GRUB_CONF}; PARAMS=\"{_OVERCLOCK_PARAMS}\"; "
        "grep -q 'amdgpu.ppfeaturemask=0xffffffff' \"$CONF\" && exit 0; "
        "sed -i \"s|^\\(GRUB_CMDLINE_LINUX_DEFAULT='[^']*\\)'|\\1 $PARAMS'|\" \"$CONF\"",
    ]
    log_fn = page.log if page else lambda _: None
    ok = backend.run_privileged_sync(cmd, log_fn)
    if page:
        page.log("\n✔  Параметры разгона добавлены\n" if ok else "\n✘  Ошибка записи в GRUB\n")
    return ok


def check_wheel(_page: Any, _arg: Any) -> bool:
    username = os.environ.get("SUDO_USER") or os.environ.get("USER", "")
    r = subprocess.run(["id", "-nG", username], capture_output=True, text=True)
    return "wheel" in r.stdout.split()


def setup_lact_wheel(page, _arg: Any) -> bool:
    username = os.environ.get("SUDO_USER") or os.environ.get("USER", "")
    if not username:
        if page:
            page.log("\n✘  Не удалось определить пользователя\n")
        return False
    cmd = [
        "bash", "-c",
        f"usermod -aG wheel {username} && "
        "sed -i 's|\"admin_group\":.*|\"admin_group\": \"wheel\",|' /etc/lact/config.json 2>/dev/null || true",
    ]
    log_fn = page.log if page else lambda _: None
    ok = backend.run_privileged_sync(cmd, log_fn)
    if page:
        page.log("\n✔  Для применения нужно перезайти в сессию\n" if ok else "\n✘  Ошибка\n")
    return ok


def apply_lact_config(page, src_path: str) -> bool:
    if not src_path or not os.path.exists(src_path):
        if page:
            page.log("\n✘  Файл не найден\n")
        return False
    cmd = [
        "bash", "-c",
        f"mkdir -p /etc/lact && cp '{src_path}' /etc/lact/config.json && "
        "systemctl restart lactd 2>/dev/null || true",
    ]
    log_fn = page.log if page else lambda _: None
    ok = backend.run_privileged_sync(cmd, log_fn)
    if ok:
        config.state_set("lact_applied_conf", src_path)
    if page:
        page.log(f"\n✔  Конфиг применён: {os.path.basename(src_path)}\n" if ok else "\n✘  Ошибка\n")
    return ok


def confirm_reboot(page, _arg: Any) -> bool:
    if page:
        GLib.idle_add(page._show_reboot_dialog)
    return True
