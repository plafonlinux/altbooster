
import os
import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from ui.dynamic_page import DynamicPage
from ui.common import load_module


class AmdPage(DynamicPage):
    def __init__(self, log_fn):
        super().__init__(load_module("amd"), log_fn)
        self._log = log_fn

    def check_overclock(self):
        try:
            with open("/proc/cmdline", "r") as f:
                cmdline = f.read()
            return "amdgpu.ppfeaturemask=0xffffffff" in cmdline
        except Exception:
            return False

    def enable_overclock(self):
        self._log("\n▶  Включение режима разгона AMD...\n")
        
        def _do():
            try:
                with open("/etc/default/grub", "r") as f:
                    lines = f.readlines()
            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка чтения GRUB: {e}\n")
                return

            new_lines = []
            found = False
            changed = False
            
            for line in lines:
                if line.strip().startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                    if "amdgpu.ppfeaturemask=0xffffffff" in line:
                        GLib.idle_add(self._log, "ℹ  Параметр уже установлен.\n")
                        return
                    
                    parts = line.split("=", 1)
                    val = parts[1].strip()
                    quote = val[0] if val[0] in ['"', "'"] else ''
                    if quote:
                        content = val[1:-1]
                        new_val = f"{quote}{content} amdgpu.ppfeaturemask=0xffffffff{quote}"
                    else:
                        new_val = f"{val} amdgpu.ppfeaturemask=0xffffffff"
                    
                    new_lines.append(f"{parts[0]}={new_val}\n")
                    found = True
                    changed = True
                else:
                    new_lines.append(line)
            
            if not found:
                new_lines.append('GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.ppfeaturemask=0xffffffff"\n')
                changed = True

            if changed:
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                    tmp.writelines(new_lines)
                    tmp_path = tmp.name
                
                backend.run_privileged(
                    ["mv", tmp_path, "/etc/default/grub"],
                    self._log,
                    lambda ok: self._update_grub(ok)
                )
            else:
                GLib.idle_add(self._log, "ℹ  Изменений не требуется.\n")

        threading.Thread(target=_do, daemon=True).start()

    def _update_grub(self, ok):
        if not ok:
            self._log("✘  Ошибка записи конфига GRUB.\n")
            return
        self._log("▶  Обновление загрузчика (update-grub)...\n")
        backend.run_privileged(["update-grub"], self._log, lambda ok2: self._log("✔  Готово! Перезагрузите ПК.\n" if ok2 else "✘  Ошибка update-grub\n"))

    def confirm_reboot(self):
        d = Adw.AlertDialog(
            heading="Перезагрузить компьютер?",
            body="Изменения вступят в силу только после перезагрузки.",
        )
        d.add_response("cancel", "Отмена")
        d.add_response("reboot", "Перезагрузить")
        d.set_response_appearance("reboot", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("reboot")
        
        def _on_resp(_, r):
            if r == "reboot":
                subprocess.run(["systemctl", "reboot"])
        
        d.connect("response", _on_resp)
        d.present(self.get_root())

    def check_wheel(self):
        try:
            import grp
            user = os.environ.get("USER")
            groups = [g.gr_name for g in grp.getgrall() if user in g.gr_mem]
            gid = os.getgid()
            groups.append(grp.getgrgid(gid).gr_name)
            return "wheel" in groups
        except Exception:
            return False

    def setup_lact_wheel(self):
        user = os.environ.get("USER")
        if not user: return
        self._log(f"\n▶  Добавление пользователя {user} в группу wheel...\n")
        backend.run_privileged(
            ["usermod", "-aG", "wheel", user],
            self._log,
            lambda ok: self._log("✔  Готово! Перезайдите в систему.\n" if ok else "✘  Ошибка\n")
        )

    def apply_lact_config(self, file_path):
        self._log(f"\n▶  Применение конфига LACT: {file_path}...\n")
        backend.run_privileged(
            ["cp", file_path, "/etc/lact/config.yaml"],
            self._log,
            lambda ok: self._restart_lact(ok)
        )

    def _restart_lact(self, ok):
        if not ok:
            self._log("✘  Ошибка копирования конфига.\n")
            return
        self._log("▶  Перезапуск службы lactd...\n")
        backend.run_privileged(
            ["systemctl", "restart", "lactd"],
            self._log,
            lambda ok2: self._log("✔  Конфиг применён!\n" if ok2 else "✘  Ошибка перезапуска lactd\n")
        )


