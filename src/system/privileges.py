"""
privileges.py — Повышение привилегий и работа с sudo.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Callable, Sequence

from gi.repository import GLib

import config

# ── Типы ──────────────────────────────────────────────────────────────────────

OnLine = Callable[[str], None]
OnDone = Callable[[bool], None]

# ── Пароль sudo ──────────────────────────────────────────────────────────────

_sudo_password: str | None = None
_sudo_lock = threading.Lock()
_sudo_nopass: bool = False  # True когда sudo работает без пароля (NOPASSWD / кэш)


def set_sudo_password(pw: str) -> None:
    """Сохраняет sudo-пароль в памяти процесса."""
    global _sudo_password
    with _sudo_lock:
        _sudo_password = pw

def get_sudo_password() -> str:
    """Возвращает сохранённый sudo-пароль."""
    with _sudo_lock:
        return _sudo_password or ""

def set_sudo_nopass(enabled: bool) -> None:
    """Переключает режим sudo без пароля (для NOPASSWD или кэшированной сессии)."""
    global _sudo_nopass
    _sudo_nopass = enabled

# ── pkexec режим ──────────────────────────────────────────────────────────────

_use_pkexec: bool = False
_pkexec_shell_proc: subprocess.Popen | None = None
_pkexec_shell_lock = threading.Lock()

def _get_pkexec_shell() -> subprocess.Popen | None:
    """Возвращает (или запускает) долгоживущий root-шелл через pkexec."""
    global _pkexec_shell_proc
    
    # Если процесс мертв, сбрасываем переменную
    if _pkexec_shell_proc and _pkexec_shell_proc.poll() is not None:
        _pkexec_shell_proc = None

    if _pkexec_shell_proc is None:
        try:
            # Запускаем bash через pkexec. Это вызовет диалог пароля ОДИН раз.
            _pkexec_shell_proc = subprocess.Popen(
                ["pkexec", "bash"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, # Объединяем stderr с stdout для простоты
                text=True,
                bufsize=0 # Без буферизации
            )
        except Exception:
            return None
            
    return _pkexec_shell_proc

def set_pkexec_mode(enabled: bool) -> None:
    """Переключает на pkexec вместо sudo -S (когда sudo не настроен)."""
    global _use_pkexec
    _use_pkexec = enabled

def start_pkexec_shell() -> tuple[bool, bool]:
    """Запускает и верифицирует постоянный pkexec bash-шелл.

    Вызывается из диалога подтверждения pkexec. Блокирует вызывающий поток
    до тех пор, пока пользователь не ответит на диалог polkit (или не истечёт
    60-секундный таймаут).

    Возвращает:
        (ok, is_cancel)
        ok        — шелл запущен и ответил на тест
        is_cancel — пользователь закрыл диалог polkit (returncode == 126)
    """
    global _pkexec_shell_proc

    with _pkexec_shell_lock:
        # Шелл уже работает — ничего не нужно делать
        if _pkexec_shell_proc and _pkexec_shell_proc.poll() is None:
            return True, False

        try:
            proc = subprocess.Popen(
                ["pkexec", "bash"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,
            )
        except Exception:
            return False, False

        # Отправляем тестовую команду с уникальным маркером
        test_marker = f"---READY-{uuid.uuid4()}---"
        try:
            proc.stdin.write(f'echo "{test_marker}"\n')
            proc.stdin.flush()
        except OSError:
            proc.terminate()
            return False, False

        # Читаем stdout в отдельном потоке, чтобы задать жёсткий таймаут
        found: list[bool | None] = [None]

        def _reader() -> None:
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:            # EOF — процесс завершился
                        found[0] = False
                        return
                    if test_marker in line:
                        found[0] = True
                        return
            except Exception:
                found[0] = False

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        reader.join(timeout=60)

        if found[0] is True:
            _pkexec_shell_proc = proc
            return True, False

        # Таймаут или ошибка — завершаем процесс
        is_cancel = False
        if proc.poll() is not None:
            is_cancel = (proc.returncode == 126)
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        return False, is_cancel

def sudo_check(pw: str) -> bool:
    """Проверяет корректность sudo-пароля."""
    try:
        result = subprocess.run(
            ["sudo", "-k", "-S", "id", "-u"],
            input=pw + "\n",
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "0"
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

# ── Фильтр предупреждений APT о множественных версиях ────────────────────────

def _apt_dedup_filter(on_line: OnLine) -> OnLine:
    """Подавляет многострочные блоки 'W: There are multiple versions of...'."""
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
    in_warn = [False]

    def _filtered(line: str) -> None:
        if "There are multiple versions of" in line:
            in_warn[0] = True
        if in_warn[0]:
            if not line.strip():
                return
            if any(pat in line for pat in _WARN_PATTERNS):
                return
            in_warn[0] = False
        on_line(line)

    return _filtered


# ── Выполнение привилегированных команд ───────────────────────────────────────

def _wrap_epm_auto_install(cmd: Sequence[str]) -> Sequence[str]:
    """Оборачивает команду epm в скрипт авто-установки eepm."""
    if not cmd or cmd[0] not in ("epm", "epmi"):
        return cmd
    
    script = (
        "if ! rpm -q eepm >/dev/null 2>&1; then "
        "echo -e '▶ EPM не найден. Выполняется установка eepm...\\n'; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get install -y eepm; "
        "fi && \"$@\""
    )
    return ["bash", "-c", script, "--", *cmd]

def _run_pkexec(cmd: Sequence[str], on_line: OnLine, on_done: OnDone) -> None:
    """Запускает команду через pkexec (polkit). Пароль не нужен — спрашивает polkit."""
    def _worker() -> None:
        # Проверяем блокировку APT, если команда похожа на пакетный менеджер
        check_lock = False
        if cmd:
            if cmd[0] in ("apt", "apt-get", "flatpak", "epm", "epmi"):
                check_lock = True
            elif cmd[0] == "bash" and len(cmd) >= 3:
                # Эвристика для обернутых команд
                if "apt-get" in cmd[2] or "epm" in cmd[2] or "flatpak" in cmd[2]:
                    check_lock = True
        if check_lock:
            _wait_for_apt_lock(on_line)

        # Используем глобальную блокировку, чтобы команды не перемешались в шелле
        with _pkexec_shell_lock:
            proc = _get_pkexec_shell()
            
            # Если не удалось запустить (например, отмена пароля)
            if not proc or proc.poll() is not None:
                GLib.idle_add(on_line, "⚠  Root-сессия не активна (pkexec).\n")
                GLib.idle_add(on_done, False)
                return

            # Формируем команду с маркером окончания
            delimiter = f"---END-{uuid.uuid4()}---"
            cmd_str = shlex.join(cmd)
            # stdbuf -oL форсирует строчную буферизацию stdout дочерней команды,
            # иначе apt/epm внутри пайпа буферизуют вывод блоками → прогресс
            # появляется "пачками", а не построчно.
            script = f"(stdbuf -oL {cmd_str}) 2>&1; echo \"{delimiter} $?\"\n"

            success = False
            try:
                if proc.stdin:
                    proc.stdin.write(script)
                    proc.stdin.flush()
                
                # Читаем вывод до маркера
                if proc.stdout:
                    while True:
                        line = proc.stdout.readline()
                        if not line: break # EOF — шелл упал
                        
                        if delimiter in line:
                            # Парсим код возврата: "---END-uuid--- 0"
                            try:
                                code = int(line.strip().split(" ")[-1])
                                success = (code == 0)
                            except (ValueError, IndexError):
                                success = False
                            break
                        
                        GLib.idle_add(on_line, line)
            except (BrokenPipeError, OSError):
                # Шелл умер (например, был убит)
                GLib.idle_add(on_line, "⚠  Root-сессия была прервана.\n")
            
            GLib.idle_add(on_done, success)

    threading.Thread(target=_worker, daemon=True).start()


def run_privileged(cmd: Sequence[str], on_line: OnLine, on_done: OnDone) -> None:
    """Запускает команду через sudo -S или pkexec (если sudo не настроен)."""

    if _use_pkexec:
        _run_pkexec(cmd, on_line, on_done)
        return

    # Фильтруем вывод apt и epm (до того как cmd превратится в bash-скрипт)
    if cmd and cmd[0] in ("apt", "apt-get", "epm", "epmi"):
        on_line = _apt_dedup_filter(on_line)

    cmd = _wrap_epm_auto_install(cmd)

    def _worker() -> None:
        password = get_sudo_password()

        check_lock = False
        if cmd:
            if cmd[0] in ("apt", "apt-get", "flatpak"):
                check_lock = True
            elif cmd[0] == "bash" and len(cmd) >= 3:
                if "apt-get" in cmd[2] or "epm" in cmd[2] or "flatpak" in cmd[2]:
                    check_lock = True
        if check_lock:
            _wait_for_apt_lock(on_line)

        if _sudo_nopass:
            proc = subprocess.Popen(
                ["sudo", "-n", *cmd],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        else:
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

    cmd = _wrap_epm_auto_install(cmd)

    on_line = _apt_dedup_filter(on_line)

    if _use_pkexec:
        _run_pkexec(cmd, on_line, on_done)
        return

    def _worker() -> None:
        _wait_for_apt_lock(on_line)

        askpass_path: str | None = None
        try:
            if _sudo_nopass:
                proc = subprocess.Popen(
                    ["sudo", "-n", *cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                )
            else:
                password = get_sudo_password()
                env = os.environ.copy()
                fd, askpass_path = tempfile.mkstemp(suffix=".sh")
                with os.fdopen(fd, "w") as script:
                    script.write("#!/bin/sh\n")
                    script.write(f"printf '%s\\n' {shlex.quote(password)}\n")
                # Устанавливаем права 0700 (rwx------)
                os.chmod(askpass_path, stat.S_IRWXU)
                env["SUDO_ASKPASS"] = askpass_path
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
            if askpass_path:
                try:
                    os.unlink(askpass_path)
                except OSError:
                    pass

    threading.Thread(target=_worker, daemon=True).start()
