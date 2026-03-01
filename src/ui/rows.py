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
        # Всегда запускаем фоновую проверку реального состояния,
        # чтобы кэш не показывал устаревший статус (например, после внешнего изменения sudo).
        if "kbd" not in state_key and check_fn is not None:
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
        
        # Нормализация источников: всегда работаем со списком
        if "sources" in app:
            self._sources = app["sources"]
        elif "source" in app:
            self._sources = [app["source"]]
        else:
            self._sources = []

        self._log = log_fn
        self._on_change = on_change_cb
        self._installing = False
        self._state_key = f"app_{app['id']}"
        self._installed_source_index = -1
        self._selected_source_index = 0

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

        # Контейнер для бейджиков источников
        self._badges_box = Gtk.Box(spacing=4)
        self._badges_box.set_valign(Gtk.Align.CENTER)
        suffix.append(self._badges_box)

        suffix.append(self._btn)
        suffix.append(self._trash_btn)
        self.add_suffix(suffix)

        self._update_badges()
        threading.Thread(target=self._check, daemon=True).start()

    def is_installed(self):
        return config.state_get(self._state_key) is True

    def _check(self):
        # Проверяем все источники
        installed = False
        installed_idx = -1
        
        for i, src in enumerate(self._sources):
            if backend.check_app_installed(src):
                installed = True
                installed_idx = i
                break
        
        self._installed_source_index = installed_idx
        config.state_set(self._state_key, installed)
        GLib.idle_add(self._set_installed_ui, installed)

    def _update_badges(self):
        # Очищаем старые бейджики
        child = self._badges_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self._badges_box.remove(child)
            child = next_child

        if self.is_installed():
            # Если установлено: показываем только один зеленый бейдж
            idx = self._installed_source_index
            if idx >= 0 and idx < len(self._sources):
                src = self._sources[idx]
                label = src.get("label", "Source")
            else:
                label = "Установлено"
                
            btn = Gtk.Button(label=label)
            btn.add_css_class("success") # Зеленый
            btn.add_css_class("pill")    # Скругленный
            btn.set_sensitive(False)     # Неактивный (просто индикатор)
            self._badges_box.append(btn)
        else:
            # Если не установлено: показываем все варианты
            # Если вариант всего один, можно показать его для информации или скрыть.
            # Покажем всегда, чтобы было видно, откуда будет установка.
            for i, src in enumerate(self._sources):
                btn = Gtk.Button(label=src.get("label", "Source"))
                btn.add_css_class("pill")
                if i == self._selected_source_index:
                    btn.add_css_class("suggested-action") # Синий (выбран)
                else:
                    btn.add_css_class("flat") # Бледный (не выбран)
                
                btn.connect("clicked", lambda b, idx=i: self.set_selected_source(idx))
                self._badges_box.append(btn)

    def set_selected_source(self, idx):
        if idx < 0 or idx >= len(self._sources):
            return
        self._selected_source_index = idx
        self._update_badges()

    def _set_installed_ui(self, installed):
        self._update_badges()
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
            
        # Определяем источник: выбранный в дропдауне
        idx = self._selected_source_index
        if idx < 0 or idx >= len(self._sources):
            idx = 0
        
        if not self._sources:
             self._log("\n✘  Нет источников установки для этого приложения.\n")
             return

        src = self._sources[idx]

        self._installing = True
        self._btn.set_sensitive(False)
        self._badges_box.set_sensitive(False)
        self._btn.set_label("…")
        self._prog.set_visible(True)
        self._prog.set_fraction(0.0)
        GLib.timeout_add(120, self._pulse)
        self._log(f"\n▶  Установка {self._app['label']} ({src['label']})...\n")
        
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Установка {self._app['label']}...")

        cmd = list(src["cmd"])
        is_epm = False
        if cmd and cmd[0] == "epm":
            is_epm = True
            # Автоматически добавляем -y для epm install/remove
            if len(cmd) > 1 and cmd[1] in ("install", "-i", "remove", "-e") and "-y" not in cmd:
                cmd.insert(2, "-y")

        # Логика повторной попытки при 404 (устаревшие индексы)
        self._install_needs_update = False
        def _log_wrapper(text):
            if ("404" in text and "Not Found" in text) or "Unable to fetch some archives" in text:
                self._install_needs_update = True
            self._log(text)

        def _on_update_done(ok):
            if not ok:
                self._log("✘  Не удалось обновить индексы. Прерываю.\n")
                self._install_done(False)
                return
            self._log(f"\n▶  Повторная попытка установки {self._app['label']}...\n")
            # При повторе используем обычный логгер
            if is_epm:
                backend.run_epm(cmd, self._log, self._install_done)
            else:
                backend.run_privileged(cmd, self._log, self._install_done)

        def _first_attempt_done(ok):
            if not ok and self._install_needs_update:
                self._log("\n⚠  Обнаружены устаревшие индексы. Выполняю обновление (apt-get update)...\n")
                backend.run_privileged(["apt-get", "update"], self._log, _on_update_done)
            else:
                self._install_done(ok)

        if is_epm:
            backend.run_epm(cmd, _log_wrapper, _first_attempt_done)
        else:
            backend.run_privileged(cmd, _log_wrapper, _first_attempt_done)

    def _on_uninstall(self, _):
        if self._installing:
            return
            
        # Удаляем то, что установлено
        idx = self._installed_source_index
        if idx < 0:
            # Если не знаем что установлено, берем текущий выбор
            idx = self._selected_source_index
        
        if idx < 0 or idx >= len(self._sources):
            idx = 0
            
        if not self._sources:
             return

        src = self._sources[idx]

        self._installing = True
        self._trash_btn.set_sensitive(False)
        self._prog.set_visible(True)
        self._prog.set_fraction(0.0)
        GLib.timeout_add(120, self._pulse)
        
        kind, pkg = src["check"]
        if kind == "flatpak":
            if isinstance(pkg, str):
                pkg = pkg.strip().split()[0]
            cmd = ["flatpak", "uninstall", "-y", pkg]
        elif kind == "rpm":
            if isinstance(pkg, str):
                pkg = pkg.strip().split()[0]
            cmd = ["epm", "-e", "-y", pkg]
        else:
            # Fallback для кастомных скриптов
            if "monitor-control" in str(src):
                cmd = [
                    "rm", "-rf",
                    os.path.expanduser("~/.local/share/monitor-control"),
                    os.path.expanduser("~/Monic"),
                ]
            else:
                self._log("⚠ Неизвестный метод удаления для этого источника. Попробуйте удалить вручную.\n")
                self._installing = False
                self._set_installed_ui(True)
                return

        self._log(f"\n▶  Удаление {self._app['label']}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Удаление {self._app['label']}...")

        if cmd and cmd[0] == "epm":
            backend.run_epm(cmd, self._log, self._uninstall_done)
        else:
            backend.run_privileged(cmd, self._log, self._uninstall_done)

    def _pulse(self):
        if self._installing:
            self._prog.pulse()
            return True
        return False

    def _install_done(self, ok):
        self._installing = False
        self._prog.set_visible(False)
        win = self.get_root()
        if ok:
            self._log(f"✔  {self._app['label']} установлен!\n")
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
            config.state_set(self._state_key, True)
            # Обновляем индекс установленного источника
            self._installed_source_index = self._selected_source_index
            self._set_installed_ui(True)
        else:
            self._log(f"✘  Ошибка установки {self._app['label']}\n")
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
            self._btn.set_sensitive(True)
            self._badges_box.set_sensitive(True)
            self._btn.set_label("Повторить")

    def _uninstall_done(self, ok):
        self._installing = False
        self._prog.set_visible(False)
        win = self.get_root()
        if ok:
            self._log(f"✔  {self._app['label']} удалён!\n")
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
            config.state_set(self._state_key, False)
            self._installed_source_index = -1
            self._set_installed_ui(False)
        else:
            self._log(f"✘  Ошибка удаления {self._app['label']}\n")
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
            self._trash_btn.set_sensitive(True)


