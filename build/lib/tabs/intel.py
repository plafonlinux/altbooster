
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from core.sched_ext import has_sched_ext
from ui.rows import SettingRow
from ui.widgets import make_icon

_SCX_METEOR_BIN = "/usr/local/bin/scx_meteor"
_SCX_METEOR_SERVICE = "/etc/systemd/system/scx_meteor.service"
_SCX_METEOR_REPO = "https://github.com/Toxblh/scx_meteor"

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


def is_intel_cpu() -> bool:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("vendor_id"):
                    return "intel" in line.split(":", 1)[1].strip().lower()
    except OSError:
        pass
    return False


def _intel_only_badge() -> Gtk.Widget:
    lbl = Gtk.Label(label="Только для Intel")
    lbl.add_css_class("ab-tweak-intel-only-badge")
    lbl.set_valign(Gtk.Align.CENTER)
    lbl.set_tooltip_text(
        "scx_meteor рассчитан на гибридные процессоры Intel (P/E/LP-ядра). "
        "На AMD и других CPU блок отключён."
    )
    return lbl


def _experimental_tech_badge() -> Gtk.Widget:
    lbl = Gtk.Label(label="Экспериментальная технология")
    lbl.add_css_class("ab-tweak-experimental-badge")
    lbl.set_valign(Gtk.Align.CENTER)
    lbl.set_tooltip_text(
        "scx_meteor в активной разработке. Возможны сбои и регрессии — используйте на свой страх и риск."
    )
    return lbl


class ScxMeteorTweaksSection:
    """Блок scx_meteor на вкладке «Твики» (раньше отдельная вкладка «Intel»)."""

    def __init__(self, log_fn, host: Gtk.Widget):
        self._log = log_fn
        self._host = host

    def append_to(self, body: Gtk.Box) -> Gtk.Widget:
        group = self._build_scx_meteor_group()
        if not is_intel_cpu():
            group.set_header_suffix(_intel_only_badge())
            group.set_sensitive(False)
        body.append(group)
        return self._row_install

    def _build_scx_meteor_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Планировщик Intel (SCX Meteor)")

        intro = Adw.ActionRow()
        intro.set_title("SCX Meteor")
        intro.set_subtitle(
            "Планировщик для гибридных процессоров Intel (P-, E- и LP-ядра, SoC tile). "
            "Требуется активный sched_ext в ядре — настройка в общем блоке выше.\n\n"
            "Стратегия «LP-first, burst-up, drain-back»: задачи стартуют на LP-ядрах "
            "и поднимаются на E/P-ядра по требованию; заявлено снижение энергопотребления "
            "на 30–50% относительно стандартного планировщика."
        )
        intro.set_activatable(False)
        bulb = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        bulb.set_valign(Gtk.Align.CENTER)
        intro.add_prefix(bulb)
        intro.add_suffix(_experimental_tech_badge())

        group.add(intro)

        self._row_install = SettingRow(
            "application-x-executable-symbolic",
            "SCX Meteor",
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

    def apply_sched_ext_gate(self, sched_ok: bool) -> None:
        if not is_intel_cpu():
            return
        tip = (
            ""
            if sched_ok
            else (
                "Сначала подключите sched_ext в ядре (блок «Поддержка sched_ext в ядре» "
                "на подвкладке «Планировщик ядра»)."
            )
        )
        self._row_install.set_sensitive(sched_ok)
        self._row_service.set_sensitive(sched_ok)
        self._row_install.set_tooltip_text(tip)
        self._row_service.set_tooltip_text(tip)

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

    def _root(self):
        return self._host.get_root()

    def _install_scx_meteor(self, row):
        row.set_working()
        self._log("\n▶  Установка scx_meteor...\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Установка scx_meteor...")

        def _thread():
            if not has_sched_ext():
                def _fail():
                    self._log(
                        "✘  В текущем ядре нет sched_ext. Установите ядро через блок "
                        "«Поддержка sched_ext в ядре» на подвкладке «Планировщик ядра» и перезагрузитесь.\n"
                    )
                    row.set_done(False)
                    if win and hasattr(win, "stop_progress"):
                        win.stop_progress(False)

                GLib.idle_add(_fail)
                return

            self._log("▶  Установка зависимостей (rust, rust-cargo, git, clang, llvm)...\n")
            backend.run_privileged_sync(
                ["epm", "install", "-y", "rust", "rust-cargo", "git", "clang", "llvm"],
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
        win = self._root()
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
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Включение scx_meteor...")

        def _thread():
            if not has_sched_ext():
                def _fail():
                    self._log(
                        "✘  В текущем ядре нет sched_ext. Установите ядро через блок "
                        "«Поддержка sched_ext в ядре» на подвкладке «Планировщик ядра» и перезагрузитесь.\n"
                    )
                    row.set_done(False)
                    if win and hasattr(win, "stop_progress"):
                        win.stop_progress(False)

                GLib.idle_add(_fail)
                return

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
        win = self._root()
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
