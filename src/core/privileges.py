from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from typing import Callable, Sequence

from gi.repository import GLib

import traceback

from core import config

OnLine = Callable[[str], None]
OnDone = Callable[[bool], None]

_CMD_WHITELIST: frozenset[str] = frozenset({
    "apt", "apt-get",
    "epm", "epmi",
    "flatpak",
    "bash",
    "sh",
    "systemctl",
    "btrfs",
    "rsync",
    "mount",
    "umount",
    "chsh",
    "reboot",
    "rm",
    "chmod",
    "chown",
    "mkdir",
    "cp",
    "mv",
    "install",
    "sysctl",
    "patch",
    "borg",
    "gtk-update-icon-cache",
    "update-desktop-database",
    "journalctl",
    "fstrim",
    "npm",
    "git",
    "tar",
    "find",
})

_current_proc: subprocess.Popen | None = None
_current_proc_lock = threading.Lock()

_stdbuf: list[str] = ["stdbuf", "-oL"] if shutil.which("stdbuf") else []

_pkexec_shell_proc: subprocess.Popen | None = None
_pkexec_shell_lock = threading.Lock()


def _create_and_verify_shell() -> subprocess.Popen | None:
    global _pkexec_shell_proc
    try:
        proc = subprocess.Popen(
            ["pkexec", "bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception:
        config.log_exception("_create_and_verify_shell: Popen")
        return None

    test_marker = f"---READY-{uuid.uuid4()}---"
    try:
        proc.stdin.write(f'echo "{test_marker}"\n')
        proc.stdin.flush()
    except OSError:
        proc.terminate()
        return None

    found: list[bool | None] = [None]

    def _reader() -> None:
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    found[0] = False
                    return
                if test_marker in line:
                    found[0] = True
                    return
        except Exception:
            config.log_exception("_create_and_verify_shell: reader")
            found[0] = False

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    reader.join(timeout=60)

    if found[0] is True:
        _pkexec_shell_proc = proc
        return proc

    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return None


def _get_pkexec_shell() -> subprocess.Popen | None:
    global _pkexec_shell_proc

    if _pkexec_shell_proc and _pkexec_shell_proc.poll() is not None:
        _pkexec_shell_proc = None

    if _pkexec_shell_proc is None:
        return _create_and_verify_shell()

    return _pkexec_shell_proc


def cancel_current() -> None:
    global _pkexec_shell_proc
    with _pkexec_shell_lock:
        proc = _pkexec_shell_proc
        _pkexec_shell_proc = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def start_pkexec_shell() -> tuple[bool, bool]:
    global _pkexec_shell_proc

    with _pkexec_shell_lock:
        if _pkexec_shell_proc and _pkexec_shell_proc.poll() is None:
            return True, False

        proc = _create_and_verify_shell()
        if proc is not None:
            return True, False
        return False, False


def _is_apt_locked() -> bool:
    for lock_file in config.APT_LOCK_FILES:
        if not os.path.exists(lock_file):
            continue
        if subprocess.run(["fuser", lock_file], capture_output=True, timeout=5).returncode == 0:
            return True
    return False

def _wait_for_apt_lock(on_line: OnLine | None = None, timeout: int = 60) -> bool:
    for attempt in range(timeout // 5):
        if not _is_apt_locked():
            return True
        if on_line:
            if attempt == 0:
                GLib.idle_add(
                    on_line,
                    "⏳ Пакетный менеджер занят другим процессом (возможно, GNOME Software или PackageKit обновляет базу в фоне). Ожидание освобождения...\n",
                )
            else:
                GLib.idle_add(
                    on_line,
                    f"⏳ Ожидание... ({attempt * 5} с)\n",
                )
        time.sleep(5)
    return False

def _apt_dedup_filter(on_line: OnLine) -> OnLine:
    _WARN_PATTERNS = (
        "There are multiple versions of",
        "won't be cleanly updated",
        "only one version",
        "To leave multiple versions installed",
        "you may remove that warning",
        "option in your configuration file",
        "RPM:",
        "To disable these warnings completely set",
        "You may want to run apt-get update to correct",
        "В Вашей системе установлено несколько версий пакета",
        "Этот пакет не может быть обновлён обычным путём",
        "оставите только одну его версию",
        "Чтобы оставить установленными несколько версий",
    )
    in_warn = False

    def _filtered(line: str) -> None:
        nonlocal in_warn
        if "There are multiple versions of" in line:
            in_warn = True
        if in_warn:
            if not line.strip():
                in_warn = False
                return
            if any(pat in line for pat in _WARN_PATTERNS):
                return
            in_warn = False
        on_line(line)

    return _filtered


def _wrap_epm_auto_install(cmd: Sequence[str]) -> Sequence[str]:
    if not cmd or cmd[0] not in ("epm", "epmi"):
        return cmd

    script = (
        "if ! rpm -q eepm >/dev/null 2>&1; then "
        "echo -e '▶ EPM не найден. Выполняется установка eepm...\\n'; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get install -y eepm; "
        "fi && stdbuf -oL \"$@\""
    )
    return ["bash", "-c", script, "--", *cmd]

def _run_pkexec(cmd: Sequence[str], on_line: OnLine | None, on_done: OnDone) -> None:
    def _emit(line: str) -> None:
        if on_line is not None:
            GLib.idle_add(on_line, line)

    def _worker() -> None:
        if not cmd:
            GLib.idle_add(on_done, False)
            return
        if cmd[0] not in _CMD_WHITELIST:
            msg = f"⛔  Команда отклонена (не в whitelist): {cmd[0]!r}\n"
            _emit(msg)
            config.log_exception(f"_run_pkexec: rejected command {cmd[0]!r}")
            GLib.idle_add(on_done, False)
            return

        check_lock = False
        if cmd[0] in ("apt", "apt-get", "flatpak", "epm", "epmi"):
            check_lock = True
        elif cmd[0] == "bash" and len(cmd) >= 3:
            if "apt-get" in cmd[2] or "epm" in cmd[2] or "flatpak" in cmd[2]:
                check_lock = True
        if check_lock:
            _wait_for_apt_lock(on_line)

        with _pkexec_shell_lock:
            proc = _get_pkexec_shell()

            if not proc or proc.poll() is not None:
                _emit("⚠  Root-сессия не активна (pkexec).\n")
                GLib.idle_add(on_done, False)
                return

            marker = f"__AB_EXIT__{uuid.uuid4().hex}__"
            exit_line_re = re.compile(rf"^{re.escape(marker)}\s+(-?\d+)\s*$")
            cmd_str = shlex.join(cmd)
            script = (
                f"(stdbuf -oL {cmd_str}) 2>&1; "
                f"printf '%s %s\\n' '{marker}' \"$?\"\n"
            )

            success = False
            try:
                if proc.stdin:
                    proc.stdin.write(script)
                    proc.stdin.flush()

                if proc.stdout:
                    while True:
                        line = proc.stdout.readline()
                        if not line:
                            break

                        stripped = line.rstrip("\r\n")
                        m = exit_line_re.match(stripped)
                        if m:
                            try:
                                success = int(m.group(1)) == 0
                            except ValueError:
                                success = False
                            break

                        _emit(line)
            except (BrokenPipeError, OSError):
                _emit("⚠  Root-сессия была прервана.\n")

            GLib.idle_add(on_done, success)

    threading.Thread(target=_worker, daemon=True).start()


def run_privileged(cmd: Sequence[str], on_line: OnLine | None, on_done: OnDone) -> None:
    _run_pkexec(cmd, on_line, on_done)

def _sync_wrapper(async_fn, cmd: Sequence[str], on_line: OnLine | None, timeout: int = 300) -> bool:
    if threading.current_thread() is threading.main_thread():
        raise RuntimeError(
            "_sync_wrapper called from main thread — would deadlock "
            "because on_done is dispatched via GLib.idle_add"
        )
    event = threading.Event()
    result = False

    def _done(ok: bool) -> None:
        nonlocal result
        result = ok
        event.set()

    async_fn(cmd, on_line, _done)
    event.wait(timeout=timeout)
    return result

def run_privileged_sync(cmd: Sequence[str], on_line: OnLine | None) -> bool:
    return _sync_wrapper(run_privileged, cmd, on_line)

def run_epm_sync(cmd: Sequence[str], on_line: OnLine) -> bool:
    return _sync_wrapper(run_epm, cmd, on_line)

def run_epm(cmd: Sequence[str], on_line: OnLine, on_done: OnDone) -> None:
    cmd = _wrap_epm_auto_install(cmd)
    on_line = _apt_dedup_filter(on_line)
    _run_pkexec(cmd, on_line, on_done)
