"""Блок System76 Scheduler (Pop!_OS) на вкладке «Твики» → «Приоритеты процессов»."""

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
from ui.rows import SettingRow

_S76_BIN = "/usr/local/bin/system76-scheduler"
_S76_SERVICE = "/etc/systemd/system/com.system76.Scheduler.service"
_S76_REPO = "https://github.com/pop-os/system76-scheduler"
_S76_TAG = "2.0.2"

_DBUS_CONF = """<!DOCTYPE busconfig PUBLIC
          "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
          "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
    <policy user="root">
        <allow own="com.system76.Scheduler"/>
        <allow send_destination="com.system76.Scheduler"/>
        <allow receive_sender="com.system76.Scheduler"/>
    </policy>
    <policy context="default">
        <allow send_destination="com.system76.Scheduler"/>
        <allow receive_sender="com.system76.Scheduler"/>
    </policy>
</busconfig>
"""

_SERVICE_UNIT = f"""[Unit]
Description=System76 Scheduler — приоритеты процессов и CFS (Pop!_OS)
Documentation={_S76_REPO}
After=network.target dbus.socket

[Service]
ExecStart={_S76_BIN} daemon
ExecReload={_S76_BIN} daemon reload
Type=dbus
BusName=com.system76.Scheduler

[Install]
WantedBy=multi-user.target
"""


def _experimental_badge() -> Gtk.Widget:
    lbl = Gtk.Label(label="Экспериментально")
    lbl.add_css_class("ab-tweak-experimental-badge")
    lbl.set_valign(Gtk.Align.CENTER)
    lbl.set_tooltip_text(
        "Сторонняя сборка из исходников Pop!_OS: возможны сбои и несовместимость с вашей системой."
    )
    return lbl


