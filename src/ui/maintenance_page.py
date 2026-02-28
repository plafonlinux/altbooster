"""Вкладка «Обслуживание» — задачи из modules/maintenance.json."""

import json
import os
import shlex
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
import config
from widgets import (
    make_button, make_scrolled_page, make_icon, make_status_icon,
    set_status_ok, set_status_error, clear_status
)
from ui.common import load_module
from ui.rows import TaskRow, SettingRow


class CacheTaskRow(Adw.ExpanderRow):
    """Специальная строка для очистки кэша с настройками."""

    def __init__(self, log_fn, on_progress):
        super().__init__()
        self._log = log_fn
        self._on_progress = on_progress
        self._running = False
        self.result = None

        self.set_title("Очистка кэша")
        self.set_subtitle("Настройте пути для очистки")
        self.add_prefix(make_icon("user-trash-symbolic"))

        # Элементы управления внутри
        self._build_controls()

        # Суффикс: Прогресс + Статус + Кнопка
        self._prog = Gtk.ProgressBar()
        self._prog.set_size_request(100, -1)
        self._prog.set_valign(Gtk.Align.CENTER)
        self._prog.set_visible(False)

        self._status = make_status_icon()

        self._btn = make_button("Запустить", width=110)
        self._btn.connect("clicked", lambda _: self.start())
        self._btn.set_valign(Gtk.Align.CENTER)

        suffix = Gtk.Box(spacing=10)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(self._prog)
        suffix.append(self._status)
        suffix.append(self._btn)
        self.add_suffix(suffix)

    def _build_controls(self):
        self._sw_cache = self._add_switch("Системный кэш", "~/.cache", "clean_sys", True)
        self._sw_thumb = self._add_switch("Миниатюры", "~/.thumbnails", "clean_thumb", True)
        self._sw_trash = self._add_switch("Корзина", "~/.local/share/Trash", "clean_trash", True)
        self._sw_flatpak = self._add_switch("Кэш Flatpak", "~/.var/app/*/cache", "clean_flatpak", False)

        self._entry = Adw.EntryRow()
        self._entry.set_title("Добавить свой путь")
        self._entry.set_show_apply_button(True)
        self._entry.connect("apply", self._on_add_path)
        self.add_row(self._entry)

        self._custom_rows_map = {}
        self._refresh_custom_rows()

    def _add_switch(self, title, subtitle, key, default):
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        val = config.state_get(key)
        if val is None:
            val = default
        sw.set_active(val)
        sw.connect("notify::active", lambda s, p: config.state_set(key, s.get_active()))
        row.add_suffix(sw)
        self.add_row(row)
        return sw

    def _refresh_custom_rows(self):
        paths = config.state_get("clean_custom_paths") or []
        # Удаляем удаленные
        for p in list(self._custom_rows_map.keys()):
            if p not in paths:
                self._custom_rows_map[p].unparent()
                del self._custom_rows_map[p]
        # Добавляем новые
        for p in paths:
            if p not in self._custom_rows_map:
                row = Adw.ActionRow()
                row.set_title(p)
                btn = Gtk.Button(icon_name="user-trash-symbolic")
                btn.add_css_class("flat")
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", lambda _, path=p: self._remove_path(path))
                row.add_suffix(btn)
                self.add_row(row)
                self._custom_rows_map[p] = row

    def _on_add_path(self, entry):
        path = entry.get_text().strip()
        if not path:
            return
        paths = config.state_get("clean_custom_paths") or []
        if path not in paths:
            paths.append(path)
            config.state_set("clean_custom_paths", paths)
            self._refresh_custom_rows()
        entry.set_text("")

    def _remove_path(self, path):
        paths = config.state_get("clean_custom_paths") or []
        if path in paths:
            paths.remove(path)
            config.state_set("clean_custom_paths", paths)
            self._refresh_custom_rows()

    def start(self):
        if self._running:
            return
        self._running = True
        self.result = None
        self._btn.set_sensitive(False)
        self._btn.set_label("…")
        clear_status(self._status)
        self._prog.set_visible(True)
        self._prog.set_fraction(0.0)

        targets = []
        if self._sw_cache.get_active():
            targets.append(shlex.quote(os.path.expanduser("~/.cache")) + "/*")
        if self._sw_thumb.get_active():
            targets.append(shlex.quote(os.path.expanduser("~/.thumbnails")) + "/*")
        if self._sw_trash.get_active():
            targets.append(shlex.quote(os.path.expanduser("~/.local/share/Trash")) + "/*")
        if self._sw_flatpak.get_active():
            # ~/.var/app/*/cache/* — здесь важно не экранировать звездочки
            base = os.path.expanduser("~/.var/app")
            targets.append(shlex.quote(base) + "/*/cache/*")

        custom = config.state_get("clean_custom_paths") or []
        for p in custom:
            if p.strip() in ["/", "~", os.path.expanduser("~")]:
                continue
            if p.startswith("~"):
                p = os.path.expanduser(p)
            targets.append(shlex.quote(p) + "/*")

        if not targets:
            self._finish(True)
            return

        cmd_str = "shopt -s nullglob; rm -rf " + " ".join(targets)
        self._log(f"\n▶  Очистка кэша ({len(targets)} путей)...\n")
        
        GLib.timeout_add(110, self._pulse)
        threading.Thread(target=self._run, args=(["bash", "-c", cmd_str],), daemon=True).start()

    def _run(self, cmd):
        import subprocess
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.stdout: GLib.idle_add(self._log, res.stdout)
            if res.stderr: GLib.idle_add(self._log, res.stderr)
            GLib.idle_add(self._finish, res.returncode == 0)
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}\n")
            GLib.idle_add(self._finish, False)

    def _pulse(self):
        if self._running:
            self._prog.pulse()
            return True
        return False

    def _finish(self, ok):
        self._running = False
        self.result = ok
        self._prog.set_visible(False)
        if ok:
            set_status_ok(self._status)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        else:
            set_status_error(self._status)
        self._btn.set_label("Повтор")
        self._btn.set_sensitive(True)
        self._log(f"{'✔  Кэш очищен' if ok else '✘  Ошибка очистки'}\n")
        if self._on_progress:
            self._on_progress()


