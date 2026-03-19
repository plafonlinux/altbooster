
import json
import os
import shlex
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from core import config
from ui.widgets import (
    make_button, make_scrolled_page, make_icon, make_status_icon,
    set_status_ok, set_status_error, clear_status
)
from ui.common import load_module
from ui.rows import TaskRow


class CacheTaskRow(Adw.ExpanderRow):

    def __init__(self, log_fn, on_progress):
        super().__init__()
        self._log = log_fn
        self._on_progress = on_progress
        self._running = False
        self.result = None

        self.set_title("Очистка кэша")
        self.set_subtitle("Настройте пути для очистки")
        self.add_prefix(make_icon("user-trash-symbolic"))

        self._build_controls()

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
        row = Adw.SwitchRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        val = config.state_get(key)
        if val is None:
            val = default
        row.set_active(val)
        row.connect("notify::active", lambda r, p: config.state_set(key, r.get_active()))
        self.add_row(row)
        return row

    def _refresh_custom_rows(self):
        paths = config.state_get("clean_custom_paths") or []
        for p in list(self._custom_rows_map.keys()):
            if p not in paths:
                self._custom_rows_map[p].unparent()
                del self._custom_rows_map[p]
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
            base = os.path.expanduser("~/.var/app")
            targets.append(shlex.quote(base) + "/*/cache/*")

        _DANGEROUS_PREFIXES = (
            "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/lib32",
            "/proc", "/run", "/sbin", "/sys", "/usr", "/var",
        )
        _DANGEROUS_EXACT = {"/", os.path.expanduser("~")}

        custom = config.state_get("clean_custom_paths") or []
        for p in custom:
            p = p.strip()
            if not p:
                continue
            if p.startswith("~"):
                p = os.path.expanduser(p)
            real = os.path.realpath(p)
            if real in _DANGEROUS_EXACT:
                self._log(f"⚠  Пропущен опасный путь: {p}\n")
                continue
            if any(real == d or real.startswith(d + "/") for d in _DANGEROUS_PREFIXES):
                self._log(f"⚠  Пропущен системный путь: {p}\n")
                continue
            targets.append(shlex.quote(p) + "/*")

        if not targets:
            self._finish(True)
            return

        cmd_str = "shopt -s nullglob; rm -rf " + " ".join(targets)
        self._log(f"\n▶  Очистка кэша ({len(targets)} путей)...\n")
        
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Очистка кэша...")
        
        GLib.timeout_add(110, self._pulse)
        backend.run_privileged(["bash", "-c", cmd_str], self._log, self._finish)

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
        
        win = self.get_root()
        if hasattr(win, "stop_progress"):
            win.stop_progress(ok)
            
        if self._on_progress:
            self._on_progress()


