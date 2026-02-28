"""
privileges.py — Повышение привилегий и работа с sudo.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import threading
import time
from typing import Callable, Sequence

from gi.repository import GLib

import config

# ── Типы ──────────────────────────────────────────────────────────────────────

OnLine = Callable[[str], None]
OnDone = Callable[[bool], None]

# ── Пароль sudo ──────────────────────────────────────────────────────────────

_sudo_password: str | None = None
_sudo_lock = threading.Lock()


def set_sudo_password(pw: str) -> None:
    """Сохраняет sudo-пароль в памяти процесса."""
    global _sudo_password
    with _sudo_lock:
        _sudo_password = pw

def get_sudo_password() -> str:
    """Возвращает сохранённый sudo-пароль."""
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
    for lock_file in config.APT_LOCK_FILES:
        if not os.path.exists(lock_file):
            continue
        if subprocess.run(["fuser", lock_file], capture_output=True).returncode == 0:
            return True
    return False

def _wait_for_apt_lock(on_line: OnLine | None = None, timeout: int = 60) -> bool:
    """Ждёт освобождения APT-блокировки (проверка каждые 5 секунд)."""
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
    """Запускает команду через sudo -S."""
    
    if cmd and cmd[0] in ("epm", "epmi"):
        cmd = [
            "bash", "-c",
            "if ! rpm -q eepm >/dev/null 2>&1; then echo -e '▶ EPM не найден. Выполняется установка eepm...\\n'; apt-get install -y eepm; fi && \"$@\"",
            "--", *cmd
        ]

    def _worker() -> None:
        password = get_sudo_password()

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

# ── EPM (sudo -A + SUDO_ASKPASS) ──────────────────────────────────────────────

def run_epm(cmd: Sequence[str], on_line: OnLine, on_done: OnDone) -> None:
    """Запускает epm через sudo -A."""

    if cmd and cmd[0] in ("epm", "epmi"):
        cmd = [
            "bash", "-c",
            "if ! rpm -q eepm >/dev/null 2>&1; then echo -e '▶ EPM не найден. Выполняется установка eepm...\\n'; apt-get install -y eepm; fi && \"$@\"",
            "--", *cmd
        ]

    def _worker() -> None:
        password = get_sudo_password()
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
