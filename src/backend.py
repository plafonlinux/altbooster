import subprocess
import threading
import os
from gi.repository import GLib

_sudo_password: str | None = None

def set_sudo_password(pw: str):
    global _sudo_password
    _sudo_password = pw

def sudo_check(pw: str) -> bool:
    r = subprocess.run(["sudo", "-S", "-v"], input=pw+"\n", capture_output=True, text=True)
    return r.returncode == 0

def _wait_for_apt_lock(on_line=None, timeout=60):
    """Ждёт освобождения APT-блокировки."""
    import time
    lock_files = ["/var/cache/apt/archives/lock", "/var/lib/dpkg/lock-frontend", "/var/lib/apt/lists/lock"]
    for attempt in range(timeout // 5):
        locked = any(
            subprocess.run(["fuser", lf], capture_output=True).returncode == 0
            for lf in lock_files if os.path.exists(lf)
        )
        if not locked:
            return True
        if on_line:
            GLib.idle_add(on_line, f"⏳ APT занят другим процессом, ожидание... ({attempt+1})\n")
        time.sleep(5)
    return False

def run_privileged(cmd: list, on_line, on_done):
    def _w():
        pw = _sudo_password or ""
        # Для apt/flatpak командждём освобождения блокировки
        if cmd and cmd[0] in ("apt-get", "apt", "flatpak", "epm"):
            _wait_for_apt_lock(on_line)
        # Пароль передается ТОЛЬКО через stdin, не в списке аргументов cmd!
        proc = subprocess.Popen(["sudo", "-S"] + cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            proc.stdin.write(pw+"\n"); proc.stdin.flush()
        except BrokenPipeError: pass
        finally: proc.stdin.close()
        
        def _drain():
            for l in proc.stderr:
                low = l.lower()
                if "[sudo]" not in low and "password" not in low:
                    GLib.idle_add(on_line, l)
        
        t = threading.Thread(target=_drain, daemon=True); t.start()
        for l in proc.stdout: GLib.idle_add(on_line, l)
        t.join(); proc.wait()
        GLib.idle_add(on_done, proc.returncode == 0)
    threading.Thread(target=_w, daemon=True).start()

def run_epm(cmd: list, on_line, on_done):
    """Запускает epm через sudo с паролем через SUDO_ASKPASS чтобы избежать
    передачи пароля в stdin дочерних процессов (apt-get читает stdin как аргументы)."""
    import tempfile, stat
    def _w():
        pw = _sudo_password or ""
        env = os.environ.copy()
        # Создаём временный скрипт-хелпер который возвращает пароль
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            askpass_path = f.name
            f.write(f'#!/bin/sh\necho {pw!r}\n')
        os.chmod(askpass_path, stat.S_IRWXU)
        env["SUDO_ASKPASS"] = askpass_path
        try:
            # Ждём освобождения APT-блокировки (до 60 сек)
            lock_files = ["/var/cache/apt/archives/lock", "/var/lib/dpkg/lock-frontend"]
            for attempt in range(12):
                locked = any(
                    subprocess.run(["fuser", lf], capture_output=True).returncode == 0
                    for lf in lock_files if os.path.exists(lf)
                )
                if not locked:
                    break
                GLib.idle_add(on_line, f"⏳ APT занят, ожидание... ({attempt+1}/12)\n")
                import time; time.sleep(5)

            # -A = использовать SUDO_ASKPASS вместо stdin
            proc = subprocess.Popen(
                ["sudo", "-A"] + cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, env=env
            )
            for line in proc.stdout:
                GLib.idle_add(on_line, line)
            proc.wait()
            GLib.idle_add(on_done, proc.returncode == 0)
        finally:
            try: os.unlink(askpass_path)
            except: pass
    threading.Thread(target=_w, daemon=True).start()

def run_gsettings(args: list) -> bool:
    return subprocess.run(["gsettings"]+args, capture_output=True, text=True).returncode == 0

def gsettings_get(schema, key):
    r = subprocess.run(["gsettings","get",schema,key], capture_output=True, text=True)
    return r.stdout.strip()

def is_sudo_enabled() -> bool:
    r = subprocess.run(["control","sudowheel"], capture_output=True, text=True)
    return "enabled" in r.stdout.strip()

def is_flathub_enabled() -> bool:
    r = subprocess.run(["flatpak","remotes"], capture_output=True, text=True)
    return "flathub" in r.stdout.lower()

def is_fractional_scaling_enabled() -> bool:
    return "scale-monitor-framebuffer" in gsettings_get("org.gnome.mutter","experimental-features")

def check_app_installed(source: dict) -> bool:
    kind, pkg = source["check"]
    if kind == "flatpak":
        r = subprocess.run(["flatpak","list","--app","--columns=application"], capture_output=True, text=True)
        return pkg in r.stdout
    elif kind == "rpm":
        return subprocess.run(["rpm","-q",pkg], capture_output=True).returncode == 0
    elif kind == "path":
        return os.path.exists(os.path.expanduser(pkg))
    return False

def is_davinci_installed() -> bool:
    return os.path.exists("/opt/resolve/bin/resolve") or subprocess.run(["rpm","-q","davinci-resolve"], capture_output=True).returncode == 0

def is_aac_installed() -> bool:
    return os.path.exists("/opt/resolve/IOPlugins/aac_encoder_plugin.dvcp.bundle")

def is_fairlight_installed() -> bool:
    return subprocess.run(["rpm","-q","alsa-plugins-pulse"], capture_output=True).returncode == 0

def is_fstrim_enabled() -> bool:
    r = subprocess.run(["systemctl", "is-enabled", "fstrim.timer"], capture_output=True, text=True)
    return r.returncode == 0

def is_journal_optimized() -> bool:
    conf = "/etc/systemd/journald.conf"
    if not os.path.exists(conf): return False
    try:
        with open(conf, "r") as f:
            content = f.read()
            return "SystemMaxUse=100M" in content and "Compress=yes" in content
    except: return False

def is_system_busy() -> bool:
    """Проверяет реальную блокировку пакетного менеджера."""
    try:
        # Проверяем PackageKit
        if subprocess.run(["pgrep", "-f", "packagekitd"], capture_output=True).returncode == 0:
            return True
        # Проверяем файлы блокировки
        for lp in ["/var/lib/dpkg/lock-frontend", "/var/lib/apt/lists/lock"]:
            if os.path.exists(lp) and subprocess.run(["fuser", lp], capture_output=True).returncode == 0:
                return True
    except: pass
    return False
