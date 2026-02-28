"""Строки виджетов: SettingRow, AppRow, TaskRow."""

import os
import subprocess
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

    # ВАЖНО: Добавили аргумент done_label="Активировано" в конец
    def __init__(self, icon, title, subtitle, btn_label, on_activate, check_fn, state_key, done_label="Активировано", on_undo=None, undo_label="Отменить", undo_icon="edit-undo-symbolic"):
        super().__init__()
        self.set_title(title)
        self.set_subtitle(subtitle)
        self._check_fn = check_fn
        self._on_activate = on_activate
        self._on_undo = on_undo
        self._state_key = state_key
        self._orig_label = btn_label
        self._done_label = done_label  # Сохраняем кастомный текст для кнопки
        self._undo_label = undo_label
        self._undo_icon = undo_icon
        self._is_active = False

        self.add_prefix(make_icon(icon))
        self._status = make_status_icon()
        self._btn = make_button(btn_label)
        self._btn.connect("clicked", self._on_btn_clicked)
        self._btn.set_sensitive(False)
        self.add_suffix(make_suffix_box(self._status, self._btn))

        if config.state_get(state_key) is True:
            self._set_ui(True)
        elif "kbd" not in state_key and check_fn is not None:
            threading.Thread(target=self._refresh, daemon=True).start()

    def _on_btn_clicked(self, _):
        if self._is_active and self._on_undo:
            self._on_undo(self)
        else:
            self._on_activate(self)

    def _refresh(self):
        try:
            enabled = self._check_fn()
        except Exception:
            enabled = False
        config.state_set(self._state_key, enabled)
        GLib.idle_add(self._set_ui, enabled)

    def _set_ui(self, enabled):
        self._is_active = enabled
        self._status.set_visible(True)
        if enabled:
            set_status_ok(self._status)
            if self._on_undo:
                self._btn.set_visible(True)
                self._btn.set_icon_name(self._undo_icon)
                self._btn.set_tooltip_text(self._undo_label)
                self._btn.set_sensitive(True)
                self._btn.remove_css_class("suggested-action")
                self._btn.add_css_class("flat")
                self._btn.add_css_class("circular")
                self._btn.set_size_request(-1, -1)
                self._btn.remove_css_class("success")
            else:
                if not self._done_label:
                    self._status.set_visible(False)
                    self._btn.set_visible(True)
                    self._btn.set_icon_name("object-select-symbolic")
                    self._btn.set_tooltip_text("Активно")
                    self._btn.set_sensitive(False)
                    self._btn.remove_css_class("suggested-action")
                    self._btn.add_css_class("flat")
                    self._btn.add_css_class("circular")
                    self._btn.set_size_request(-1, -1)
                    self._btn.add_css_class("success")
                else:
                    self._btn.set_visible(True)
                    self._btn.set_label(self._done_label)  # Используем наш кастомный текст
                    self._btn.set_sensitive(False)
                    self._btn.remove_css_class("suggested-action")
                    self._btn.remove_css_class("circular")
                    self._btn.add_css_class("flat")
                    self._btn.set_size_request(130, -1)
                    self._btn.remove_css_class("success")
        else:
            clear_status(self._status)
            self._status.set_visible(True)
            self._btn.set_visible(True)
            self._btn.set_label(self._orig_label)
            self._btn.set_tooltip_text("")
            self._btn.set_sensitive(True)
            self._btn.remove_css_class("flat")
            self._btn.remove_css_class("circular")
            self._btn.remove_css_class("success")
            self._btn.add_css_class("suggested-action")
            self._btn.set_size_request(130, -1)

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

    def set_undo_done(self, ok):
        if ok:
            config.state_set(self._state_key, False)
            self._set_ui(False)
        else:
            # Если ошибка, возвращаем состояние "Активно"
            self._set_ui(True)

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
            self._prog.set_visible(False)
            
            # Кнопка остается видимой, но становится неактивной с текстом "Установлено"
            self._btn.set_visible(True)
            self._btn.set_label("Установлено")
            self._btn.set_sensitive(False)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
            
            self._trash_btn.set_visible(True)
            self._trash_btn.set_sensitive(True)
        else:
            clear_status(self._status)
            
            # Возвращаем исходный вид кнопки "Установить"
            self._btn.set_visible(True)
            self._btn.set_label("Установить")
            self._btn.set_sensitive(True)
            self._btn.remove_css_class("flat")
            self._btn.add_css_class("suggested-action")
            
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

        if "check" in task:
            threading.Thread(target=self._initial_check, daemon=True).start()

    def _initial_check(self):
        check = self._task["check"]
        ok = False
        if check.get("type") == "path":
            path = os.path.expanduser(check["value"])
            ok = os.path.exists(path)
        
        if ok:
            GLib.idle_add(self._mark_done_init)

    def _mark_done_init(self):
        self.result = True
        set_status_ok(self._status)
        self._btn.set_label("Применено")
        self._btn.set_sensitive(False)
        self._btn.remove_css_class("suggested-action")
        self._btn.add_css_class("flat")
        if self._on_progress:
            self._on_progress()

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

        if self._task.get("type") == "user":
            self._on_log(f"\n▶  {self._task['label']}...\n")
            GLib.timeout_add(110, self._pulse)
            threading.Thread(target=self._run_user, args=(cmd,), daemon=True).start()
            return

        if self._task["id"] == "davinci":
            cmd = [
                "find",
                config.get_dv_cache(), config.get_dv_proxy(),
                "-mindepth", "1", "-delete",
            ]
        self._on_log(f"\n▶  {self._task['label']}...\n")
        GLib.timeout_add(110, self._pulse)
        backend.run_privileged(cmd, self._on_log, self._finish)

    def _run_user(self, cmd):
        try:
            # Запускаем команду от текущего пользователя
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.stdout:
                GLib.idle_add(self._on_log, res.stdout)
            if res.stderr:
                GLib.idle_add(self._on_log, res.stderr)
            GLib.idle_add(self._finish, res.returncode == 0)
        except Exception as e:
            GLib.idle_add(self._on_log, f"Error: {e}\n")
            GLib.idle_add(self._finish, False)

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
