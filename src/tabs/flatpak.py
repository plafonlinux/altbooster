
from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from core import config
from ui.common import load_module
from ui.rows import TaskRow, SettingRow
from ui.widgets import make_icon, make_scrolled_page, make_suffix_box


@dataclass
class FlatpakApp:
    app_id: str
    name: str
    version: str
    installation: str
    masked: bool = field(default=False)
    icon_path: str | None = field(default=None)


_FLATHUB_MIRRORS = [
    ("Официальный",       "dl.flathub.org",       "https://dl.flathub.org/repo/"),
    ("USTC — Китай",      "mirrors.ustc.edu.cn",  "https://mirrors.ustc.edu.cn/flathub"),
    ("SJTU — Китай",      "mirror.sjtu.edu.cn",   "https://mirror.sjtu.edu.cn/flathub"),
    ("Seoul — Ю. Корея",  "sel.flathub.org",      "https://sel.flathub.org/repo/"),
]

def _get_flathub_url() -> str | None:
    try:
        r = subprocess.run(
            ["flatpak", "remotes", "--columns=name,url"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            parts = line.split()
            if parts and parts[0].strip() == "flathub" and len(parts) > 1:
                return parts[1].strip()
    except Exception:
        pass
    return None

def _is_flatpak_available() -> bool:
    return shutil.which("flatpak") is not None


def _list_flatpak_apps() -> list[FlatpakApp]:
    try:
        r = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application,name,version,installation"],
            capture_output=True, text=True, timeout=15,
        )
        apps = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            app_id, name, version, installation = parts[0], parts[1], parts[2], parts[3]
            apps.append(FlatpakApp(
                app_id=app_id.strip(),
                name=name.strip() or app_id.strip(),
                version=version.strip(),
                installation=installation.strip(),
            ))
        return apps
    except Exception:
        return []


def _get_masked_ids() -> set[str]:
    masked: set[str] = set()
    for scope in ("--user", "--system"):
        try:
            r = subprocess.run(
                ["flatpak", "mask", scope],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    masked.add(line)
        except Exception:
            pass
    return masked


def _run_user_op(cmd: list, on_line, on_done) -> None:
    def _worker():
        ok = False
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8",
            )
            for line in proc.stdout:
                GLib.idle_add(on_line, line)
            proc.wait()
            ok = proc.returncode == 0
        except Exception as e:
            GLib.idle_add(on_line, f"✘ Ошибка: {e}\n")
        GLib.idle_add(on_done, ok)

    threading.Thread(target=_worker, daemon=True).start()


def _build_icon_index() -> dict[str, str]:
    index: dict[str, str] = {}

    for size in ("64x64", "128x128", "scalable"):
        for base in (
            Path.home() / ".local" / "share" / "flatpak" / "appstream",
            Path("/var/lib/flatpak/appstream"),
        ):
            for icons_dir in base.glob(f"*/*/active/icons/{size}"):
                if not icons_dir.is_dir():
                    continue
                for icon_file in icons_dir.iterdir():
                    if icon_file.stem not in index:
                        index[icon_file.stem] = str(icon_file)

    for app_base in (
        Path.home() / ".local" / "share" / "flatpak" / "app",
        Path("/var/lib/flatpak/app"),
    ):
        if not app_base.exists():
            continue
        for app_dir in app_base.iterdir():
            app_id = app_dir.name
            if app_id in index:
                continue
            hicolor = app_dir / "current" / "active" / "files" / "share" / "icons" / "hicolor"
            if not hicolor.exists():
                continue
            for size in ("64x64", "128x128", "scalable"):
                apps_dir = hicolor / size / "apps"
                if not apps_dir.is_dir():
                    continue
                for icon_file in apps_dir.iterdir():
                    if icon_file.stem == app_id:
                        index[app_id] = str(icon_file)
                        break
                if app_id in index:
                    break

    return index


def _make_app_icon(icon_path: str | None) -> Gtk.Widget:
    if icon_path:
        try:
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import Gdk, GdkPixbuf
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 32, 32, True)
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            img = Gtk.Image()
            img.set_from_paintable(texture)
            img.set_size_request(32, 32)
            return img
        except Exception:
            pass
    return make_icon("package-x-generic-symbolic")


