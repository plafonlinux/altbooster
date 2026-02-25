"""
backend.py — системные команды, проверки и привилегированное выполнение.

Все операции, требующие sudo, запускаются в фоновых потоках.
Результат возвращается через GLib.idle_add, чтобы обновлять UI из главного потока.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from gi.repository import GLib

# ── Типы ──────────────────────────────────────────────────────────────────────

OnLine = Callable[[str], None]
OnDone = Callable[[bool], None]

# ── Пароль sudo ──────────────────────────────────────────────────────────────

_sudo_password: str | None = None
_sudo_lock = threading.Lock()

APT_LOCK_FILES = [
    "/var/cache/apt/archives/lock",
    "/var/lib/dpkg/lock-frontend",
    "/var/lib/apt/lists/lock",
]

def set_sudo_password(pw: str) -> None:
    """Сохраняет sudo-пароль в памяти процесса."""
    global _sudo_password
    with _sudo_lock:
        _sudo_password = pw

def _get_sudo_password() -> str:
    with _sudo_lock:
        return _sudo_password or ""

def sudo_check(pw: str) -> bool:
    """Проверяет корректность sudo-пароля через вызов /bin/true."""
    try:
        result = subprocess.run(
            ["sudo", "-k", "-S", "/bin/true"],
            input=pw + "\n",
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

# ── Ожидание APT-блокировки ───────────────────────────────────────────────────

def _is_apt_locked() -> bool:
    """Возвращает True, если хотя бы один файл блокировки APT занят."""
    for lock_file in APT_LOCK_FILES:
        if not os.path.exists(lock_file):
            continue
        if subprocess.run(["fuser", lock_file], capture_output=True).returncode == 0:
            return True
    return False

def _wait_for_apt_lock(on_line: OnLine | None = None, timeout: int = 60) -> bool:
    """
    Ждёт освобождения APT-блокировки (проверка каждые 5 секунд).

    Возвращает True если блокировка снята,
    False если истёк таймаут ожидания.
    """
    for attempt in range(timeout // 5):
        if not _is_apt_locked():
            return True
        if on_line:
            GLib.idle_add(
                on_line,
                f"⏳ APT занят другим процессом, ожидание... ({attempt + 1})\n",
            )
        time.sleep(5)
    return False

# ── Выполнение привилегированных команд ───────────────────────────────────────

def run_privileged(cmd: Sequence[str], on_line: OnLine, on_done: OnDone) -> None:
    """
    Запускает команду через sudo -S (пароль передаётся через stdin).

    Используется для apt, apt-get, flatpak, systemctl и других команд,
    которые не порождают дочерние sudo-процессы.
    """

    def _worker() -> None:
        password = _get_sudo_password()

        if cmd and cmd[0] in ("apt", "apt-get", "flatpak"):
            _wait_for_apt_lock(on_line)

        proc = subprocess.Popen(
            ["sudo", "-S", *cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if proc.stdin:
            try:
                proc.stdin.write(password + "\n")
                proc.stdin.flush()
            except BrokenPipeError:
                pass
            proc.stdin.close()

        def _drain_stderr() -> None:
            if not proc.stderr:
                return
            for line in proc.stderr:
                low = line.lower()
                if "sudo" in low or "password" in low:
                    continue
                GLib.idle_add(on_line, line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        if proc.stdout:
            for line in proc.stdout:
                GLib.idle_add(on_line, line)

        stderr_thread.join()
        proc.wait()
        GLib.idle_add(on_done, proc.returncode == 0)

    threading.Thread(target=_worker, daemon=True).start()

def run_privileged_sync(cmd: Sequence[str], on_line: OnLine) -> bool:
    """Блокирующая обёртка над run_privileged."""
    event = threading.Event()
    result = False

    def _done(ok: bool) -> None:
        nonlocal result
        result = ok
        event.set()

    run_privileged(cmd, on_line, _done)
    event.wait()
    return result

# ── EPM (sudo -A + SUDO_ASKPASS) ──────────────────────────────────────────────

def run_epm(cmd: Sequence[str], on_line: OnLine, on_done: OnDone) -> None:
    """
    Запускает epm через sudo -A (пароль через SUDO_ASKPASS).

    epm сам вызывает apt внутри себя — sudo -S приводит к зависанию.
    """

    def _worker() -> None:
        password = _get_sudo_password()
        env = os.environ.copy()

        fd, askpass_path = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "w") as script:
            script.write("#!/bin/sh\n")
            script.write(f"echo {password!r}\n")

        os.chmod(askpass_path, stat.S_IRWXU)
        env["SUDO_ASKPASS"] = askpass_path

        try:
            _wait_for_apt_lock(on_line)

            proc = subprocess.Popen(
                ["sudo", "-A", *cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                env=env,
            )

            if proc.stdout:
                for line in proc.stdout:
                    GLib.idle_add(on_line, line)

            proc.wait()
            GLib.idle_add(on_done, proc.returncode == 0)

        finally:
            try:
                os.unlink(askpass_path)
            except OSError:
                pass

    threading.Thread(target=_worker, daemon=True).start()

def run_epm_sync(cmd: Sequence[str], on_line: OnLine) -> bool:
    """Блокирующая обёртка над run_epm."""
    event = threading.Event()
    result = False

    def _done(ok: bool) -> None:
        nonlocal result
        result = ok
        event.set()

    run_epm(cmd, on_line, _done)
    event.wait()
    return result

# ── gsettings ─────────────────────────────────────────────────────────────────

def run_gsettings(args: Sequence[str]) -> bool:
    result = subprocess.run(["gsettings", *args], capture_output=True, text=True)
    return result.returncode == 0

def gsettings_get(schema: str, key: str) -> str:
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()

# ── Проверки состояния системы ────────────────────────────────────────────────

def is_sudo_enabled() -> bool:
    """Проверяет, включено ли правило sudowheel в ALT Linux."""
    try:
        result = subprocess.run(
            ["control", "sudowheel"],
            capture_output=True,
            text=True,
        )
        return "enabled" in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False

def is_flathub_enabled() -> bool:
    result = subprocess.run(
        ["flatpak", "remotes"],
        capture_output=True,
        text=True,
    )
    return "flathub" in result.stdout.lower()

def is_fractional_scaling_enabled() -> bool:
    value = gsettings_get("org.gnome.mutter", "experimental-features")
    return "scale-monitor-framebuffer" in value

def is_fstrim_enabled() -> bool:
    result = subprocess.run(
        ["systemctl", "is-enabled", "fstrim.timer"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0

def is_journal_optimized() -> bool:
    """Проверяет наличие оптимизации журналов systemd."""
    paths = [
        "/etc/systemd/journald.conf",
        "/etc/systemd/journald.conf.d/99-altbooster.conf",
    ]
    for path in paths:
        try:
            content = Path(path).read_text()
            if "SystemMaxUse=100M" in content:
                return True
        except OSError:
            continue
    return False

def is_system_busy() -> bool:
    """Проверяет, занят ли пакетный менеджер."""
    try:
        if subprocess.run(
            ["pgrep", "-f", "packagekitd"],
            capture_output=True,
        ).returncode == 0:
            return True

        for lock_file in APT_LOCK_FILES:
            if os.path.exists(lock_file):
                if subprocess.run(["fuser", lock_file], capture_output=True).returncode == 0:
                    return True
    except OSError:
        pass
    return False

def check_app_installed(source: dict) -> bool:
    """Проверяет, установлено ли приложение по типу источника."""
    kind, value = source["check"]

    if kind == "flatpak":
        result = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True,
            text=True,
        )
        return value in result.stdout

    if kind == "rpm":
        return subprocess.run(["rpm", "-q", value], capture_output=True).returncode == 0

    if kind == "path":
        return os.path.exists(os.path.expanduser(value))

    return False

# ── Проверки DaVinci Resolve ──────────────────────────────────────────────────

def is_davinci_installed() -> bool:
    binary_exists = os.path.exists("/opt/resolve/bin/resolve")
    rpm_installed = subprocess.run(
        ["rpm", "-q", "davinci-resolve"],
        capture_output=True,
    ).returncode == 0
    return binary_exists or rpm_installed


def is_aac_installed() -> bool:
    return os.path.exists("/opt/resolve/IOPlugins/aac_encoder_plugin.dvcp.bundle")


def is_fairlight_installed() -> bool:
    return subprocess.run(
        ["rpm", "-q", "alsa-plugins-pulse"],
        capture_output=True,
    ).returncode == 0

def install_aac_codec(archive_path: str, on_line: OnLine, on_done: OnDone) -> None:
    """Распаковывает и копирует AAC-кодек в /opt/resolve/IOPlugins/."""
    cmd = [
        "bash",
        "-c",
        (
            f"tar xzf '{archive_path}' -C /tmp && "
            "cp -r /tmp/aac_encoder_plugin.dvcp.bundle /opt/resolve/IOPlugins/"
        ),
    ]
    run_privileged(cmd, on_line, on_done)
