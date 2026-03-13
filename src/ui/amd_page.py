
import os
import subprocess
import tempfile
import shlex
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
from dynamic_page import DynamicPage
from ui.common import load_module
from ui.rows import SettingRow


class AmdPage(DynamicPage):
    def __init__(self, log_fn):
        super().__init__(load_module("amd"), log_fn)
        self._log = log_fn
        self._build_scx_ui()

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
                import tempfile
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


    def _is_sisyphus(self):
        for path in ["/etc/altlinux-release", "/etc/os-release"]:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        if "Sisyphus" in f.read():
                            return True
                except Exception:
                    pass
        return False

    def _build_scx_ui(self):
        is_sisyphus = self._is_sisyphus()
        group = Adw.PreferencesGroup()
        group.set_title("Планировщик CPU (SCX) — Экспериментально")
        desc = "Экспериментальные планировщики sched-ext (LAVD) для игровых задач."
        if not is_sisyphus:
            desc += "\n⚠️ Пакет scx-scheds доступен только в репозитории Sisyphus."
        group.set_description(desc)
        
        target = getattr(self, "_body", None)
        if not target:
            child = self.get_first_child()
            while child:
                if isinstance(child, Gtk.ScrolledWindow):
                    target = child.get_child()
                    if isinstance(target, Adw.Clamp):
                        target = target.get_child()
                    if isinstance(target, Gtk.Viewport):
                        target = target.get_child()
                    break
                child = child.get_next_sibling()

        (target if target else self).append(group)

        self._row_lavd_std = SettingRow(
            "utilities-terminal-symbolic", "LAVD (Performance)",
            "Оптимизация для игр и десктопа", "Включить",
            lambda r: self._enable_lavd(r, autopower=False),
            lambda: self._check_lavd_active(autopower=False),
            "amd_scx_lavd_std", "Активно",
            self._disable_lavd, "Выключить",
            help_text="LAVD (Latency-aware Virtual Deadline) — планировщик от Igalia/Valve. Снижает задержки в играх и улучшает отзывчивость системы."
        )
        group.add(self._row_lavd_std)
        if not is_sisyphus:
            self._row_lavd_std.set_sensitive(False)
            self._row_lavd_std.set_tooltip_text("Требуется репозиторий Sisyphus")

        self._row_lavd_auto = SettingRow(
            "battery-symbolic", "LAVD (Autopower)",
            "Режим для ноутбуков и мини-ПК", "Включить",
            lambda r: self._enable_lavd(r, autopower=True),
            lambda: self._check_lavd_active(autopower=True),
            "amd_scx_lavd_auto", "Активно",
            self._disable_lavd, "Выключить",
            help_text="Версия с флагом --autopower. Адаптирует частоты для экономии энергии при работе от батареи."
        )
        group.add(self._row_lavd_auto)
        if not is_sisyphus:
            self._row_lavd_auto.set_sensitive(False)
            self._row_lavd_auto.set_tooltip_text("Требуется репозиторий Sisyphus")

    def _check_scx_installed(self):
        return backend.check_app_installed({"check": ["rpm", "scx-scheds"]})

    def _check_lavd_active(self, autopower):
        if subprocess.run(["systemctl", "is-active", "scx_lavd"], capture_output=True).returncode != 0:
            return False
        try:
            with open("/etc/systemd/system/scx_lavd.service", "r") as f:
                content = f.read()
            has_auto = "--autopower" in content
            return has_auto == autopower
        except Exception:
            return False

    def _enable_lavd(self, row, autopower):
        row.set_working()
        mode_str = "Autopower" if autopower else "Performance"
        self._log(f"\n▶  Включение LAVD ({mode_str})...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress(f"Включение LAVD ({mode_str})...")

        def _thread_worker():
            if not self._check_scx_installed():
                GLib.idle_add(self._log, "▶  Установка пакета scx-scheds...\n")
                ok_inst = backend.run_privileged_sync(["apt-get", "install", "-y", "scx-scheds"], self._log)
                if not ok_inst:
                    GLib.idle_add(self._log, "✘  Ошибка установки пакета scx-scheds\n")
                    GLib.idle_add(row.set_done, False)
                    if win and hasattr(win, "stop_progress"): GLib.idle_add(win.stop_progress, False)
                    return

            exec_start = "/usr/bin/scx_lavd --autopower" if autopower else "/usr/bin/scx_lavd"
            service_content = f"""[Unit]
Description=SCX LAVD CPU Scheduler
Documentation=https://github.com/sched-ext/scx
After=multi-user.target
ConditionPathExists=/sys/kernel/sched_ext
[Service]
Type=simple
ExecStart={exec_start}
ExecStop=/bin/kill -SIGINT $MAINPID
Restart=on-failure
RestartSec=5
KillMode=mixed
[Install]
WantedBy=multi-user.target
"""
            try:
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                    tmp.write(service_content)
                    tmp_path = tmp.name
            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка подготовки файла: {e}\n")
                GLib.idle_add(row.set_done, False)
                if win and hasattr(win, "stop_progress"): GLib.idle_add(win.stop_progress, False)
                return

            cmd = [
                "bash", "-c",
                f"mv {shlex.quote(tmp_path)} /etc/systemd/system/scx_lavd.service && "
                "chmod 644 /etc/systemd/system/scx_lavd.service && "
                "systemctl daemon-reload && "
                "systemctl enable scx_lavd && "
                "systemctl restart scx_lavd"
            ]

            ok_service = backend.run_privileged_sync(cmd, self._log)

            def _finish_ui():
                row.set_done(ok_service)
                other_row = self._row_lavd_std if autopower else self._row_lavd_auto
                other_row._refresh()

                if ok_service:
                    self._log(f"✔  LAVD ({mode_str}) активирован!\n")
                else:
                    self._log("✘  Ошибка включения LAVD\n")
                if win and hasattr(win, "stop_progress"): win.stop_progress(ok_service)

            GLib.idle_add(_finish_ui)

        threading.Thread(target=_thread_worker, daemon=True).start()

    def _disable_lavd(self, row):
        row.set_working()
        self._log("\n▶  Отключение LAVD...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"): win.start_progress("Отключение LAVD...")
        
        backend.run_privileged(
            ["systemctl", "disable", "--now", "scx_lavd"],
            self._log,
            lambda ok: (
                row.set_undo_done(ok),
                GLib.idle_add(self._row_lavd_std._refresh),
                GLib.idle_add(self._row_lavd_auto._refresh),
                self._log("✔  LAVD отключён\n" if ok else "✘  Ошибка отключения\n"),
                win.stop_progress(ok) if win and hasattr(win, "stop_progress") else None
            )
        )