class ConfirmFlatpakRow(TaskRow):

    def __init__(self, task, log_fn):
        super().__init__(task, log_fn, None)

    def start(self, *args):
        if getattr(self, "_running", False):
            return
        dialog = Adw.AlertDialog(
            heading="Удалить мусор Flatpak?",
            body=(
                "Будут удалены runtime-библиотеки, которые не требуются установленным приложениям.\n\n"
                "⚠ Внимание: Если вы устанавливали runtime вручную для сторонних программ "
                "(не из Flathub), они могут перестать работать."
            ),
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("run", "Удалить")
        dialog.set_response_appearance("run", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: super(ConfirmFlatpakRow, self).start() if r == "run" else None)
        dialog.present(self.get_root())


class FlatpakPage(Gtk.Box):

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._apps_group: Adw.PreferencesGroup | None = None
        self._refresh_btn: Gtk.Button | None = None
        self._update_all_btn: Gtk.Button | None = None

        scroll, self._body = make_scrolled_page()
        self.append(scroll)

        self._build_header_group()
        self._build_apps_group()
        self._build_manage_group()


    def _build_header_group(self):
        group = Adw.PreferencesGroup()
        group.set_title("Установленные приложения Flatpak")
        group.set_description(
            "Установленные Flatpak-приложения. "
            "Можно обновлять, удалять и замораживать обновления по отдельности."
        )
        self._body.append(group)

        suffix_box = Gtk.Box(spacing=8)
        suffix_box.set_valign(Gtk.Align.CENTER)

        self._refresh_btn = Gtk.Button()
        self._refresh_btn.set_icon_name("view-refresh-symbolic")
        self._refresh_btn.add_css_class("flat")
        self._refresh_btn.set_tooltip_text("Обновить список")
        self._refresh_btn.connect("clicked", lambda _: self._refresh())
        suffix_box.append(self._refresh_btn)

        self._update_all_btn = Gtk.Button(label="Обновить все")
        self._update_all_btn.add_css_class("suggested-action")
        self._update_all_btn.add_css_class("pill")
        self._update_all_btn.set_size_request(120, -1)
        self._update_all_btn.set_valign(Gtk.Align.CENTER)
        self._update_all_btn.set_sensitive(False)
        self._update_all_btn.connect("clicked", lambda _: self._on_update_all())
        suffix_box.append(self._update_all_btn)

        group.set_header_suffix(suffix_box)

    def _build_apps_group(self):
        self._apps_group = Adw.PreferencesGroup()
        self._body.append(self._apps_group)
        self._refresh()


    def _refresh(self):
        self._clear_apps_group()
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(False)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)

        spinner_row = Adw.ActionRow()
        spinner_row.set_title("Загрузка...")
        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_valign(Gtk.Align.CENTER)
        spinner_row.add_suffix(spinner)
        self._apps_group.add(spinner_row)

        threading.Thread(target=self._load_apps, daemon=True).start()

    def _clear_apps_group(self):
        if self._apps_group:
            self._body.remove(self._apps_group)
        self._apps_group = Adw.PreferencesGroup()
        self._body.append(self._apps_group)

    def _load_apps(self):
        if not _is_flatpak_available():
            GLib.idle_add(self._show_unavailable_state)
            return
        if not backend.is_flathub_enabled():
            GLib.idle_add(self._show_no_flathub_dialog)
            return
        apps = _list_flatpak_apps()
        masked = _get_masked_ids()
        icons = _build_icon_index()
        for app in apps:
            app.masked = app.app_id in masked
            app.icon_path = icons.get(app.app_id)
        GLib.idle_add(self._populate_apps, apps)

    def _populate_apps(self, apps: list[FlatpakApp]):
        self._clear_apps_group()
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(True)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(bool(apps))

        if not apps:
            row = Adw.ActionRow()
            row.set_title("Flatpak-приложений не найдено")
            row.set_subtitle("Установите Flatpak-приложения — они появятся здесь")
            row.add_prefix(make_icon("package-x-generic-symbolic"))
            self._apps_group.add(row)
            return

        user_apps   = sorted([a for a in apps if a.installation == "user"],   key=lambda a: a.name.lower())
        system_apps = sorted([a for a in apps if a.installation == "system"], key=lambda a: a.name.lower())
        other_apps  = sorted([a for a in apps if a.installation not in ("user", "system")], key=lambda a: a.name.lower())

        if user_apps and system_apps:
            exp_u = Adw.ExpanderRow()
            exp_u.set_title("Пользовательские")
            exp_u.set_subtitle(f"{len(user_apps)} прил.")
            exp_u.set_expanded(True)
            for app in user_apps:
                exp_u.add_row(self._make_app_row(app))
            self._apps_group.add(exp_u)

            exp_s = Adw.ExpanderRow()
            exp_s.set_title("Системные")
            exp_s.set_subtitle(f"{len(system_apps)} прил.")
            exp_s.set_expanded(True)
            for app in system_apps:
                exp_s.add_row(self._make_app_row(app))
            self._apps_group.add(exp_s)

            for app in other_apps:
                self._apps_group.add(self._make_app_row(app))
        else:
            for app in user_apps + system_apps + other_apps:
                self._apps_group.add(self._make_app_row(app))

    def _show_unavailable_state(self):
        self._clear_apps_group()
        row = Adw.ActionRow()
        row.set_title("flatpak не установлен")
        row.set_subtitle("Установите пакет «flatpak» для управления Flatpak-приложениями")
        row.add_prefix(make_icon("dialog-warning-symbolic"))
        self._apps_group.add(row)
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(True)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)

    def _show_no_flathub_dialog(self):
        self._clear_apps_group()
        row = Adw.ActionRow()
        row.set_title("Flathub не подключён")
        row.set_subtitle("Добавьте репозиторий Flathub для установки и управления приложениями")
        row.add_prefix(make_icon("dialog-information-symbolic"))
        self._apps_group.add(row)
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(True)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)

        dialog = Adw.AlertDialog(
            heading="Flathub не подключён",
            body="Для управления Flatpak-приложениями необходимо подключить репозиторий Flathub.\n\nПодключить сейчас?",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("setup", "Подключить Flathub")
        dialog.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("setup")
        dialog.connect("response", self._on_no_flathub_response)
        dialog.present(self.get_root())

    def _on_no_flathub_response(self, _dialog, response):
        if response != "setup":
            return
        self._log("\n▶  Установка Flatpak и Flathub...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Установка Flatpak...")

        def _step2(ok):
            if not ok:
                if hasattr(win, "stop_progress"):
                    GLib.idle_add(win.stop_progress, False)
                return
            backend.run_privileged(
                ["apt-get", "install", "-y", "flatpak-repo-flathub"],
                self._log,
                lambda ok2: (
                    GLib.idle_add(self._log, "✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n"),
                    GLib.idle_add(win.stop_progress, ok2) if hasattr(win, "stop_progress") else None,
                    GLib.idle_add(self._refresh) if ok2 else None,
                ),
            )

        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, _step2)


    def _make_app_row(self, app: FlatpakApp) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(app.name)
        row.set_subtitle(app.app_id)
        row.add_prefix(_make_app_icon(app.icon_path))

        ver_label = Gtk.Label(label=app.version or "—")
        ver_label.add_css_class("dim-label")
        ver_label.add_css_class("caption")
        ver_label.set_valign(Gtk.Align.CENTER)

        freeze_btn = Gtk.ToggleButton(label="❄")
        freeze_btn.set_active(app.masked)
        freeze_btn.add_css_class("flat")
        freeze_btn.add_css_class("circular")
        freeze_btn.set_valign(Gtk.Align.CENTER)
        freeze_btn.set_tooltip_text("Заморозить обновления (flatpak mask)")
        if app.masked:
            freeze_btn.add_css_class("accent")
        _hid: list[int] = []
        _hid.append(freeze_btn.connect(
            "toggled",
            lambda btn, a=app, h=_hid: self._on_freeze_toggle(a, btn, h),
        ))

        upd_btn = Gtk.Button()
        upd_btn.set_icon_name("software-update-available-symbolic")
        upd_btn.add_css_class("flat")
        upd_btn.add_css_class("circular")
        upd_btn.set_valign(Gtk.Align.CENTER)
        upd_btn.set_tooltip_text("Обновить")
        upd_btn.connect("clicked", lambda _, a=app: self._on_update_app(a))

        del_btn = Gtk.Button()
        del_btn.set_icon_name("user-trash-symbolic")
        del_btn.add_css_class("destructive-action")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("circular")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.set_tooltip_text("Удалить")
        del_btn.connect("clicked", lambda _, a=app: self._on_remove_app(a))

        row.add_suffix(make_suffix_box(ver_label, freeze_btn, upd_btn, del_btn))
        return row


    def _on_remove_app(self, app: FlatpakApp):
        dialog = Adw.AlertDialog()
        dialog.set_heading("Удалить приложение?")
        dialog.set_body(
            f"«{app.name}» ({app.app_id}) будет удалён.\n"
            f"Расположение: {app.installation}."
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: self._do_remove_app(app) if r == "delete" else None)
        dialog.present(self.get_root())

    def _do_remove_app(self, app: FlatpakApp):
        self._log(f"\n▶  Удаление {app.name} ({app.app_id})...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Удаление {app.name}...")

        scope = "--user" if app.installation == "user" else "--system"
        cmd = ["flatpak", "uninstall", "-y", scope, app.app_id]

        def _done(ok):
            msg = f"✔  {app.name} удалён!\n" if ok else f"✘  Ошибка удаления {app.name}\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)
            GLib.idle_add(self._refresh)

        if app.installation == "user":
            _run_user_op(cmd, self._log, _done)
        else:
            backend.run_privileged(cmd, self._log, _done)

    def _on_update_app(self, app: FlatpakApp):
        self._log(f"\n▶  Обновление {app.name} ({app.app_id})...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Обновление {app.name}...")

        scope = "--user" if app.installation == "user" else "--system"
        cmd = ["flatpak", "update", "-y", scope, app.app_id]

        def _done(ok):
            msg = f"✔  {app.name} обновлён!\n" if ok else f"✘  Ошибка обновления {app.name}\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)
            GLib.idle_add(self._refresh)

        if app.installation == "user":
            _run_user_op(cmd, self._log, _done)
        else:
            backend.run_privileged(cmd, self._log, _done)

    def _on_freeze_toggle(self, app: FlatpakApp, btn: Gtk.ToggleButton, hid: list[int]):
        state = btn.get_active()
        scope = "--user" if app.installation == "user" else "--system"
        if state:
            cmd = ["flatpak", "mask", scope, app.app_id]
            action = "заморожен"
        else:
            cmd = ["flatpak", "mask", "--remove", scope, app.app_id]
            action = "разморожен"

        btn.set_sensitive(False)

        def _apply_state(new_state: bool):
            btn.handler_block(hid[0])
            btn.set_active(new_state)
            if new_state:
                btn.add_css_class("accent")
            else:
                btn.remove_css_class("accent")
            btn.handler_unblock(hid[0])
            btn.set_sensitive(True)

        def _done(ok):
            if ok:
                GLib.idle_add(self._log, f"✔  {app.name} {action}\n")
                GLib.idle_add(_apply_state, state)
            else:
                GLib.idle_add(
                    self._log,
                    f"✘  Не удалось изменить заморозку для {app.name}\n"
                    "   Команда flatpak mask доступна в flatpak ≥ 1.8\n",
                )
                GLib.idle_add(_apply_state, not state)

        if app.installation == "user":
            _run_user_op(cmd, self._log, _done)
        else:
            backend.run_privileged(cmd, self._log, _done)


    def _build_manage_group(self):
        group = Adw.PreferencesGroup()
        self._body.append(group)

        mirror_row = Adw.ComboRow()
        mirror_row.set_title("Зеркало Flathub")
        mirror_row.set_subtitle("Источник загрузки приложений")
        mirror_row.set_model(Gtk.StringList.new([n for n, _, _ in _FLATHUB_MIRRORS]))

        sel_factory = Gtk.SignalListItemFactory()
        def _sel_setup(_f, item):
            lbl = Gtk.Label()
            lbl.set_halign(Gtk.Align.START)
            item.set_child(lbl)
        def _sel_bind(_f, item):
            pos = item.get_position()
            if pos < len(_FLATHUB_MIRRORS):
                item.get_child().set_label(_FLATHUB_MIRRORS[pos][0])
        sel_factory.connect("setup", _sel_setup)
        sel_factory.connect("bind", _sel_bind)
        mirror_row.set_factory(sel_factory)

        list_factory = Gtk.SignalListItemFactory()
        def _list_setup(_f, item):
            lbl = Gtk.Label()
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(6)
            lbl.set_margin_end(12)
            lbl.set_margin_top(6)
            lbl.set_margin_bottom(6)
            item.set_child(lbl)
        def _list_bind(_f, item):
            pos = item.get_position()
            if pos < len(_FLATHUB_MIRRORS):
                name, domain, _ = _FLATHUB_MIRRORS[pos]
                item.get_child().set_label(f"{name}  ({domain})")
        list_factory.connect("setup", _list_setup)
        list_factory.connect("bind", _list_bind)
        mirror_row.set_list_factory(list_factory)

        current_url = (_get_flathub_url() or "").rstrip("/")
        selected = 0
        for i, (_, _, url) in enumerate(_FLATHUB_MIRRORS):
            if current_url == url.rstrip("/"):
                selected = i
                break
        mirror_row.set_selected(selected)
        mirror_row.connect("notify::selected", self._on_mirror_selected)
        group.add(mirror_row)

        expander = Adw.ExpanderRow()
        expander.set_title("Обслуживание Flatpak")
        expander.set_expanded(False)
        group.add(expander)

        flathub_row = SettingRow(
            "application-x-addon-symbolic",
            "Подключить Flathub",
            "Устанавливает flatpak и flathub",
            "Включить", self._on_flathub, backend.is_flathub_enabled,
            "setting_flathub", "Активировано", self._on_flathub_undo, "Удалить",
        )
        expander.add_row(flathub_row)

        try:
            data = load_module("maintenance")
            tasks = data.get("tasks", [])
        except (OSError, Exception):
            tasks = []

        flatpak_ids = {"flatpak", "flatpak_repair", "flatpak_home"}
        for task in tasks:
            if task["id"] not in flatpak_ids:
                continue
            if task["id"] == "flatpak":
                task_row = ConfirmFlatpakRow(task, self._log)
            else:
                task_row = TaskRow(task, self._log, None)
            expander.add_row(task_row)


    def _on_flathub(self, row):
        row.set_working()
        self._log("\n▶  Установка Flatpak и Flathub...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Установка Flatpak...")

        def step2(ok):
            if not ok:
                row.set_done(False)
                return
            backend.run_privileged(
                ["apt-get", "install", "-y", "flatpak-repo-flathub"],
                self._log,
                lambda ok2: (
                    row.set_done(ok2),
                    self._log("✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n"),
                    win.stop_progress(ok2) if hasattr(win, "stop_progress") else None,
                ),
            )

        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, step2)

    def _on_flathub_undo(self, row):
        row.set_working()
        self._log("\n▶  Удаление Flatpak и Flathub...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Удаление Flatpak...")
        backend.run_privileged(
            ["apt-get", "remove", "-y", "flatpak", "flatpak-repo-flathub"],
            self._log,
            lambda ok: (
                row.set_undo_done(ok),
                self._log("✔  Flatpak удалён!\n" if ok else "✘  Ошибка\n"),
                win.stop_progress(ok) if hasattr(win, "stop_progress") else None,
            ),
        )

    def _on_mirror_selected(self, row, _):
        idx = row.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(_FLATHUB_MIRRORS):
            return
        name, _, url = _FLATHUB_MIRRORS[idx]
        current = _get_flathub_url() or ""
        if current.rstrip("/") == url.rstrip("/"):
            return
        win = self.get_root()
        self._log(f"\n▶  Смена зеркала Flathub: {name}...\n")
        if hasattr(win, "start_progress"):
            win.start_progress("Смена зеркала Flathub...")
        backend.run_privileged(
            ["flatpak", "remote-modify", "--system", "flathub", f"--url={url}"],
            self._log,
            lambda ok: (
                self._log("✔  Зеркало изменено!\n" if ok else "✘  Ошибка\n"),
                win.stop_progress(ok) if hasattr(win, "stop_progress") else None,
            ),
        )

    def _on_update_all(self):
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Обновление всех Flatpak-приложений...")
        self._log("\n▶  Обновление всех Flatpak-приложений...\n")
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(False)

        def _done_system(ok_sys):
            ok = ok_sys
            msg = "✔  Все Flatpak-приложения обновлены!\n" if ok else "⚠  Обновление завершено с ошибками\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)
            GLib.idle_add(self._refresh)

        def _done_user(ok_user):
            if not ok_user:
                GLib.idle_add(self._log, "⚠  Ошибки при обновлении пользовательских приложений\n")
            backend.run_privileged(["flatpak", "update", "-y", "--system"], self._log, _done_system)

        _run_user_op(["flatpak", "update", "-y", "--user"], self._log, _done_user)

