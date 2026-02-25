"""Строки виджетов: SettingRow, AppRow, TaskRow."""

import os
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
import config
from widgets import (
    make_icon, make_button, make_status_icon,
    set_status_ok, set_status_error, clear_status, make_suffix_box,
)


class SettingRow(Adw.ActionRow):
    """Строка настройки с кнопкой и индикатором статуса."""

    def __init__(self, icon, title, subtitle, btn_label, on_activate, check_fn, state_key):
        super().__init__()
        self.set_title(title)
        self.set_subtitle(subtitle)
        self._check_fn = check_fn
        self._on_activate = on_activate
        self._state_key = state_key
        self._orig_label = btn_label

        self.add_prefix(make_icon(icon))
        self._status = make_status_icon()
        self._btn = make_button(btn_label)
        self._btn.connect("clicked", lambda _: self._on_activate(self))
        self._btn.set_sensitive(False)
        self.add_suffix(make_suffix_box(self._status, self._btn))

        if config.state_get(state_key) is True:
            self._set_ui(True)
        elif "kbd" not in state_key and check_fn is not None:
            threading.Thread(target=self._refresh, daemon=True).start()

    def _refresh(self):
        try:
            enabled = self._check_fn()
        except Exception:
            enabled = False
        config.state_set(self._state_key, enabled)
        GLib.idle_add(self._set_ui, enabled)

    def _set_ui(self, enabled):
        if enabled:
            set_status_ok(self._status)
            self._btn.set_label("Активировано")
            self._btn.set_sensitive(False)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        else:
            clear_status(self._status)
            self._btn.set_label(self._orig_label)
            self._btn.set_sensitive(True)
            self._btn.remove_css_class("flat")
            self._btn.add_css_class("suggested-action")

    def set_working(self):
        self._btn.set_sensitive(False)
        self._btn.set_label("…")

    def set_done(self, ok):
        if ok:
            config.state_set(self._state_key, True)
        self._set_ui(ok)
        if not ok:
            self._btn.set_label("Повторить")
            self._btn.set_sensitive(True)


class AppRow(Adw.ActionRow):
    """Строка приложения с установкой / удалением."""

    def __init__(self, app, log_fn, on_change_cb):
        super().__init__()
        self._app = app
        self._log = log_fn
        self._on_change = on_change_cb
        self._installing = False
        self._state_key = f"app_{app['id']}"

        self.set_title(app["label"])
        self.set_subtitle(app["desc"])

        self._status = make_status_icon()
        self.add_prefix(self._status)

        self._btn = make_button("Установить", width=120)
        self._btn.connect("clicked", self._on_install)
        self._btn.set_sensitive(False)

        self._trash_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self._trash_btn.add_css_class("destructive-action")
        self._trash_btn.set_valign(Gtk.Align.CENTER)
        self._trash_btn.connect("clicked", self._on_uninstall)
        self._trash_btn.set_visible(False)

        self._prog = Gtk.ProgressBar()
        self._prog.set_hexpand(True)
        self._prog.set_valign(Gtk.Align.CENTER)
        self._prog.set_visible(False)

        suffix = Gtk.Box(spacing=8)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(self._prog)
        suffix.append(self._btn)
        suffix.append(self._trash_btn)
        self.add_suffix(suffix)

        threading.Thread(target=self._check, daemon=True).start()

    def is_installed(self):
        return config.state_get(self._state_key) is True

    def _check(self):
        installed = backend.check_app_installed(self._app["source"])
        config.state_set(self._state_key, installed)
        GLib.idle_add(self._set_installed_ui, installed)

    def _set_installed_ui(self, installed):
        if installed:
            set_status_ok(self._status)
            self._btn.set_visible(False)
            self._prog.set_visible(False)
            self._trash_btn.set_visible(True)
            self._trash_btn.set_sensitive(True)
        else:
            clear_status(self._status)
            self._btn.set_visible(True)
            self._btn.set_label("Установить")
            self._btn.set_sensitive(True)
            self._trash_btn.set_visible(False)
        if self._on_change:
            self._on_change()

    def _on_install(self, _=None):
        if self._installing or self.is_installed():
            return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Подождите...\n")
            return
        self._installing = True
        src = self._app["source"]
        self._btn.set_sensitive(False)
        self._btn.set_label("…")
        self._prog.set_visible(True)
        self._prog.set_fraction(0.0)
        GLib.timeout_add(120, self._pulse)
        self._log(f"\n▶  Установка {self._app['label']} ({src['label']})...\n")
        cmd = src["cmd"]
        if cmd and cmd[0] == "epm":
            backend.run_epm(cmd, self._log, self._install_done)
        else:
            backend.run_privileged(cmd, self._log, self._install_done)

    def _on_uninstall(self, _):
        if self._installing:
            return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        self._installing = True
        self._trash_btn.set_sensitive(False)
        self._prog.set_visible(True)
        self._prog.set_fraction(0.0)
        GLib.timeout_add(120, self._pulse)
        kind, pkg = self._app["source"]["check"]
        if kind == "flatpak":
            cmd = ["flatpak", "uninstall", "-y", pkg]
        elif kind == "rpm":
            cmd = ["epm", "-e", pkg]
        else:
            cmd = [
                "rm", "-rf",
                os.path.expanduser("~/.local/share/monitor-control"),
                os.path.expanduser("~/Monic"),
            ]
        self._log(f"\n▶  Удаление {self._app['label']}...\n")
        backend.run_privileged(cmd, self._log, self._uninstall_done)

    def _pulse(self):
        if self._installing:
            self._prog.pulse()
            return True
        return False

    def _install_done(self, ok):
        self._installing = False
        self._prog.set_visible(False)
        if ok:
            self._log(f"✔  {self._app['label']} установлен!\n")
            config.state_set(self._state_key, True)
            self._set_installed_ui(True)
        else:
            self._log(f"✘  Ошибка установки {self._app['label']}\n")
            self._btn.set_sensitive(True)
            self._btn.set_label("Повторить")

    def _uninstall_done(self, ok):
        self._installing = False
        self._prog.set_visible(False)
        if ok:
            self._log(f"✔  {self._app['label']} удалён!\n")
            config.state_set(self._state_key, False)
            self._set_installed_ui(False)
        else:
            self._log(f"✘  Ошибка удаления {self._app['label']}\n")
            self._trash_btn.set_sensitive(True)


