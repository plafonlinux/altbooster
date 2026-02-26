"""
backend.py — системные команды, проверки и привилегированное выполнение.
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
    """Запускает epm через sudo -A."""
    
    if cmd and cmd[0] in ("epm", "epmi"):
        cmd = [
            "bash", "-c",
            "if ! rpm -q eepm >/dev/null 2>&1; then echo -e '▶ EPM не найден. Выполняется установка eepm...\\n'; apt-get install -y eepm; fi && \"$@\"",
            "--", *cmd
        ]
    
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
    """Проверяет статус sudo. В ALT Linux 'control' требует прав root для чтения /etc/sudoers."""
    
    # Способ 1: Проверка через sudo -n (неинтерактивно)
    # Если в системе действует кэш sudo (недавно вводили пароль), это сработает мгновенно
    try:
        if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=1).returncode == 0:
            return True
    except:
        pass

    # Способ 2: Если пароль уже введен в приложении, проверяем статус через него
    password = _get_sudo_password()
    if password:
        try:
            res = subprocess.run(
                ["sudo", "-S", "/usr/sbin/control", "sudowheel"],
                input=password + "\n",
                capture_output=True,
                text=True,
                timeout=2
            )
            out = (res.stdout + res.stderr).lower()
            if "enabled" in out or "wheelonly" in out:
                return True
        except:
            pass

    # Способ 3: Проверка членства текущего пользователя в группе wheel
    # Это основной индикатор возможности использования sudo в ALT Linux
    try:
        import grp
        import getpass
        user = getpass.getuser()
        group_members = grp.getgrnam("wheel").gr_mem
        if user in group_members:
            return True
    except:
        pass

    return False

def is_flathub_enabled() -> bool:
    result = subprocess.run(["flatpak", "remotes"], capture_output=True, text=True)
    return "flathub" in result.stdout.lower()

def is_fstrim_enabled() -> bool:
    result = subprocess.run(["systemctl", "is-enabled", "fstrim.timer"], capture_output=True)
    return result.returncode == 0

def is_fractional_scaling_enabled() -> bool:
    value = gsettings_get("org.gnome.mutter", "experimental-features")
    return "scale-monitor-framebuffer" in value

def is_system_busy() -> bool:
    """Проверяет занятость пакетного менеджера."""
    try:
        if subprocess.run(["pgrep", "-f", "packagekitd"], capture_output=True).returncode == 0:
            return True
        for lock_file in APT_LOCK_FILES:
            if os.path.exists(lock_file) and subprocess.run(["fuser", lock_file], capture_output=True).returncode == 0:
                return True
    except OSError:
        pass
    return False

def check_app_installed(source: dict) -> bool:
    kind, value = source["check"]
    if kind == "flatpak":
        res = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True)
        return value in res.stdout
    if kind == "rpm":
        return subprocess.run(["rpm", "-q", value], capture_output=True).returncode == 0
    if kind == "path":
        return os.path.exists(os.path.expanduser(value))
    return False

# ── Настройки накопителей и ФМ ──────────────────────────────────────────────

def is_vm_dirty_optimized() -> bool:
    try:
        content = Path("/etc/sysctl.d/90-dirty.conf").read_text(encoding="utf-8")
        return "67108864" in content
    except OSError:
        return False

def apply_vm_dirty(on_log, on_done) -> None:
    cmd = ["bash", "-c", "echo -e 'vm.dirty_bytes = 67108864\\nvm.dirty_background_bytes = 16777216' > /etc/sysctl.d/90-dirty.conf && sysctl -p /etc/sysctl.d/90-dirty.conf"]
    run_privileged(cmd, on_log, on_done)

def is_drive_menu_patched() -> bool:
    try:
        ext_path = "/usr/share/gnome-shell/extensions/drive-menu@gnome-shell-extensions.gcampax.github.com/extension.js"
        content = Path(ext_path).read_text(encoding="utf-8")
        return "GLib.timeout_add_seconds" in content
    except OSError:
        return False

def patch_drive_menu(on_log, on_done) -> None:
    """Внедряет задержку 5 сек в extension.js (с восстановлением прав доступа)."""
    script = """
FILE="/usr/share/gnome-shell/extensions/drive-menu@gnome-shell-extensions.gcampax.github.com/extension.js"
if [ ! -f "$FILE" ]; then exit 1; fi

# Принудительно возвращаем права на чтение, если прошлый sed -i их сломал
chmod 644 "$FILE"

# Если уже пропатчено ранее
if grep -q "GLib.timeout_add_seconds" "$FILE"; then exit 0; fi

# Делаем бекап
cp "$FILE" "$FILE.bak"

# Применяем патч
sed -i 's/this._indicator = new DriveMenu();/this._delayId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => { this._indicator = new DriveMenu(); Main.panel.addToStatusArea("drive-menu", this._indicator); return GLib.SOURCE_REMOVE; });/' "$FILE"
sed -i '/Main.panel.addToStatusArea/d' "$FILE"
sed -i '/if (this._indicator) {/i \\        if (this._delayId) { GLib.Source.remove(this._delayId); this._delayId = null; }' "$FILE"

# Снова восстанавливаем права для нового файла
chmod 644 "$FILE"

# Проверяем успешность
if grep -q "GLib.timeout_add_seconds" "$FILE"; then
    exit 0
else
    mv "$FILE.bak" "$FILE"
    chmod 644 "$FILE"
    exit 1
fi
"""
    run_privileged(["bash", "-c", script], on_log, on_done)

# ── Прочее ───────────────────────────────────────────────────────────────────

def is_journal_optimized() -> bool:
    paths = ["/etc/systemd/journald.conf", "/etc/systemd/journald.conf.d/99-altbooster.conf"]
    for p in paths:
        try:
            if "SystemMaxUse=100M" in Path(p).read_text(encoding="utf-8"): return True
        except OSError: continue
    return False

def is_davinci_installed() -> bool:
    return os.path.exists("/opt/resolve/bin/resolve") or subprocess.run(["rpm", "-q", "davinci-resolve"], capture_output=True).returncode == 0

def is_aac_installed() -> bool:
    return os.path.exists("/opt/resolve/IOPlugins/aac_encoder_plugin.dvcp.bundle")

def is_fairlight_installed() -> bool:
    return subprocess.run(["rpm", "-q", "alsa-plugins-pulse"], capture_output=True).returncode == 0

def install_aac_codec(archive_path: str, on_line: OnLine, on_done: OnDone) -> None:
    cmd = ["bash", "-c", f"tar xzf '{archive_path}' -C /tmp && cp -r /tmp/aac_encoder_plugin.dvcp.bundle /opt/resolve/IOPlugins/"]
    run_privileged(cmd, on_line, on_done)
    
def is_epm_installed() -> bool:
    """Проверяет, установлен ли пакетный менеджер eepm."""
    return subprocess.run(["rpm", "-q", "eepm"], capture_output=True).returncode == 0
