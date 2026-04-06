from __future__ import annotations

import os
import re
import shlex
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

_SAFE_CMDS: frozenset[str] = frozenset({
    # Нужен для packages.get_install_preview → env LC_ALL=C apt-get -s …
    "env",
    "apt", "apt-get",
    "epm", "epmi",
    "flatpak",
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
    # /usr/sbin/control (ALT): tabs/setup.py run_privileged при отключении sudo
    "control",
})

_INTERNAL_CMDS: frozenset[str] = frozenset({
    "bash",
    "sh",
    "npm",
    "git",
    "tar",
    "find",
})

_CMD_WHITELIST: frozenset[str] = _SAFE_CMDS | _INTERNAL_CMDS

_BLOCKED_RM_PATHS: frozenset[str] = frozenset({
    "/", "/home", "/etc", "/boot", "/usr", "/var",
    "/lib", "/lib64", "/bin", "/sbin", "/proc", "/sys",
    "/run", "/dev", "/tmp",
})


def _check_args(cmd: Sequence[str]) -> str | None:
    if not cmd:
        return None
    name, args = cmd[0], list(cmd[1:])

    if name == "env":
        # Only allow "env LC_ALL=C <argv...>" for reproducible apt-get -s output.
        if len(args) < 2 or args[0] != "LC_ALL=C":
            return "env: разрешён только префикс LC_ALL=C для следующей команды"

    if name == "control":
        if len(args) < 2 or args[0] != "sudowheel":
            return "control: разрешена только субкоманда sudowheel"

    if name == "rm":
        if "--no-preserve-root" in args:
            return "rm: --no-preserve-root запрещён"
        for arg in args:
            if not arg.startswith("-") and os.path.normpath(arg) in _BLOCKED_RM_PATHS:
                return f"rm: цель {arg!r} защищена"

    elif name == "find":
        for flag in ("-exec", "-execdir", "-delete", "-ok", "-okdir"):
            if flag in args:
                return f"find: флаг {flag!r} запрещён"

    elif name == "tar":
        for arg in args:
            for flag in ("--to-command", "--use-compress-program", "--checkpoint-action"):
                if arg == flag or arg.startswith(f"{flag}="):
                    return f"tar: {flag!r} запрещён"

    elif name == "git":
        if args and args[0] == "config":
            return "git: субкоманда 'config' запрещена"
        if any(a == "--template" or a.startswith("--template=") for a in args):
            return "git: --template запрещён"

    elif name == "npm":
        blocked = {"run", "exec", "x", "start", "test", "publish", "pack"}
        if args and args[0] in blocked:
            return f"npm: субкоманда {args[0]!r} запрещена"

    elif name == "install":
        for i, arg in enumerate(args):
            mode_str: str | None = None
            if arg in ("-m", "--mode") and i + 1 < len(args):
                mode_str = args[i + 1]
            elif arg.startswith("-m") and len(arg) > 2:
                mode_str = arg[2:]
            if mode_str is not None:
                try:
                    if int(mode_str, 8) & 0o6000:
                        return f"install: режим {mode_str!r} содержит SUID/SGID биты"
                except ValueError:
                    pass

    return None

_current_proc: subprocess.Popen | None = None
_current_proc_lock = threading.Lock()


def _stdbuf_line_prefix() -> str:
    """Префикс line-buffer для вывода в pkexec bash. PATH у root может не содержать stdbuf из user PATH."""
    for p in ("/usr/bin/stdbuf", "/bin/stdbuf"):
        if os.path.isfile(p):
            return f"{p} -oL "
    return ""


_pkexec_shell_proc: subprocess.Popen | None = None
_pkexec_shell_lock = threading.Lock()
_pkexec_io_lock = threading.Lock()

_polkit_agent_proc: subprocess.Popen | None = None

_POLKIT_AGENTS = [
    "/usr/libexec/polkit-1/polkit-gnome-authentication-agent-1",
    "/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1",
    "/usr/libexec/polkit-gnome-authentication-agent-1",
    "/usr/lib/x86_64-linux-gnu/polkit-gnome-authentication-agent-1",
    "/usr/lib/aarch64-linux-gnu/polkit-gnome-authentication-agent-1",
    "/usr/lib/xfce4/polkit-xfce-authentication-agent-1",
    "/usr/libexec/xfce-polkit",
    "lxpolkit",
    "/usr/lib/lxqt-policykit/lxqt-policykit-agent",
    "/usr/libexec/kf6/polkit-kde-authentication-agent-1",
    "/usr/libexec/polkit-kde-authentication-agent-1",
]


def _is_polkit_agent_running() -> bool:
    """Проверяет, зарегистрирован ли polkit authentication agent через D-Bus."""
    try:
        result = subprocess.run(
            [
                "busctl", "--user", "list", "--no-pager",
            ],
            capture_output=True, text=True, timeout=3,
        )
        return "org.freedesktop.PolicyKit1.AuthenticationAgent" in result.stdout
    except Exception:
        pass
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.DBus",
                "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.ListNames",
            ],
            capture_output=True, text=True, timeout=3,
        )
        return "org.freedesktop.PolicyKit1.AuthenticationAgent" in result.stdout
    except Exception:
        return False


