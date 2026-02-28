"""
dynamic_page.py — универсальный движок Data-Driven UI для ALT Booster.

Архитектура:
  run_check(check)      — проверка статуса по JSON-описанию
  ActionDispatcher      — выполнение action из JSON в фоновом потоке
  RowFactory            — фабрика виджетов Adw из JSON-описания строки
  DynamicPage           — Gtk.Box, строит интерфейс из JSON-словаря

Поддерживаемые типы строк (row.type):
  command_row  — кнопка выполнения команды с индикатором статуса
  dropdown_row — выпадающий список + кнопка применения
  file_row     — выбор файла + кнопка применения

Поддерживаемые типы action:
  privileged   — sudo через backend.run_privileged
  epm          — epm через backend.run_epm
  shell        — subprocess без root
  gsettings    — backend.run_gsettings
  open_url     — Gio.AppInfo.launch_default_for_uri
  builtin      — вызов функции из BUILTIN_REGISTRY

Поддерживаемые типы check:
  rpm              — rpm -q <value>
  flatpak          — flatpak list | grep <value>
  which            — which <value>
  path             — os.path.exists(~/<value>)
  systemd          — systemctl is-enabled <value>
  gsettings        — gsettings get schema key == expected
  gsettings_contains — gsettings get schema key contains value
  builtin          — вызов check-функции из BUILTIN_REGISTRY
"""

from __future__ import annotations

import shutil
import os
import subprocess
import threading
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import backend
import config
from builtin_actions import BUILTIN_REGISTRY
from widgets import (
    make_icon, make_button, make_status_icon,
    set_status_ok, set_status_error, clear_status, make_suffix_box,
)


# ─────────────────────────────────────────────────────────────────────────────
# run_check — проверка статуса строки
# ─────────────────────────────────────────────────────────────────────────────

def run_check(check: dict | None) -> bool:
    """Выполняет проверку статуса из JSON-описания check."""
    if not check:
        return False
    kind = check.get("type")

    if kind == "rpm":
        return subprocess.run(
            ["rpm", "-q", check["value"]], capture_output=True,
        ).returncode == 0

    if kind == "flatpak":
        r = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True, text=True,
        )
        return check["value"] in r.stdout

    if kind == "which":
        return subprocess.run(
            ["which", check["value"]], capture_output=True,
        ).returncode == 0

    if kind == "path":
        return os.path.exists(os.path.expanduser(check["value"]))

    if kind == "systemd":
        return subprocess.run(
            ["systemctl", "is-enabled", check["value"]], capture_output=True,
        ).returncode == 0

    if kind == "gsettings":
        value = backend.gsettings_get(check["schema"], check["key"])
        return check.get("expected", "") in value

    if kind == "gsettings_contains":
        value = backend.gsettings_get(check["schema"], check["key"])
        return check.get("value", "") in value

    if kind == "builtin":
        fn = BUILTIN_REGISTRY.get(check.get("fn", ""))
        if fn:
            try:
                return bool(fn(None, None))
            except Exception:
                return False

    return False


# ─────────────────────────────────────────────────────────────────────────────
# ActionDispatcher — выполнение action из JSON
# ─────────────────────────────────────────────────────────────────────────────

class ActionDispatcher:
    """Выполняет action из JSON в фоновом потоке."""

    def __init__(self, page: DynamicPage) -> None:
        self._page = page

    def dispatch(
        self,
        action: dict,
        on_done: Callable[[bool], None] | None = None,
        arg: Any = None,
    ) -> None:
        """Запускает action в фоновом потоке, on_done(ok) вызывается в главном."""
        threading.Thread(
            target=self._run,
            args=(action, on_done, arg),
            daemon=True,
        ).start()

    def _run(self, action: dict, on_done: Callable | None, arg: Any) -> None:
        ok = False
        kind = action.get("type")
        page = self._page

        try:
            if kind == "privileged":
                ok = backend.run_privileged_sync(action["cmd"], page.log)

            elif kind == "epm":
                ok = backend.run_epm_sync(action["cmd"], page.log)

            elif kind == "shell":
                r = subprocess.run(action["cmd"], capture_output=True, text=True)
                if r.stdout:
                    GLib.idle_add(page.log, r.stdout)
                if r.stderr:
                    GLib.idle_add(page.log, r.stderr)
                ok = r.returncode == 0

            elif kind == "gsettings":
                ok = backend.run_gsettings(action["args"])

            elif kind == "open_url":
                GLib.idle_add(Gio.AppInfo.launch_default_for_uri, action["url"], None)
                ok = True

            elif kind == "builtin":
                fn_name = action.get("fn", "")
                fn = BUILTIN_REGISTRY.get(fn_name)
                if fn:
                    # Создаем "безопасную" обертку, чтобы встроенные функции не роняли GTK
                    class SafePage:
                        def __init__(self, real_page):
                            self._page = real_page
                            
                        def log(self, text):
                            GLib.idle_add(self._page.log, text)
                            
                        def __getattr__(self, name):
                            return getattr(self._page, name)
                            
                    safe_page = SafePage(page)
                    ok = bool(fn(safe_page, arg))
                else:
                    GLib.idle_add(page.log, f"\n✘  Неизвестная builtin: {fn_name}\n")

        except Exception as exc:
            GLib.idle_add(page.log, f"\n✘  Ошибка: {exc}\n")

        if on_done:
            GLib.idle_add(on_done, ok)

        # Обновляем всю страницу, если действие прошло успешно
        if ok:
            page.refresh()


