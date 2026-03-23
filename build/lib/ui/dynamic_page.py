from __future__ import annotations

import shutil
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from core import backend
from core import config
from tabs.terminal_actions import (
    check_ptyxis_default, set_ptyxis_default,
    check_shortcut_1, set_shortcut_1,
    check_shortcut_2, set_shortcut_2,
    check_zsh_default, set_zsh_default,
    install_zplug, check_ptyxis_font,
    install_fastfetch_config, check_zsh_aliases, add_zsh_aliases,
)
from tabs.amd_actions import (
    check_overclock, enable_overclock,
    check_wheel, setup_lact_wheel,
    apply_lact_config, confirm_reboot,
)

BUILTIN_REGISTRY: dict[str, Callable] = {
    "check_ptyxis_default":     check_ptyxis_default,
    "set_ptyxis_default":       set_ptyxis_default,
    "check_shortcut_1":         check_shortcut_1,
    "set_shortcut_1":           set_shortcut_1,
    "check_shortcut_2":         check_shortcut_2,
    "set_shortcut_2":           set_shortcut_2,
    "check_zsh_default":        check_zsh_default,
    "set_zsh_default":          set_zsh_default,
    "install_zplug":            install_zplug,
    "check_ptyxis_font":        check_ptyxis_font,
    "install_fastfetch_config": install_fastfetch_config,
    "check_zsh_aliases":        check_zsh_aliases,
    "add_zsh_aliases":          add_zsh_aliases,
    "check_overclock":          check_overclock,
    "enable_overclock":         enable_overclock,
    "check_wheel":              check_wheel,
    "setup_lact_wheel":         setup_lact_wheel,
    "apply_lact_config":        apply_lact_config,
    "confirm_reboot":           confirm_reboot,
}
from ui.widgets import (
    make_icon, make_button, make_status_icon,
    set_status_ok, set_status_error, clear_status, make_suffix_box,
    scroll_child_into_view,
)


def run_check(check: dict | None) -> bool:
    if not check:
        return False
    kind = check.get("type")

    try:
        if kind == "rpm":
            return subprocess.run(
                ["rpm", "-q", check["value"]], capture_output=True, timeout=10,
            ).returncode == 0

        if kind == "flatpak":
            r = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application"],
                capture_output=True, text=True, timeout=15,
            )
            return check["value"] in r.stdout

        if kind == "which":
            return subprocess.run(
                ["which", check["value"]], capture_output=True, timeout=5,
            ).returncode == 0

        if kind == "systemd":
            return subprocess.run(
                ["systemctl", "is-enabled", check["value"]], capture_output=True, timeout=5,
            ).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False

    if kind == "path":
        return os.path.exists(os.path.expanduser(check["value"]))

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

    if config.DEBUG:
        print(f"[ALT Booster] run_check: неизвестный тип '{kind}'")
    return False


class _SafePage:
    def __init__(self, real_page: "DynamicPage") -> None:
        self._page = real_page

    def log(self, text: str) -> None:
        GLib.idle_add(self._page.log, text)

    def __getattr__(self, name: str):
        return getattr(self._page, name)