class TaskRow(Adw.ActionRow):
    """Строка задачи обслуживания с прогрессом."""

    def __init__(self, task, on_log, on_progress, btn_label="Запустить"):
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
        self._btn = make_button(btn_label, width=110)
        self._btn.connect("clicked", lambda _: self.start())

        right = Gtk.Box(spacing=10)
        right.set_valign(Gtk.Align.CENTER)
        right.append(self._prog)
        right.append(self._status)
        right.append(self._btn)
        self.add_suffix(right)

        if "check" in task:
            threading.Thread(target=self._initial_check, daemon=True).start()

    def refresh_check(self):
        if "check" in self._task:
            threading.Thread(target=self._initial_check, daemon=True).start()

    def _initial_check(self):
        check = self._task["check"]
        ok = False
        if check.get("type") == "path":
            path = os.path.expanduser(check["value"])
            if os.path.exists(path):
                ok = True
            elif backend.get_sudo_password():
                # Если прав нет, пробуем через sudo (для системных путей)
                if backend.run_privileged_sync(["test", "-e", path], lambda _: None):
                    ok = True
        
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
            win = self.get_root()
            if hasattr(win, "start_progress"): win.start_progress(f"Выполнение: {self._task['label']}...")
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
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Выполнение: {self._task['label']}...")
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
        win = self.get_root()
        if ok:
            set_status_ok(self._status)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")

            # Если у задачи есть проверка (это фикс/настройка), то после успеха блокируем кнопку
            if "check" in self._task:
                self._btn.set_label("Применено")
                self._btn.set_sensitive(False)
                self._on_log(f"✔  Готово: {self._task['label']}\n")
                if hasattr(win, "stop_progress"): win.stop_progress(ok)
                self._on_progress()
                return
        else:
            set_status_error(self._status)
        self._btn.set_label("Повтор")
        self._btn.set_sensitive(True)
        self._on_log(f"{'✔  Готово' if ok else '✘  Ошибка'}: {self._task['label']}\n")
        if hasattr(win, "stop_progress"): win.stop_progress(ok)
        self._on_progress()