def try_start_polkit_agent() -> bool:
    """Пробует запустить один из известных polkit агентов. Возвращает True если агент стартовал."""
    global _polkit_agent_proc

    if _is_polkit_agent_running():
        return True

    for agent_path in _POLKIT_AGENTS:
        exe = agent_path if os.path.isabs(agent_path) else None
        if exe is None:
            import shutil as _shutil
            exe = _shutil.which(agent_path)
        if exe and os.path.isfile(exe):
            try:
                _polkit_agent_proc = subprocess.Popen(
                    [exe],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Дать агенту время зарегистрироваться в D-Bus
                for _ in range(10):
                    time.sleep(0.3)
                    if _is_polkit_agent_running():
                        return True
            except Exception:
                config.log_exception(f"try_start_polkit_agent: failed to start {exe!r}")
    return False


def _create_and_verify_shell() -> subprocess.Popen | None:
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
        _pkexec_shell_proc = _create_and_verify_shell()

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
            _pkexec_shell_proc = proc
            return True, False

        # pkexec не смог запустить диалог — попробуем запустить polkit agent
        agent_started = try_start_polkit_agent()
        if agent_started:
            proc = _create_and_verify_shell()
            if proc is not None:
                _pkexec_shell_proc = proc
                return True, False

        _pkexec_shell_proc = None
        return False, False


def _is_apt_locked() -> bool:
    for lock_file in config.APT_LOCK_FILES:
        if not os.path.exists(lock_file):
            continue
        try:
            if subprocess.run(["fuser", lock_file], capture_output=True, timeout=5).returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
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

    stdbuf_run = _stdbuf_line_prefix().rstrip()
    tail = f'{stdbuf_run} \"$@\"' if stdbuf_run else "\"$@\""
    script = (
        "if ! rpm -q eepm >/dev/null 2>&1; then "
        "echo -e '▶ EPM не найден. Выполняется установка eepm...\\n'; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get install -y eepm; "
        f"fi && {tail}"
    )
    return ["bash", "-c", script, "--", *cmd]

def _run_pkexec(cmd: Sequence[str], on_line: OnLine | None, on_done: OnDone, *, trusted: bool = True) -> None:
    def _emit(line: str) -> None:
        if on_line is not None:
            GLib.idle_add(on_line, line)

    def _worker() -> None:
        success = False
        try:
            if not cmd:
                GLib.idle_add(on_done, False)
                return
            if cmd[0] not in _CMD_WHITELIST:
                _emit(f"⛔  Команда отклонена (не в whitelist): {cmd[0]!r}\n")
                config.log_exception(f"_run_pkexec: rejected command {cmd[0]!r}")
                GLib.idle_add(on_done, False)
                return
            if not trusted and cmd[0] in _INTERNAL_CMDS:
                _emit(
                    f"Операция заблокирована из соображений безопасности: запуск '{cmd[0]}' "
                    f"с правами администратора разрешён только встроенным функциям программы, "
                    f"но не командам из пользовательской конфигурации. "
                    f"Проверьте команду установки в настройках приложения.\n"
                )
                config.log_exception(f"_run_pkexec: untrusted call to internal command {cmd[0]!r}")
                GLib.idle_add(on_done, False)
                return
            arg_error = _check_args(cmd)
            if arg_error:
                _emit(f"⛔  {arg_error}\n")
                config.log_exception(f"_run_pkexec: blocked args: {arg_error}")
                GLib.idle_add(on_done, False)
                return

            check_lock = False
            if cmd[0] in ("apt", "apt-get", "flatpak", "epm", "epmi"):
                check_lock = True
            elif (
                cmd[0] == "env"
                and len(cmd) >= 4
                and cmd[1] == "LC_ALL=C"
                and cmd[2] in ("apt", "apt-get", "flatpak", "epm", "epmi")
            ):
                check_lock = True
            elif cmd[0] == "bash" and len(cmd) >= 3:
                if "apt-get" in cmd[2] or "epm" in cmd[2] or "flatpak" in cmd[2]:
                    check_lock = True
            if check_lock and not _wait_for_apt_lock(on_line):
                _emit("⚠  Пакетный менеджер занят, операция отменена.\n")
                GLib.idle_add(on_done, False)
                return

            with _pkexec_shell_lock:
                proc = _get_pkexec_shell()
                if not proc or proc.poll() is not None:
                    _emit("⚠  Root-сессия не активна (pkexec).\n")
                    GLib.idle_add(on_done, False)
                    return

            marker = f"__AB_EXIT__{uuid.uuid4().hex}__"
            exit_line_re = re.compile(rf"^{re.escape(marker)}\s+(-?\d+)\s*$")
            cmd_str = shlex.join(cmd)
            stdbuf_p = _stdbuf_line_prefix()
            script = (
                f"({stdbuf_p}{cmd_str}) 2>&1; "
                f"printf '%s %s\\n' '{marker}' \"$?\"\n"
            )

            with _pkexec_io_lock:
                if proc.poll() is not None:
                    _emit("⚠  Root-сессия была отменена.\n")
                    GLib.idle_add(on_done, False)
                    return
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
        except Exception:
            config.log_exception("_run_pkexec: unexpected exception in _worker")
            _emit("⚠  Внутренняя ошибка при выполнении команды. Смотрите лог.\n")
            GLib.idle_add(on_done, False)

    threading.Thread(target=_worker, daemon=True).start()


def run_privileged(cmd: Sequence[str], on_line: OnLine | None, on_done: OnDone, *, trusted: bool = True) -> None:
    _run_pkexec(cmd, on_line, on_done, trusted=trusted)

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
