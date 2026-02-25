"""Вкладка «Обслуживание» — задачи из modules/maintenance.json."""

import json
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
import config
from widgets import make_button, make_scrolled_page
from ui.common import load_module
from ui.rows import TaskRow


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
            tasks = data.get("tasks", [])
        except (OSError, json.JSONDecodeError):
            tasks = []

        self._build_header(body, len(tasks))
        self._build_tasks(body, tasks)

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
        group = Adw.PreferencesGroup()
        group.set_title("Задачи обслуживания")
        body.append(group)

        is_btrfs = config.is_btrfs()
        btrfs_ids = {"btrfs_bal", "btrfs_defrag", "btrfs_scrub"}

        for task in tasks:
            row = TaskRow(task, self._log, self._update_progress)
            if task["id"] in btrfs_ids and not is_btrfs:
                row.set_sensitive(False)
                row.set_tooltip_text("Недоступно: не Btrfs")
            self._rows.append(row)
            group.add(row)

    def set_sensitive_all(self, sensitive):
        self._btn_all.set_sensitive(sensitive)
        for r in self._rows:
            r._btn.set_sensitive(sensitive)

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