class TaskRow(Adw.ActionRow):
    """Строка задачи обслуживания с прогрессом."""

    def __init__(self, task, on_log, on_progress):
        super().__init__()
        self._task = task
        self._on_log = on_log
        self._on_progress = on_progress
        self._running = False
        self.result = None

        self.set_title(task["label"])
        self.set_subtitle(task["desc"])
        self.add_prefix(make_icon(task["icon"]))

        self._prog = Gtk.ProgressBar()
        self._prog.set_size_request(170, -1)
        self._prog.set_valign(Gtk.Align.CENTER)
        self._status = make_status_icon()
        self._status.set_size_request(22, -1)
        self._btn = make_button("Запустить", width=110)
        self._btn.connect("clicked", lambda _: self.start())

        right = Gtk.Box(spacing=10)
        right.set_valign(Gtk.Align.CENTER)
        right.append(self._prog)
        right.append(self._status)
        right.append(self._btn)
        self.add_suffix(right)

    def start(self):
        if self._running:
            return
        self._running = True
        self.result = None
        self._btn.set_sensitive(False)
        self._btn.set_label("…")
        clear_status(self._status)
        self._prog.set_fraction(0.0)
        cmd = self._task["cmd"].copy()
        if self._task["id"] == "davinci":
            cmd = [
                "find",
                config.get_dv_cache(), config.get_dv_proxy(),
                "-mindepth", "1", "-delete",
            ]
        self._on_log(f"\n▶  {self._task['label']}...\n")
        GLib.timeout_add(110, self._pulse)
        backend.run_privileged(cmd, self._on_log, self._finish)

    def _pulse(self):
        if self._running:
            self._prog.pulse()
            return True
        return False

    def _finish(self, ok):
        self._running = False
        self.result = ok
        self._prog.set_fraction(1.0 if ok else 0.0)
        if ok:
            set_status_ok(self._status)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        else:
            set_status_error(self._status)
        self._btn.set_label("Повтор")
        self._btn.set_sensitive(True)
        self._on_log(f"{'✔  Готово' if ok else '✘  Ошибка'}: {self._task['label']}\n")
        self._on_progress()