class MaintenancePage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._rows = []
        self._busy = False

        scroll, body = make_scrolled_page()
        self.append(scroll)

        try:
            data = load_module("maintenance")
            all_tasks = data.get("tasks", [])
        except (OSError, json.JSONDecodeError):
            all_tasks = []

        flatpak_ids = ["flatpak", "flatpak_home"]
        fix_ids = ["fix_gdm_usb", "fix_gsconnect", "disable_tracker"]

        flatpak_tasks = [t for t in all_tasks if t["id"] in flatpak_ids]
        fix_tasks = [t for t in all_tasks if t["id"] in fix_ids]
        other_tasks = [t for t in all_tasks if t["id"] not in flatpak_ids and t["id"] not in fix_ids]

        self._build_header(body, len(all_tasks) + 1) # +1 для CacheTaskRow
        self._build_tasks(body, other_tasks)
        self._build_flatpak_group(body, flatpak_tasks)
        self._build_fixes_group(body, fix_tasks)

    def _build_header(self, body, total):
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        body.append(container)

        header = Gtk.Box()
        lbl = Gtk.Label(label="Общий прогресс")
        lbl.set_halign(Gtk.Align.START)
        lbl.add_css_class("caption")
        lbl.set_hexpand(True)
        self._prog_lbl = Gtk.Label(label=f"0 / {total} задач")
        self._prog_lbl.add_css_class("caption")
        header.append(lbl)
        header.append(self._prog_lbl)
        container.append(header)

        self._prog_bar = Gtk.ProgressBar()
        self._prog_bar.set_hexpand(True)
        container.append(self._prog_bar)

        self._btn_all = make_button("Запустить все задачи")
        self._btn_all.set_halign(Gtk.Align.CENTER)
        self._btn_all.connect("clicked", self._run_all)
        body.append(self._btn_all)

    def _build_flatpak_group(self, body, tasks):
        group = Adw.PreferencesGroup()
        group.set_title("Flatpak и Flathub")
        group.set_description("Управление подсистемой Flatpak")
        body.append(group)

        # 1. Подключить Flathub
        row = SettingRow(
            "application-x-addon-symbolic",       "Подключить Flathub",          "Устанавливает flatpak и flathub",               "Включить",     self._on_flathub,        backend.is_flathub_enabled,            "setting_flathub", "Активировано", self._on_flathub_undo, "Удалить"
        )
        group.add(row)

        # 2. Задачи (Уборка, Доступ к Home)
        for task in tasks:
            row = TaskRow(task, self._log, self._update_progress)
            self._rows.append(row)
            group.add(row)

    def _build_fixes_group(self, body, tasks):
        if not tasks:
            return
        group = Adw.PreferencesGroup()
        group.set_title("Различные баги и фиксы")
        body.append(group)

        for task in tasks:
            row = TaskRow(task, self._log, self._update_progress, btn_label="Применить")
            self._rows.append(row)
            group.add(row)

    def _on_flathub(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        row.set_working()
        self._log("\n▶  Установка Flatpak и Flathub...\n")

        def step2(ok):
            if not ok:
                row.set_done(False)
                return
            backend.run_privileged(
                ["apt-get", "install", "-y", "flatpak-repo-flathub"],
                self._log,
                lambda ok2: (row.set_done(ok2), self._log("✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n")),
            )

        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, step2)

    def _on_flathub_undo(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        row.set_working()
        self._log("\n▶  Удаление Flatpak и Flathub...\n")
        backend.run_privileged(
            ["apt-get", "remove", "-y", "flatpak", "flatpak-repo-flathub"],
            self._log,
            lambda ok: (row.set_undo_done(ok), self._log("✔  Flatpak удалён!\n" if ok else "✘  Ошибка\n")),
        )

    def _build_tasks(self, body, tasks):
        # Отдельная группа для очистки кэша
        cache_group = Adw.PreferencesGroup()
        body.append(cache_group)
        self._cache_row = CacheTaskRow(self._log, self._update_progress)
        self._rows.append(self._cache_row)
        cache_group.add(self._cache_row)

        # Группа для остальных задач
        tasks_group = Adw.PreferencesGroup()
        tasks_group.set_title("Задачи обслуживания")
        body.append(tasks_group)

        is_btrfs = config.is_btrfs()
        btrfs_ids = {"btrfs_bal", "btrfs_defrag", "btrfs_scrub"}

        for task in tasks:
            row = TaskRow(task, self._log, self._update_progress)
            if task["id"] in btrfs_ids and not is_btrfs:
                row.set_sensitive(False)
                row.set_tooltip_text("Недоступно: не Btrfs")
            self._rows.append(row)
            tasks_group.add(row)

    def set_sensitive_all(self, sensitive):
        self._btn_all.set_sensitive(sensitive)
        for r in self._rows:
            r._btn.set_sensitive(sensitive)

    def refresh_checks(self):
        for row in self._rows:
            if isinstance(row, TaskRow):
                row.refresh_check()

    def _run_all(self, _):
        if self._busy:
            return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        self._busy = True
        self._btn_all.set_sensitive(False)
        self._btn_all.set_label("⏳  Выполняется...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        for row in self._rows:
            GLib.idle_add(row.start)
            while row._running or row.result is None:
                time.sleep(0.2)
        GLib.idle_add(self._all_done)

    def _all_done(self):
        self._busy = False
        self._btn_all.set_sensitive(True)
        self._btn_all.set_label("Запустить все задачи")
        self._log("\n✔  Готово!\n")

    def _update_progress(self):
        done = sum(1 for r in self._rows if r.result is not None)
        total = len(self._rows)
        self._prog_bar.set_fraction(done / total if total else 0.0)
        self._prog_lbl.set_label(f"{done} / {total} задач")
