"""Блок napd (App Nap для Linux) на вкладке «Твики» → «Приоритеты процессов»."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from ui.rows import SettingRow

_NAPD_BIN = "/usr/local/bin/napd"
_NAPD_REPO = "https://github.com/Toxblh/napd"
_NAPD_SERVICE_NAME = "napd.service"
_NAPD_USER_SERVICE_DIR = Path.home() / ".config" / "systemd" / "user"
_NAPD_USER_SERVICE = _NAPD_USER_SERVICE_DIR / _NAPD_SERVICE_NAME

_SERVICE_UNIT = f"""[Unit]
Description=App Nap daemon — automatic throttling of background applications
After=graphical-session.target pipewire.service
PartOf=graphical-session.target
Wants=pipewire.service

[Service]
Type=simple
ExecStart={_NAPD_BIN}
Restart=on-failure
RestartSec=5

Environment=RUST_LOG=info

[Install]
WantedBy=graphical-session.target
"""


def _experimental_badge() -> Gtk.Widget:
    lbl = Gtk.Label(label="Экспериментально")
    lbl.add_css_class("ab-tweak-experimental-badge")
    lbl.set_valign(Gtk.Align.CENTER)
    lbl.set_tooltip_text(
        "Сборка из исходников: возможны сбои и несовместимость с вашей системой."
    )
    return lbl


class NapdTweaksSection:
    def __init__(self, log_fn, host: Gtk.Widget):
        self._log = log_fn
        self._host = host

    def append_to(self, body: Gtk.Box) -> Gtk.Widget:
        group = Adw.PreferencesGroup()
        group.set_title("napd — App Nap для Linux")
        group.set_header_suffix(_experimental_badge())

        intro = Adw.ActionRow()
        intro.set_title("napd")
        intro.set_subtitle(
            "Аналог macOS App Nap: автоматически снижает приоритет фоновых приложений "
            "(CPU nice +19, SCHED_BATCH, I/O idle, cgroup cpu.max) и восстанавливает "
            "его при переключении фокуса. Аудио-приложения обходятся через PipeWire.\n\n"
            "Требует Wayland с поддержкой zwlr_foreign_toplevel_manager_v1 "
            "(Niri, Sway и другие wlroots-compositor) и cgroup v2.\n\n"
            "Автор: Toxblh (github.com/Toxblh/napd). "
            "Сборка из GitHub в /usr/local/bin, запускается как пользовательский systemd-сервис."
        )
        intro.set_activatable(False)
        intro.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        group.add(intro)

        self._row_install = SettingRow(
            "application-x-executable-symbolic",
            "napd",
            "Сборка из исходников и установка в /usr/local/bin/",
            "Установить",
            self._install,
            self._check_installed,
            "napd_installed",
            done_label="Установлен",
            on_undo=self._uninstall,
            undo_label="Удалить",
            undo_icon="user-trash-symbolic",
            help_text=(
                "Клонирует репозиторий Toxblh/napd, ставит зависимости (rust, rust-cargo, "
                "pipewire-libs-devel) через epm, собирает cargo build --release, "
                "копирует бинарник в /usr/local/bin/napd."
            ),
        )
        group.add(self._row_install)

        self._row_service = SettingRow(
            "system-run-symbolic",
            "Пользовательский сервис",
            "systemd --user: napd.service",
            "Включить",
            self._enable_service,
            self._check_service,
            "napd_service",
            done_label="Активен",
            on_undo=self._disable_service,
            undo_label="Выключить",
            undo_icon="media-playback-stop-symbolic",
            help_text="Устанавливает и запускает napd как пользовательский systemd-сервис.",
        )
        group.add(self._row_service)

        body.append(group)
        return intro

    def _root(self):
        return self._host.get_root()

    def _check_installed(self) -> bool:
        return os.path.isfile(_NAPD_BIN)

    def _check_service(self) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-enabled", _NAPD_SERVICE_NAME],
                capture_output=True,
                text=True,
            )
            return r.returncode == 0 and r.stdout.strip() in ("enabled", "static")
        except OSError:
            return False

    def _write_service_file(self) -> bool:
        try:
            _NAPD_USER_SERVICE_DIR.mkdir(parents=True, exist_ok=True)
            _NAPD_USER_SERVICE.write_text(_SERVICE_UNIT, encoding="utf-8")
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            return True
        except Exception as e:
            self._log(f"✘  Ошибка записи unit-файла: {e}\n")
            return False

    def _install(self, row):
        row.set_working()
        self._log("\n▶  Установка napd…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Установка napd…")

        def _thread():
            self._log("▶  Зависимости сборки (rust, rust-cargo, pipewire-libs-devel…)…\n")
            backend.run_privileged_sync(
                [
                    "epm",
                    "install",
                    "-y",
                    "rust",
                    "rust-cargo",
                    "git",
                    "gcc",
                    "make",
                    "pkg-config",
                    "pipewire-libs-devel",
                ],
                self._log,
            )

            repo = shlex.quote(_NAPD_REPO)
            build_script = (
                "set -e\n"
                "BUILDDIR=$(mktemp -d)\n"
                "trap 'rm -rf \"$BUILDDIR\"' EXIT\n"
                f"git clone --depth=1 {repo} \"$BUILDDIR/repo\"\n"
                'cd "$BUILDDIR/repo"\n'
                "cargo build --release\n"
                f"install -D -m0755 target/release/napd {shlex.quote(_NAPD_BIN)}\n"
            )
            ok = backend.run_privileged_sync(["bash", "-c", build_script], self._log)

            def _finish():
                row.set_done(ok)
                if ok:
                    self._log("✔  napd установлен.\n")
                    self._row_service._refresh()
                else:
                    self._log("✘  Ошибка установки napd\n")
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_thread, daemon=True).start()

    def _uninstall(self, row):
        row.set_working()
        self._log("\n▶  Удаление napd…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Удаление napd…")

        def _thread():
            # Остановить и выключить пользовательский сервис (без root)
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", _NAPD_SERVICE_NAME],
                capture_output=True,
            )
            try:
                _NAPD_USER_SERVICE.unlink(missing_ok=True)
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            except Exception:
                pass

            # Удалить бинарник (нужны права)
            ok = backend.run_privileged_sync(
                ["rm", "-f", _NAPD_BIN],
                self._log,
            )

            def _finish():
                row.set_undo_done(ok)
                GLib.idle_add(self._row_service._refresh)
                self._log("✔  napd удалён\n" if ok else "✘  Ошибка удаления\n")
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_thread, daemon=True).start()

    def _enable_service(self, row):
        row.set_working()
        self._log("\n▶  Включение napd.service…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Включение napd…")

        def _thread():
            ok = self._write_service_file()
            if ok:
                r = subprocess.run(
                    ["systemctl", "--user", "enable", "--now", _NAPD_SERVICE_NAME],
                    capture_output=True,
                    text=True,
                )
                ok = r.returncode == 0
                if not ok:
                    self._log(f"✘  {r.stderr.strip()}\n")

            def _finish():
                row.set_done(ok)
                self._log(
                    "✔  napd запущен\n" if ok else "✘  Ошибка запуска napd\n"
                )
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_thread, daemon=True).start()

    def _disable_service(self, row):
        row.set_working()
        self._log("\n▶  Отключение napd.service…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Отключение napd…")

        def _thread():
            r = subprocess.run(
                ["systemctl", "--user", "disable", "--now", _NAPD_SERVICE_NAME],
                capture_output=True,
                text=True,
            )
            ok = r.returncode == 0
            if not ok:
                self._log(f"✘  {r.stderr.strip()}\n")

            def _finish():
                row.set_undo_done(ok)
                self._log(
                    "✔  napd остановлен\n" if ok else "✘  Ошибка отключения\n"
                )
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_thread, daemon=True).start()