# ─────────────────────────────────────────────────────────────────────────────
# RowFactory — строит Adw.ActionRow из JSON-описания
# ─────────────────────────────────────────────────────────────────────────────

class RowFactory:
    def __init__(self, page: DynamicPage) -> None:
        self._page = page
        self._dispatcher = ActionDispatcher(page)

    def build(self, rd: dict) -> Adw.ActionRow:
        row_type = rd.get("type", "command_row")
        if row_type == "command_row":
            return self._command_row(rd)
        if row_type == "dropdown_row":
            return self._dropdown_row(rd)
        if row_type == "file_row":
            return self._file_row(rd)
        # fallback
        row = Adw.ActionRow()
        row.set_title(rd.get("title", "?"))
        return row

    # ── command_row ───────────────────────────────────────────────────────────

    def _command_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", ""))
        if rd.get("icon"):
            row.add_prefix(make_icon(rd["icon"]))

        status = make_status_icon()
        style = rd.get("button_style", "suggested-action")
        btn = make_button(rd.get("button_label", "Запустить"), style=style)
        btn.set_sensitive(False)

        done_label = rd.get("button_done_label")
        orig_label = rd.get("button_label", "Запустить")
        action = rd.get("action", {})

        def _on_click(_b: Gtk.Button) -> None:
            btn.set_sensitive(False)
            btn.set_label("…")
            self._page.log(f"\n▶  {rd.get('title', '')}...\n")
            self._dispatcher.dispatch(action, on_done=_on_done)

        def _on_done(ok: bool) -> None:
            if ok:
                set_status_ok(status)
                if done_label:
                    btn.set_label(done_label)
                    btn.set_sensitive(False)
                    btn.remove_css_class("suggested-action")
                    btn.add_css_class("flat")
                else:
                    btn.set_label(orig_label)
                    btn.set_sensitive(True)
            else:
                set_status_error(status)
                btn.set_label("Повторить")
                btn.set_sensitive(True)

        btn.connect("clicked", _on_click)

        row._dp_status = status
        row._dp_button = btn
        row._dp_done_label = done_label
        row._dp_orig_label = orig_label
        row._dp_check = rd.get("check")

        row.add_suffix(make_suffix_box(status, btn))
        return row

    # ── dropdown_row ──────────────────────────────────────────────────────────

    def _dropdown_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", ""))
        if rd.get("icon"):
            row.add_prefix(make_icon(rd["icon"]))

        options = rd.get("options", [])
        dropdown = Gtk.DropDown.new_from_strings(options)
        dropdown.set_valign(Gtk.Align.CENTER)

        state_key = rd.get("state_key")
        if state_key:
            saved = config.state_get(state_key)
            if saved and saved in options:
                dropdown.set_selected(options.index(saved))

        status = make_status_icon()
        btn = make_button(rd.get("button_label", "Применить"), width=120)
        action = rd.get("action", {})

        def _on_click(_b: Gtk.Button) -> None:
            idx = dropdown.get_selected()
            selected = options[idx] if idx < len(options) else ""
            btn.set_sensitive(False)
            btn.set_label("…")
            self._page.log(f"\n▶  {rd.get('title', '')}: {selected}...\n")
            self._dispatcher.dispatch(action, on_done=_on_done, arg=selected)

        def _on_done(ok: bool) -> None:
            if ok:
                set_status_ok(status)
            else:
                set_status_error(status)
            btn.set_label(rd.get("button_label", "Применить"))
            btn.set_sensitive(True)

        btn.connect("clicked", _on_click)

        row._dp_status = status
        row._dp_button = btn
        row._dp_check = None
        row._dp_state_key = state_key

        row.add_suffix(make_suffix_box(dropdown, status, btn))
        return row

    # ── file_row ──────────────────────────────────────────────────────────────

    def _file_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", "Файл не выбран"))
        if rd.get("icon"):
            row.add_prefix(make_icon(rd["icon"]))

        status = make_status_icon()
        pick_btn = make_button(rd.get("pick_label", "Выбрать файл"), width=150, style="flat")
        apply_btn = make_button(rd.get("apply_label", "Применить"), width=170)
        apply_btn.set_sensitive(False)

        action = rd.get("action", {})
        state_key = rd.get("state_key")
        selected_path: list[str] = [""]

        if state_key:
            saved = config.state_get(state_key)
            if saved and os.path.exists(saved):
                row.set_subtitle(f"Применён: {os.path.basename(saved)}")

        def _on_pick(_b: Gtk.Button) -> None:
            dialog = Gtk.FileDialog()
            dialog.set_title("Выберите файл")
            ff_data = rd.get("file_filter")
            if ff_data:
                ff = Gtk.FileFilter()
                ff.set_name(ff_data.get("name", "Files"))
                for pat in ff_data.get("patterns", []):
                    ff.add_pattern(pat)
                store = Gio.ListStore.new(Gtk.FileFilter)
                store.append(ff)
                dialog.set_filters(store)
            root = self._page.get_root()
            dialog.open(root, None, _on_picked)

        def _on_picked(dialog: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = dialog.open_finish(res)
                if f:
                    path = f.get_path()
                    selected_path[0] = path
                    row.set_subtitle(os.path.basename(path))
                    apply_btn.set_sensitive(True)
                    clear_status(status)
            except GLib.Error:
                pass

        def _on_apply(_b: Gtk.Button) -> None:
            apply_btn.set_sensitive(False)
            apply_btn.set_label("…")
            self._page.log(f"\n▶  Применение: {os.path.basename(selected_path[0])}...\n")
            self._dispatcher.dispatch(action, on_done=_on_done, arg=selected_path[0])

        def _on_done(ok: bool) -> None:
            if ok:
                set_status_ok(status)
            else:
                set_status_error(status)
            apply_btn.set_label(rd.get("apply_label", "Применить"))
            if ok:
                apply_btn.set_sensitive(False)
                apply_btn.remove_css_class("suggested-action")
                apply_btn.add_css_class("flat")
                if state_key:
                    row.set_subtitle(f"Применён: {os.path.basename(selected_path[0])}")
            else:
                apply_btn.set_sensitive(True)

        pick_btn.connect("clicked", _on_pick)
        apply_btn.connect("clicked", _on_apply)

        row._dp_status = status
        row._dp_check = None
        row.add_suffix(make_suffix_box(status, pick_btn, apply_btn))
        return row


# ─────────────────────────────────────────────────────────────────────────────
# DynamicPage — главный класс
# ─────────────────────────────────────────────────────────────────────────────

class DynamicPage(Gtk.Box):
    """
    Универсальная вкладка, строящая интерфейс из JSON-словаря.

    Параметры:
        page_data : dict — распарсенный JSON (из modules/*.json)
        log_fn    : Callable[[str], None] — функция вывода в лог
    """

    def __init__(self, page_data: dict, log_fn: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.log = log_fn
        self._page_data = page_data
        self._rows_with_checks: list[Adw.ActionRow] = []
        self._factory = RowFactory(self)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(20)
        body.set_margin_bottom(20)
        body.set_margin_start(20)
        body.set_margin_end(20)

        scroll.set_child(body)
        self.append(scroll)

        self._build(body)

        # Одноразовый фоновый поллинг статусов
        threading.Thread(target=self._poll_checks, daemon=True).start()

    def _build(self, body: Gtk.Box) -> None:
        for group_data in self._page_data.get("groups", []):
            if "requires" in group_data and subprocess.run(["which", group_data["requires"]], capture_output=True).returncode != 0:
                continue

            group = Adw.PreferencesGroup()
            group.set_title(group_data.get("title", ""))
            if group_data.get("description"):
                group.set_description(group_data["description"])
            body.append(group)

            for row_data in group_data.get("rows", []):
                if "requires" in row_data and subprocess.run(["which", row_data["requires"]], capture_output=True).returncode != 0:
                    continue
                row = self._factory.build(row_data)
                group.add(row)
                if hasattr(row, "_dp_check"):
                    self._rows_with_checks.append(row)

    def _poll_checks(self) -> None:
        """Проверяет статус каждой строки и обновляет UI через GLib.idle_add."""
        for row in self._rows_with_checks:
            check = getattr(row, "_dp_check", None)
            ok = run_check(check)
            GLib.idle_add(self._apply_check_result, row, ok)

    def _apply_check_result(self, row: Adw.ActionRow, ok: bool) -> None:
        status = getattr(row, "_dp_status", None)
        btn = getattr(row, "_dp_button", None)
        done_label = getattr(row, "_dp_done_label", None)
        orig_label = getattr(row, "_dp_orig_label", None)

        if ok:
            if status:
                set_status_ok(status)
            if btn:
                if done_label:
                    btn.set_label(done_label)
                    btn.set_sensitive(False)
                    btn.remove_css_class("suggested-action")
                    btn.add_css_class("flat")
                else:
                    btn.set_sensitive(False)
        else:
            if status:
                clear_status(status)
            if btn:
                btn.set_sensitive(True)
                if orig_label:
                    btn.set_label(orig_label)
                btn.remove_css_class("flat")
                btn.add_css_class("suggested-action")

    def refresh(self) -> None:
        """Запускает фоновую перепроверку всех статусов на странице."""
        threading.Thread(target=self._poll_checks, daemon=True).start()

    def _show_reboot_dialog(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Перезагрузить систему?",
            body="Все несохранённые данные будут потеряны.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reboot", "Перезагрузить")
        dialog.set_response_appearance("reboot", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _do(_d: Adw.AlertDialog, response: str) -> None:
            if response == "reboot":
                backend.run_privileged(["reboot"], self.log, lambda _: None)

        dialog.connect("response", _do)
        dialog.present(self.get_root())