class System76SchedulerTweaksSection:
    def __init__(self, log_fn, host: Gtk.Widget):
        self._log = log_fn
        self._host = host

    def append_to(self, body: Gtk.Box) -> Gtk.Widget:
        group = Adw.PreferencesGroup()
        group.set_title("System76 Scheduler (Pop!_OS)")
        group.set_header_suffix(_experimental_badge())

        intro = Adw.ActionRow()
        intro.set_title("system76-scheduler")
        intro.set_subtitle(
            "Служба из Pop!_OS (System76): подстраивает латентности CFS (сеть/батарея), "
            "назначает nice и I/O-приоритеты по правилам KDL, опционально отслеживает PipeWire.\n\n"
            "Это не sched_ext и не замена ядру: работа поверх стандартного планировщика. "
            "Одновременно с ananicy-cpp не рекомендуется — оба конкурируют за приоритеты процессов.\n\n"
            "Сборка из GitHub в /usr/local/bin, конфиги в /etc/system76-scheduler; через epm ставятся "
            "rust, rust-cargo, clang-devel, pipewire-libs-devel и сопутствующие пакеты. Для execsnoop "
            "при сборке/рантайме — bcc-tools (execsnoop-bpfcc), если доступен."
        )
        intro.set_activatable(False)
        intro.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))

        group.add(intro)

        self._row_install = SettingRow(
            "application-x-executable-symbolic",
            "System76 Scheduler",
            "Сборка из исходников (тег "
            + _S76_TAG
            + ") и установка в /usr/local/bin/",
            "Установить",
            self._install,
            self._check_installed,
            "system76_scheduler_installed",
            done_label="Установлен",
            on_undo=self._uninstall,
            undo_label="Удалить",
            undo_icon="user-trash-symbolic",
            help_text=(
                "Клонирует репозиторий pop-os/system76-scheduler, ставит зависимости сборки через epm, "
                "cargo build --release, копирует бинарник, config.kdl, профиль pop_os.kdl, политику D-Bus "
                "и unit com.system76.Scheduler. Может занять несколько минут."
            ),
        )
        group.add(self._row_install)

        self._row_service = SettingRow(
            "system-run-symbolic",
            "Служба D-Bus",
            "systemd: com.system76.Scheduler (Type=dbus)",
            "Включить",
            self._enable_service,
            self._check_service,
            "system76_scheduler_service",
            done_label="Активна",
            on_undo=self._disable_service,
            undo_label="Выключить",
            undo_icon="media-playback-stop-symbolic",
            help_text="Регистрирует имя com.system76.Scheduler и запускает фоновый планировщик.",
        )
        group.add(self._row_service)

        body.append(group)
        return intro

    def _root(self):
        return self._host.get_root()

    def _check_installed(self) -> bool:
        return os.path.isfile(_S76_BIN)

    def _check_service(self) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "is-enabled", "com.system76.Scheduler.service"],
                capture_output=True,
                text=True,
            )
            return r.returncode == 0 and r.stdout.strip() in ("enabled", "static")
        except OSError:
            return False

    def _write_service_file(self) -> bool:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".service", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(_SERVICE_UNIT)
                tmp_path = tmp.name
        except OSError as e:
            self._log(f"✘  Ошибка временного файла unit: {e}\n")
            return False
        cmd = [
            "bash",
            "-c",
            f"mv {shlex.quote(tmp_path)} {_S76_SERVICE} && chmod 644 {_S76_SERVICE} && systemctl daemon-reload",
        ]
        return backend.run_privileged_sync(cmd, self._log)

    def _write_dbus_conf(self) -> bool:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".conf", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(_DBUS_CONF)
                tmp_path = tmp.name
        except OSError as e:
            self._log(f"✘  Ошибка временного файла D-Bus: {e}\n")
            return False
        cmd = [
            "bash",
            "-c",
            f"mv {shlex.quote(tmp_path)} /etc/dbus-1/system.d/com.system76.Scheduler.conf && "
            "chmod 644 /etc/dbus-1/system.d/com.system76.Scheduler.conf",
        ]
        return backend.run_privileged_sync(cmd, self._log)

    def _install(self, row):
        row.set_working()
        self._log("\n▶  Установка System76 Scheduler…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Установка System76 Scheduler…")

        def _thread():
            self._log(
                "▶  Зависимости сборки (ALT: rust, rust-cargo, clang-devel, pipewire-libs-devel…)…\n"
            )
            backend.run_privileged_sync(
                [
                    "epm",
                    "install",
                    "-y",
                    "rust",
                    "rust-cargo",
                    "git",
                    "clang",
                    "gcc",
                    "make",
                    "pkg-config",
                    "pipewire-libs-devel",
                    "clang-devel",
                ],
                self._log,
            )

            repo = shlex.quote(_S76_REPO)
            tag = shlex.quote(_S76_TAG)
            build_script = (
                "set -e\n"
                "BUILDDIR=$(mktemp -d)\n"
                "trap 'rm -rf \"$BUILDDIR\"' EXIT\n"
                f"git clone --depth=1 -b {tag} {repo} \"$BUILDDIR/repo\"\n"
                'cd "$BUILDDIR/repo"\n'
                'export EXECSNOOP_PATH="$(command -v execsnoop-bpfcc || command -v execsnoop '
                '|| echo /usr/sbin/execsnoop-bpfcc)"\n'
                "echo \"▶  EXECSNOOP_PATH=$EXECSNOOP_PATH\"\n"
                "cargo build --release\n"
                f"install -D -m0755 target/release/system76-scheduler {shlex.quote(_S76_BIN)}\n"
                "install -D -m0644 data/config.kdl /etc/system76-scheduler/config.kdl\n"
                "install -D -m0644 data/pop_os.kdl "
                "/etc/system76-scheduler/process-scheduler/pop_os.kdl\n"
            )
            ok = backend.run_privileged_sync(["bash", "-c", build_script], self._log)
            if ok:
                ok = self._write_dbus_conf()
            if ok:
                ok = self._write_service_file()
            if ok:
                ok = backend.run_privileged_sync(
                    [
                        "bash",
                        "-c",
                        "systemctl try-reload-or-restart dbus.service 2>/dev/null || "
                        "systemctl reload dbus-broker.service 2>/dev/null || true",
                    ],
                    self._log,
                )

            def _finish():
                row.set_done(ok)
                if ok:
                    self._log("✔  System76 Scheduler установлен.\n")
                    self._row_service._refresh()
                else:
                    self._log("✘  Ошибка установки System76 Scheduler\n")
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_thread, daemon=True).start()

    def _uninstall(self, row):
        row.set_working()
        self._log("\n▶  Удаление System76 Scheduler…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Удаление System76 Scheduler…")

        cmd = [
            "bash",
            "-c",
            "systemctl disable --now com.system76.Scheduler.service 2>/dev/null || true; "
            f"rm -f {shlex.quote(_S76_BIN)} "
            "/etc/dbus-1/system.d/com.system76.Scheduler.conf "
            f"{shlex.quote(_S76_SERVICE)}; "
            "rm -rf /etc/system76-scheduler; "
            "systemctl daemon-reload; "
            "systemctl try-reload-or-restart dbus.service 2>/dev/null || "
            "systemctl reload dbus-broker.service 2>/dev/null || true",
        ]

        def _on_done(ok):
            row.set_undo_done(ok)
            GLib.idle_add(self._row_service._refresh)
            self._log("✔  System76 Scheduler удалён\n" if ok else "✘  Ошибка удаления\n")
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(cmd, self._log, _on_done)

    def _enable_service(self, row):
        row.set_working()
        self._log("\n▶  Включение com.system76.Scheduler…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Включение System76 Scheduler…")

        def _thread():
            if not os.path.isfile(_S76_SERVICE):
                ok_svc = self._write_service_file() and self._write_dbus_conf()
                if not ok_svc:
                    GLib.idle_add(row.set_done, False)
                    if win and hasattr(win, "stop_progress"):
                        GLib.idle_add(win.stop_progress, False)
                    return
                backend.run_privileged_sync(
                    [
                        "bash",
                        "-c",
                        "systemctl try-reload-or-restart dbus.service 2>/dev/null || "
                        "systemctl reload dbus-broker.service 2>/dev/null || true",
                    ],
                    self._log,
                )

            def _on_done(ok):
                row.set_done(ok)
                self._log(
                    "✔  Служба System76 Scheduler запущена\n" if ok else "✘  Ошибка запуска\n"
                )
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            backend.run_privileged(
                ["systemctl", "enable", "--now", "com.system76.Scheduler.service"],
                self._log,
                _on_done,
            )

        threading.Thread(target=_thread, daemon=True).start()

    def _disable_service(self, row):
        row.set_working()
        self._log("\n▶  Отключение com.system76.Scheduler…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Отключение System76 Scheduler…")

        def _on_done(ok):
            row.set_undo_done(ok)
            self._log(
                "✔  Служба остановлена\n" if ok else "✘  Ошибка отключения\n"
            )
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(
            ["systemctl", "disable", "--now", "com.system76.Scheduler.service"],
            self._log,
            _on_done,
        )