class ActionDispatcher:
    def __init__(self, page: DynamicPage) -> None:
        self._page = page

    def dispatch(
        self,
        action: dict,
        on_done: Callable[[bool], None] | None = None,
        arg: Any = None,
    ) -> None:
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
                r = subprocess.run(action["cmd"], capture_output=True, text=True, timeout=30)
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
                    ok = bool(fn(_SafePage(page), arg))
                else:
                    GLib.idle_add(page.log, f"\n✘  Неизвестная builtin: {fn_name}\n")

        except Exception as exc:
            GLib.idle_add(page.log, f"\n✘  Ошибка: {exc}\n")

        if on_done:
            GLib.idle_add(on_done, ok)

        if ok:
            page.refresh()


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
        row = Adw.ActionRow()
        row.set_title(rd.get("title", "?"))
        return row

    def _command_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", ""))
        if rd.get("icon"):
            row.add_prefix(make_icon(rd["icon"]))

        status = make_status_icon()
        style = rd.get("button_style", "suggested-action")
        btn = make_button(rd.get("button_label", "Запустить"), style=style)
        self._page._btn_size_group.add_widget(btn)
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
        btn = make_button(rd.get("button_label", "Применить"))
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

    def _file_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", "Файл не выбран"))
        if rd.get("icon"):
            row.add_prefix(make_icon(rd["icon"]))

        status = make_status_icon()
        pick_btn = make_button(rd.get("pick_label", "Выбрать файл"), style="flat")
        apply_btn = make_button(rd.get("apply_label", "Применить"))
        apply_btn.set_sensitive(False)

        action = rd.get("action", {})
        state_key = rd.get("state_key")
        selected_path = ""

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
            nonlocal selected_path
            try:
                f = dialog.open_finish(res)
                if f:
                    path = f.get_path()
                    selected_path = path
                    row.set_subtitle(os.path.basename(path))
                    apply_btn.set_sensitive(True)
                    clear_status(status)
            except GLib.Error:
                pass

        def _on_apply(_b: Gtk.Button) -> None:
            apply_btn.set_sensitive(False)
            apply_btn.set_label("…")
            self._page.log(f"\n▶  Применение: {os.path.basename(selected_path)}...\n")
            self._dispatcher.dispatch(action, on_done=_on_done, arg=selected_path)

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
                    row.set_subtitle(f"Применён: {os.path.basename(selected_path)}")
            else:
                apply_btn.set_sensitive(True)

        pick_btn.connect("clicked", _on_pick)
        apply_btn.connect("clicked", _on_apply)

        row._dp_status = status
        row._dp_check = None
        row.add_suffix(make_suffix_box(status, pick_btn, apply_btn))
        return row


class DynamicPage(Gtk.Box):
    def __init__(self, page_data: dict, log_fn: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.log = log_fn
        self._page_data = page_data
        self._rows_with_checks: list[Adw.ActionRow] = []
        self._rows_by_id: dict[str, Adw.ActionRow] = {}
        self._btn_size_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._factory = RowFactory(self)
        self._poll_running = False
        self._poll_lock = threading.Lock()

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)

        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._body.set_margin_top(20)
        self._body.set_margin_bottom(20)
        self._body.set_margin_start(20)
        self._body.set_margin_end(20)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1152)
        clamp.set_tightening_threshold(864)
        clamp.set_child(self._body)
        scroll.set_child(clamp)
        self.append(scroll)

        self._build(self._body)

        threading.Thread(target=self._poll_checks, daemon=True).start()

    def focus_row_by_id(self, row_id: str) -> bool:
        row = self._rows_by_id.get(row_id)
        if row is None:
            return False
        scroll = self.get_first_child()
        if isinstance(scroll, Gtk.ScrolledWindow):
            scroll_child_into_view(scroll, row)
        GLib.idle_add(row.grab_focus)
        return True

    def _build(self, body: Gtk.Box) -> None:
        for group_data in self._page_data.get("groups", []):
            if "requires" in group_data and shutil.which(group_data["requires"]) is None:
                continue

            group = Adw.PreferencesGroup()
            group.set_title(group_data.get("title", ""))
            if group_data.get("description"):
                group.set_description(group_data["description"])
            body.append(group)

            for row_data in group_data.get("rows", []):
                if "requires" in row_data and shutil.which(row_data["requires"]) is None:
                    continue
                row = self._factory.build(row_data)
                group.add(row)
                rid = row_data.get("id")
                if rid:
                    self._rows_by_id[str(rid)] = row
                if hasattr(row, "_dp_check"):
                    self._rows_with_checks.append(row)

    def _poll_checks(self) -> None:
        with self._poll_lock:
            if self._poll_running:
                return
            self._poll_running = True
        try:
            rows = list(self._rows_with_checks)
            if not rows:
                return
            with ThreadPoolExecutor(max_workers=min(8, len(rows))) as pool:
                futures = {pool.submit(run_check, getattr(r, "_dp_check", None)): r for r in rows}
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        ok = future.result()
                    except Exception:
                        ok = False
                    GLib.idle_add(self._apply_check_result, row, ok)
        finally:
            with self._poll_lock:
                self._poll_running = False

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
