
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

import backend
from ui.rows import SettingRow
from widgets import make_icon, make_status_icon, set_status_ok, set_status_error

_banner_css = Gtk.CssProvider()
_banner_css.load_from_data(b"""
    .experimental-banner {
        background-color: alpha(@error_color, 0.60);
        border-radius: 12px;
        padding: 10px 14px;
        margin: 12px;
    }
    .experimental-banner label {
        color: white;
    }
    .experimental-banner image {
        color: white;
    }
""")

_SCX_METEOR_BIN     = "/usr/local/bin/scx_meteor"
_SCX_METEOR_SERVICE = "/etc/systemd/system/scx_meteor.service"
_SCX_METEOR_REPO    = "https://github.com/Toxblh/scx_meteor"

_SERVICE_CONTENT = """\
[Unit]
Description=scx_meteor — LP-aware CPU scheduler for Intel
Documentation=https://github.com/Toxblh/scx_meteor
After=multi-user.target
ConditionPathExists=/sys/kernel/sched_ext

[Service]
Type=simple
ExecStart=/usr/local/bin/scx_meteor
Restart=on-failure
RestartSec=5
KillMode=mixed

[Install]
WantedBy=multi-user.target
"""


def _get_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "Неизвестно"


class IntelPage(Gtk.Box):

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(20)
        body.set_margin_bottom(20)
        body.set_margin_start(20)
        body.set_margin_end(20)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1152)
        clamp.set_tightening_threshold(864)
        clamp.set_child(body)
        scroll.set_child(clamp)

        overlay = Gtk.Overlay()
        overlay.set_vexpand(True)
        overlay.set_child(scroll)
        overlay.add_overlay(self._build_experimental_banner())
        self.append(overlay)

        body.append(self._build_compat_group())
        body.append(self._build_scx_meteor_group())


    def _build_experimental_banner(self) -> Gtk.Box:
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _banner_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        box = Gtk.Box(spacing=10)
        box.add_css_class("experimental-banner")
        box.set_halign(Gtk.Align.FILL)
        box.set_valign(Gtk.Align.END)

        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_icon_size(Gtk.IconSize.NORMAL)
        box.append(icon)

        label = Gtk.Label(
            label="Экспериментальная вкладка — scx_meteor в активной разработке. "
                  "Используйте на свой страх и риск."
        )
        label.set_wrap(True)
        label.set_xalign(0.0)
        label.set_hexpand(True)
        box.append(label)

        return box


    def _build_compat_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Совместимость")
        group.set_description(
            "scx_meteor работает на процессорах Intel с тремя типами ядер: "
            "P-ядра, E-ядра и LP-ядра (SoC tile). "
            "Требуется ядро Linux с поддержкой sched_ext (CONFIG_SCHED_CLASS_EXT)."
        )

        self._row_sched_ext = Adw.ActionRow()
        self._row_sched_ext.set_title("sched_ext в ядре")
        self._row_sched_ext.set_subtitle("Проверка /sys/kernel/sched_ext…")
        self._row_sched_ext.add_prefix(make_icon("cpu-symbolic"))
        self._status_sched = make_status_icon()
        self._status_sched.set_visible(False)
        self._row_sched_ext.add_suffix(self._status_sched)
        group.add(self._row_sched_ext)

        row_cpu = Adw.ActionRow()
        row_cpu.set_title("Процессор")
        row_cpu.set_subtitle(_get_cpu_model())
        row_cpu.add_prefix(make_icon("computer-symbolic"))
        group.add(row_cpu)

        threading.Thread(target=self._check_compat, daemon=True).start()
        return group

    def _check_compat(self):
        has_sched_ext = os.path.exists("/sys/kernel/sched_ext")
        GLib.idle_add(self._apply_compat_ui, has_sched_ext)

    def _apply_compat_ui(self, has_sched_ext: bool):
        self._status_sched.set_visible(True)
        if has_sched_ext:
            set_status_ok(self._status_sched)
            self._row_sched_ext.set_subtitle("Поддерживается (/sys/kernel/sched_ext найден)")
        else:
            set_status_error(self._status_sched)
            self._row_sched_ext.set_subtitle(
                "Не поддерживается — обновите ядро или добавьте CONFIG_SCHED_CLASS_EXT"
            )


    def _build_scx_meteor_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("scx_meteor — планировщик задач")
        group.set_description(
            "Стратегия «LP-first, burst-up, drain-back»: задачи стартуют на LP-ядрах "
            "и поднимаются на E/P-ядра по требованию. Снижает общее энергопотребление "
            "на 30–50% по сравнению со стандартным планировщиком."
        )

        self._row_install = SettingRow(
            "application-x-executable-symbolic",
            "scx_meteor",
            "Сборка из исходников и установка в /usr/local/bin/",
            "Установить",
            self._install_scx_meteor,
            self._check_scx_meteor_installed,
            "intel_scx_meteor_installed",
            done_label="Установлен",
            on_undo=self._uninstall_scx_meteor,
            undo_label="Удалить",
            undo_icon="user-trash-symbolic",
            help_text=(
                "Клонирует github.com/Toxblh/scx_meteor, собирает через "
                "cargo build --release и устанавливает бинарник. "
                "Занимает несколько минут."
            ),
        )
        group.add(self._row_install)

        self._row_service = SettingRow(
            "system-run-symbolic",
            "Автозапуск при загрузке",
            "systemd-сервис scx_meteor (enable + start)",
            "Включить",
            self._enable_scx_meteor_service,
            self._check_scx_meteor_service,
            "intel_scx_meteor_service",
            done_label="Активен",
            on_undo=self._disable_scx_meteor_service,
            undo_label="Выключить",
            undo_icon="media-playback-stop-symbolic",
            help_text="Запускает scx_meteor при каждой загрузке системы через systemd.",
        )
        group.add(self._row_service)

        return group


    def _check_scx_meteor_installed(self) -> bool:
        return os.path.isfile(_SCX_METEOR_BIN)

    def _check_scx_meteor_service(self) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "is-enabled", "scx_meteor"],
                capture_output=True, text=True,
            )
            return r.returncode == 0 and r.stdout.strip() in ("enabled", "static")
        except OSError:
            return False


    def _install_scx_meteor(self, row):
        row.set_working()
        self._log("\n▶  Установка scx_meteor...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Установка scx_meteor...")

        def _thread():
            self._log("▶  Установка зависимостей (rust, cargo, git, clang)...\n")
            backend.run_privileged_sync(
                ["epm", "install", "-y", "rust", "cargo", "git", "clang", "llvm"],
                self._log,
            )

            build_script = (
                "set -e\n"
                "BUILDDIR=$(mktemp -d)\n"
                "trap 'rm -rf \"$BUILDDIR\"' EXIT\n"
                "echo '▶  Клонирование репозитория...'\n"
                f"git clone --depth=1 {shlex.quote(_SCX_METEOR_REPO)} \"$BUILDDIR/scx_meteor\"\n"
                "cd \"$BUILDDIR/scx_meteor\"\n"
                "echo '▶  Сборка (cargo build --release)...'\n"
                "export PATH=\"$PATH:/usr/bin\"\n"
                "cargo build --release 2>&1\n"
                "echo '▶  Установка бинарника...'\n"
                "install -m 755 target/release/scx_meteor /usr/local/bin/scx_meteor\n"
                "echo '✔  Бинарник установлен'\n"
            )
            ok = backend.run_privileged_sync(["bash", "-c", build_script], self._log)

            if ok:
                ok = self._write_service_file()

            def _finish():
                row.set_done(ok)
                if ok:
                    self._log("✔  scx_meteor установлен!\n")
                    self._row_service._refresh()
                else:
                    self._log("✘  Ошибка установки scx_meteor\n")
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_thread, daemon=True).start()

    def _write_service_file(self) -> bool:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".service", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(_SERVICE_CONTENT)
                tmp_path = tmp.name
        except OSError as e:
            self._log(f"✘  Ошибка создания временного файла: {e}\n")
            return False

        cmd = [
            "bash", "-c",
            f"mv {shlex.quote(tmp_path)} {_SCX_METEOR_SERVICE} && "
            f"chmod 644 {_SCX_METEOR_SERVICE} && "
            "systemctl daemon-reload",
        ]
        return backend.run_privileged_sync(cmd, self._log)


    def _uninstall_scx_meteor(self, row):
        row.set_working()
        self._log("\n▶  Удаление scx_meteor...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Удаление scx_meteor...")

        cmd = [
            "bash", "-c",
            "systemctl disable --now scx_meteor 2>/dev/null || true; "
            f"rm -f {_SCX_METEOR_BIN} {_SCX_METEOR_SERVICE}; "
            "systemctl daemon-reload",
        ]

        def _on_done(ok):
            row.set_undo_done(ok)
            GLib.idle_add(self._row_service._refresh)
            self._log("✔  scx_meteor удалён\n" if ok else "✘  Ошибка удаления\n")
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(cmd, self._log, _on_done)


    def _enable_scx_meteor_service(self, row):
        row.set_working()
        self._log("\n▶  Включение сервиса scx_meteor...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Включение scx_meteor...")

        def _thread():
            if not os.path.exists(_SCX_METEOR_SERVICE):
                ok_svc = self._write_service_file()
                if not ok_svc:
                    GLib.idle_add(row.set_done, False)
                    if win and hasattr(win, "stop_progress"):
                        GLib.idle_add(win.stop_progress, False)
                    return

            def _on_done(ok):
                row.set_done(ok)
                self._log(
                    "✔  scx_meteor запущен и добавлен в автозагрузку\n" if ok
                    else "✘  Ошибка включения сервиса\n"
                )
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            backend.run_privileged(
                ["systemctl", "enable", "--now", "scx_meteor"],
                self._log, _on_done,
            )

        threading.Thread(target=_thread, daemon=True).start()

    def _disable_scx_meteor_service(self, row):
        row.set_working()
        self._log("\n▶  Отключение сервиса scx_meteor...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Отключение scx_meteor...")

        def _on_done(ok):
            row.set_undo_done(ok)
            self._log(
                "✔  scx_meteor остановлен и убран из автозагрузки\n" if ok
                else "✘  Ошибка отключения\n"
            )
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(
            ["systemctl", "disable", "--now", "scx_meteor"],
            self._log, _on_done,
        )