class FstabRow(Adw.ExpanderRow):
    def __init__(self, log_fn):
        super().__init__()
        self._log = log_fn
        self._running = False
        self._new_mounts = []

        self.set_title("Добавить точки монтирования")
        self.set_subtitle("Вставьте строки fstab — директории будут созданы автоматически")
        self.add_prefix(make_icon("drive-harddisk-symbolic"))

        self._status = make_status_icon()
        self._btn = make_button("Применить", width=110)
        self._btn.set_valign(Gtk.Align.CENTER)
        self._btn.connect("clicked", lambda _: self._apply())

        suffix = Gtk.Box(spacing=10)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(self._status)
        suffix.append(self._btn)
        self.add_suffix(suffix)

        self._buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=self._buf)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.NONE)
        tv.set_margin_top(8)
        tv.set_margin_bottom(8)
        tv.set_margin_start(12)
        tv.set_margin_end(12)
        tv.add_css_class("view")

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(120)
        scroll.set_vexpand(False)
        scroll.set_child(tv)

        tv_row = Adw.PreferencesRow()
        tv_row.set_activatable(False)
        tv_row.set_focusable(False)
        tv_row.set_child(scroll)
        self.add_row(tv_row)

    def _apply(self):
        if self._running:
            return

        text = self._buf.get_text(
            self._buf.get_start_iter(), self._buf.get_end_iter(), False
        )
        entries = self._parse_fstab_lines(text)

        if not entries:
            self._log("⚠  Нет корректных строк fstab\n")
            return

        existing = self._read_fstab_mountpoints()
        new_entries = [e for e in entries if e["mountpoint"] not in existing]

        if not new_entries:
            self._log("ℹ  Все точки монтирования уже присутствуют в /etc/fstab\n")
            return

        self._new_mounts = [e["mountpoint"] for e in new_entries]
        self._running = True
        self._btn.set_sensitive(False)
        self._btn.set_label("…")
        clear_status(self._status)

        mounts_args = " ".join(shlex.quote(e["mountpoint"]) for e in new_entries)
        fstab_append = " ".join(shlex.quote(e["line"]) for e in new_entries)
        script = (
            f"mkdir -p {mounts_args} && "
            f"printf '\\n# Добавлено ALT Booster\\n' >> /etc/fstab && "
            f"printf '%s\\n' {fstab_append} >> /etc/fstab && "
            f"mount -a"
        )

        self._log(f"\n▶  Добавление {len(new_entries)} записей в fstab...\n")
        for e in new_entries:
            self._log(f"   {e['line']}\n")
        backend.run_privileged(["bash", "-c", script], self._log, self._done)

    def _done(self, ok):
        self._running = False
        self._btn.set_label("Применить")
        self._btn.set_sensitive(True)
        if ok:
            set_status_ok(self._status)
            self._log("✔  fstab обновлён, диски подключены\n")
            GLib.idle_add(self._show_nautilus_dialog)
        else:
            set_status_error(self._status)
            self._log("✘  Ошибка при обновлении fstab\n")

    def _show_nautilus_dialog(self):
        win = self.get_root()
        dialog = Adw.MessageDialog(transient_for=win)
        dialog.set_heading("Добавить в боковую панель Nautilus?")
        dialog.set_body("Выберите папки для добавления в закладки:")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(4)
        checks = []
        for mp in self._new_mounts:
            cb = Gtk.CheckButton(label=mp)
            cb.set_active(True)
            box.append(cb)
            checks.append((mp, cb))

        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Пропустить")
        dialog.add_response("add", "Добавить")
        dialog.set_default_response("add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "add":
                selected = [mp for mp, cb in checks if cb.get_active()]
                self._add_nautilus_bookmarks(selected)
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _add_nautilus_bookmarks(self, mounts):
        bookmarks_file = os.path.expanduser("~/.config/gtk-3.0/bookmarks")
        os.makedirs(os.path.dirname(bookmarks_file), exist_ok=True)

        existing = set()
        if os.path.exists(bookmarks_file):
            with open(bookmarks_file, encoding="utf-8") as f:
                existing = {line.strip() for line in f}

        new_lines = [f"file://{mp}" for mp in mounts if f"file://{mp}" not in existing]
        if new_lines:
            with open(bookmarks_file, "a", encoding="utf-8") as f:
                for line in new_lines:
                    f.write(line + "\n")
            self._log(f"✔  Добавлено {len(new_lines)} закладок в Nautilus\n")

    @staticmethod
    def _parse_fstab_lines(text):
        entries = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                entries.append({"line": stripped, "mountpoint": parts[1]})
        return entries

    @staticmethod
    def _read_fstab_mountpoints():
        try:
            with open("/etc/fstab", encoding="utf-8") as f:
                mps = set()
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    parts = stripped.split()
                    if len(parts) >= 2:
                        mps.add(parts[1])
                return mps
        except OSError:
            return set()


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

        flatpak_ids = {"flatpak", "flatpak_repair", "flatpak_home"}
        fix_ids = {"fix_gdm_usb", "fix_gsconnect", "disable_tracker"}

        other_tasks = [t for t in all_tasks if t["id"] not in flatpak_ids and t["id"] not in fix_ids]

        self._build_header(body, len(other_tasks) + 1)
        self._build_tasks(body, other_tasks)

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

    def _build_tasks(self, body, tasks):
        cache_group = Adw.PreferencesGroup()
        body.append(cache_group)
        self._cache_row = CacheTaskRow(self._log, self._update_progress)
        self._rows.append(self._cache_row)
        cache_group.add(self._cache_row)

        fstab_group = Adw.PreferencesGroup()
        fstab_group.set_title("Монтирование")
        body.append(fstab_group)
        fstab_group.add(FstabRow(self._log))

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
        self._busy = True
        self._cancel_tasks = False
        self._btn_all.set_sensitive(False)
        self._btn_all.set_label("⏳  Выполняется...")
        
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Выполнение задач обслуживания...", self._cancel_tasks_fn)
            
        threading.Thread(target=self._worker, daemon=True).start()

    def _cancel_tasks_fn(self):
        self._cancel_tasks = True
        self._log("\n⚠  Запрос отмены. Остановка после текущей задачи...\n")

    def _worker(self):
        for row in self._rows:
            if self._cancel_tasks:
                break
            row._done_event.clear()
            GLib.idle_add(row.start)
            row._done_event.wait()
        GLib.idle_add(self._all_done)

    def _all_done(self):
        self._busy = False
        self._btn_all.set_sensitive(True)
        self._btn_all.set_label("Запустить все задачи")

        self._log("\n✔  Все задачи обслуживания выполнены!\n")

        win = self.get_root()
        if hasattr(win, "stop_progress"):
            ok = not getattr(self, "_cancel_tasks", False)
            win.stop_progress(ok)

    def _update_progress(self):
        done = sum(1 for r in self._rows if r.result is not None)
        total = len(self._rows)
        self._prog_bar.set_fraction(done / total if total else 0.0)
        self._prog_lbl.set_label(f"{done} / {total} задач")


