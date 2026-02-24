"""
ui.py — интерфейс GTK4 / Adwaita для ALT Booster.

Структура:
  PasswordDialog   — диалог ввода пароля sudo
  SettingRow       — строка настройки (SetupPage)
  AppRow           — строка приложения
  TaskRow          — строка задачи обслуживания
  AppEditDialog    — диалог добавления/редактирования приложения в apps.json
  SetupPage        — вкладка «Начало»
  AppsPage         — вкладка «Приложения» (из modules/apps.json + CRUD)
  DaVinciPage      — вкладка «DaVinci Resolve»
  MaintenancePage  — вкладка «Обслуживание» (из modules/maintenance.json)
  PlafonWindow     — главное окно

Вкладки «Внешний вид», «Терминал», «AMD Radeon» строятся через DynamicPage.
"""

import shutil
import ast
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from typing import Callable

import backend
import config
from dynamic_page import DynamicPage

_MODULES_DIR = Path(__file__).parent / "modules"


def _load_module(name: str) -> dict:
    with open(_MODULES_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_icon(name, size=22):
    i = Gtk.Image.new_from_icon_name(name)
    i.set_pixel_size(size)
    return i

def _make_button(label, width=130, style="suggested-action"):
    b = Gtk.Button(label=label)
    b.set_size_request(width, -1)
    b.add_css_class(style)
    b.add_css_class("pill")
    return b

def _make_status_icon():
    i = Gtk.Image()
    i.set_pixel_size(18)
    return i

def _set_status_ok(icon):
    icon.set_from_icon_name("object-select-symbolic")
    icon.add_css_class("success")

def _set_status_error(icon):
    icon.set_from_icon_name("dialog-error-symbolic")
    icon.remove_css_class("success")

def _clear_status(icon):
    icon.clear()
    icon.remove_css_class("success")

def _make_suffix_box(*widgets):
    box = Gtk.Box(spacing=10)
    box.set_valign(Gtk.Align.CENTER)
    for w in widgets:
        box.append(w)
    return box

def _make_scrolled_page():
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
    return scroll, body


# ── PasswordDialog ────────────────────────────────────────────────────────────

class PasswordDialog(Adw.AlertDialog):
    def __init__(self, parent, on_success, on_cancel):
        super().__init__(
            heading="Требуется пароль sudo",
            body="ALT Booster выполняет системные команды от имени root.\nПароль сохраняется только на время сессии.",
        )
        self._on_success = on_success
        self._on_cancel = on_cancel
        self._attempts = 0
        self._submitted = False

        self._entry = Gtk.PasswordEntry()
        self._entry.set_show_peek_icon(True)
        self._entry.set_property("placeholder-text", "Пароль пользователя")
        self._entry.connect("activate", lambda _: self._submit())
        self.set_extra_child(self._entry)

        self.add_response("cancel", "Отмена")
        self.add_response("ok", "Войти")
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("ok")
        self.set_close_response("cancel")
        self.connect("response", self._on_response)
        self.present(parent)

    def _on_response(self, _d, rid):
        if self._submitted:
            return
        if rid == "ok":
            self._submit()
        else:
            self._on_cancel()

    def _submit(self):
        pw = self._entry.get_text()
        if not pw:
            return
        self.set_response_enabled("ok", False)
        self._entry.set_sensitive(False)
        threading.Thread(
            target=lambda: GLib.idle_add(self._check_done, pw, backend.sudo_check(pw)),
            daemon=True,
        ).start()

    def _check_done(self, pw, ok):
        if ok:
            backend.set_sudo_password(pw)
            self._submitted = True
            self.close()
            self._on_success()
        else:
            self._attempts += 1
            self.set_body(f"❌ Неверный пароль (попытка {self._attempts}). Попробуйте снова.")
            self._entry.set_text("")
            self._entry.set_sensitive(True)
            self.set_response_enabled("ok", True)
            self._entry.grab_focus()


# ── SettingRow ────────────────────────────────────────────────────────────────

class SettingRow(Adw.ActionRow):
    def __init__(self, icon, title, subtitle, btn_label, on_activate, check_fn, state_key):
        super().__init__()
        self.set_title(title)
        self.set_subtitle(subtitle)
        self._check_fn = check_fn
        self._on_activate = on_activate
        self._state_key = state_key
        self._orig_label = btn_label

        self.add_prefix(_make_icon(icon))
        self._status = _make_status_icon()
        self._btn = _make_button(btn_label)
        self._btn.connect("clicked", lambda _: self._on_activate(self))
        self._btn.set_sensitive(False)
        self.add_suffix(_make_suffix_box(self._status, self._btn))

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
            _set_status_ok(self._status)
            self._btn.set_label("Активировано")
            self._btn.set_sensitive(False)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        else:
            _clear_status(self._status)
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


# ── AppRow ────────────────────────────────────────────────────────────────────

class AppRow(Adw.ActionRow):
    def __init__(self, app, log_fn, on_change_cb):
        super().__init__()
        self._app = app
        self._log = log_fn
        self._on_change = on_change_cb
        self._installing = False
        self._state_key = f"app_{app['id']}"

        self.set_title(app["label"])
        self.set_subtitle(app["desc"])

        self._status = _make_status_icon()
        self.add_prefix(self._status)

        self._btn = _make_button("Установить", width=120)
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
            _set_status_ok(self._status)
            self._btn.set_visible(False)
            self._prog.set_visible(False)
            self._trash_btn.set_visible(True)
            self._trash_btn.set_sensitive(True)
        else:
            _clear_status(self._status)
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
            cmd = ["rm", "-rf",
                   os.path.expanduser("~/.local/share/monitor-control"),
                   os.path.expanduser("~/Monic")]
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


# ── TaskRow ───────────────────────────────────────────────────────────────────

class TaskRow(Adw.ActionRow):
    def __init__(self, task, on_log, on_progress):
        super().__init__()
        self._task = task
        self._on_log = on_log
        self._on_progress = on_progress
        self._running = False
        self.result = None

        self.set_title(task["label"])
        self.set_subtitle(task["desc"])
        self.add_prefix(_make_icon(task["icon"]))

        self._prog = Gtk.ProgressBar()
        self._prog.set_hexpand(True)
        self._prog.set_valign(Gtk.Align.CENTER)
        self._status = _make_status_icon()
        self._btn = _make_button("Запустить", width=110)
        self._btn.connect("clicked", lambda _: self.start())

        right = Gtk.Box(spacing=10)
        right.set_valign(Gtk.Align.CENTER)
        right.set_size_request(320, -1)
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
        _clear_status(self._status)
        self._prog.set_fraction(0.0)
        cmd = self._task["cmd"].copy()
        if self._task["id"] == "davinci":
            cmd = ["find", config.get_dv_cache(), config.get_dv_proxy(), "-mindepth", "1", "-delete"]
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
            _set_status_ok(self._status)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        else:
            _set_status_error(self._status)
        self._btn.set_label("Повтор")
        self._btn.set_sensitive(True)
        self._on_log(f"{'✔  Готово' if ok else '✘  Ошибка'}: {self._task['label']}\n")
        self._on_progress()


# ── AppEditDialog ─────────────────────────────────────────────────────────────

class AppEditDialog(Adw.PreferencesWindow):
    """Диалог добавления / редактирования приложения в apps.json."""

    _SOURCE_LABELS = ["Flathub", "EPM install", "EPM play", "APT", "Скрипт"]
    _SOURCE_KEYS   = ["flatpak", "epm_install", "epm_play",  "apt", "script"]

    def __init__(self, parent, on_save, group_ids, group_titles,
                 existing_item=None, current_group=""):
        super().__init__()
        self._on_save = on_save
        self._existing = existing_item
        self._group_ids = group_ids
        self._group_titles = group_titles

        self.set_title("Редактировать" if existing_item else "Добавить приложение")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_search_enabled(False)

        page = Adw.PreferencesPage()
        self.add(page)

        # Группа
        grp_g = Adw.PreferencesGroup()
        grp_g.set_title("Категория")
        page.add(grp_g)
        self._group_row = Adw.ComboRow()
        self._group_row.set_title("Группа")
        gm = Gtk.StringList()
        for t in group_titles:
            gm.append(t)
        self._group_row.set_model(gm)
        if current_group in group_ids:
            self._group_row.set_selected(group_ids.index(current_group))
        grp_g.add(self._group_row)

        # Основные поля
        main_g = Adw.PreferencesGroup()
        main_g.set_title("Приложение")
        page.add(main_g)
        self._name_row = Adw.EntryRow()
        self._name_row.set_title("Название")
        main_g.add(self._name_row)
        self._desc_row = Adw.EntryRow()
        self._desc_row.set_title("Описание")
        main_g.add(self._desc_row)
        self._id_row = Adw.EntryRow()
        self._id_row.set_title("ID (латиница, без пробелов)")
        main_g.add(self._id_row)

        # Источник
        src_g = Adw.PreferencesGroup()
        src_g.set_title("Источник установки")
        page.add(src_g)
        self._type_row = Adw.ComboRow()
        self._type_row.set_title("Тип")
        tm = Gtk.StringList()
        for l in self._SOURCE_LABELS:
            tm.append(l)
        self._type_row.set_model(tm)
        src_g.add(self._type_row)
        self._pkg_row = Adw.EntryRow()
        self._pkg_row.set_title("Пакет / App ID")
        src_g.add(self._pkg_row)
        self._check_row = Adw.EntryRow()
        self._check_row.set_title("Check ID (если отличается от пакета)")
        src_g.add(self._check_row)

        # Кнопка сохранить
        btn_g = Adw.PreferencesGroup()
        page.add(btn_g)
        save_btn = Gtk.Button(label="Сохранить")
        save_btn.set_halign(Gtk.Align.END)
        save_btn.set_margin_top(8)
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("pill")
        save_btn.connect("clicked", self._on_save_clicked)
        btn_g.add(save_btn)

        if existing_item:
            self._fill(existing_item, current_group)

        self.present()

    def _fill(self, item, group_id):
        self._name_row.set_text(item.get("label", ""))
        self._desc_row.set_text(item.get("desc", ""))
        self._id_row.set_text(item.get("id", ""))
        if group_id in self._group_ids:
            self._group_row.set_selected(self._group_ids.index(group_id))

        src = item.get("source", {})
        cmd = src.get("cmd", [])
        if cmd and cmd[0] == "flatpak":
            t = "flatpak"; pkg = cmd[4] if len(cmd) > 4 else ""
        elif cmd and cmd[0] == "epm" and len(cmd) > 1 and cmd[1] == "play":
            t = "epm_play"; pkg = cmd[-1]
        elif cmd and cmd[0] == "epm":
            t = "epm_install"; pkg = cmd[-1]
        elif cmd and cmd[0] in ("apt-get", "apt"):
            t = "apt"; pkg = cmd[-1]
        else:
            t = "script"; pkg = ""
        if t in self._SOURCE_KEYS:
            self._type_row.set_selected(self._SOURCE_KEYS.index(t))
        self._pkg_row.set_text(pkg)

        check = src.get("check", [])
        check_id = check[1] if len(check) > 1 else ""
        if check_id and check_id != pkg:
            self._check_row.set_text(check_id)

    def _build_item(self):
        name  = self._name_row.get_text().strip()
        desc  = self._desc_row.get_text().strip()
        iid   = self._id_row.get_text().strip().replace(" ", "_").lower()
        pkg   = self._pkg_row.get_text().strip()
        check_id = self._check_row.get_text().strip() or pkg
        gidx  = self._group_row.get_selected()
        group_id = self._group_ids[gidx] if gidx < len(self._group_ids) else ""
        if not name or not pkg or not iid:
            return None
        tidx = self._type_row.get_selected()
        src_type = self._SOURCE_KEYS[tidx] if tidx < len(self._SOURCE_KEYS) else "flatpak"
        if src_type == "flatpak":
            cmd = ["flatpak", "install", "-y", "flathub", pkg]; ck = "flatpak"
        elif src_type == "epm_install":
            cmd = ["epm", "-i", pkg]; ck = "rpm"
        elif src_type == "epm_play":
            cmd = ["epm", "play", pkg]; ck = "rpm"
        elif src_type == "apt":
            cmd = ["apt-get", "install", "-y", pkg]; ck = "rpm"
        else:
            cmd = ["bash", "-c", pkg]; ck = "path"
        labels = dict(zip(self._SOURCE_KEYS, self._SOURCE_LABELS))
        item = {
            "id": iid, "label": name, "desc": desc,
            "source": {"label": labels.get(src_type, ""), "cmd": cmd, "check": [ck, check_id]},
        }
        return item, group_id

    def _on_save_clicked(self, _):
        result = self._build_item()
        if not result:
            t = Adw.Toast(title="Заполните все обязательные поля")
            t.set_timeout(3)
            self.add_toast(t)
            return
        item, group_id = result
        self._on_save(item, group_id)
        self.close()


# ── SetupPage ─────────────────────────────────────────────────────────────────

class SetupPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        scroll, body = _make_scrolled_page()
        self._body = body
        self.append(scroll)
        self._build_system_group(body)
        self._build_keyboard_group(body)

    def build_quick_actions(self, apps_cb, dv_cb):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_bottom(14)

        qa_apps = _make_button("Установка всех приложений", width=240)
        qa_apps.add_css_class("success")
        qa_apps.connect("clicked", lambda _: apps_cb(qa_apps))
        box.append(qa_apps)

        qa_dv = _make_button("DaVinci Resolve Ready", width=240)
        qa_dv.connect("clicked", lambda _: dv_cb(qa_dv))
        box.append(qa_dv)

        self._epm_btn = _make_button("Обновить систему (EPM)", width=240, style="destructive-action")
        self._epm_btn.connect("clicked", self._on_epm)
        self._epm_done = False
        box.append(self._epm_btn)

        self._body.prepend(box)

    def _build_system_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Система")
        body.append(group)
        rows = [
            ("security-high-symbolic",         "Включить sudo",               "control sudowheel enabled",                        "Активировать", self._on_sudo,          backend.is_sudo_enabled,               "setting_sudo"),
            ("application-x-addon-symbolic",   "Подключить Flathub",          "Устанавливает flatpak и flathub",                   "Подключить",   self._on_flathub,       backend.is_flathub_enabled,            "setting_flathub"),
            ("media-flash-symbolic",            "Автоматический TRIM",         "Включает еженедельную очистку блоков SSD",           "Включить",     self._on_trim_timer,    backend.is_fstrim_enabled,             "setting_trim_auto"),
            ("document-open-recent-symbolic",   "Лимиты журналов",             "SystemMaxUse=100M и сжатие в journald.conf",         "Настроить",    self._on_journal_limit, backend.is_journal_optimized,          "setting_journal_opt"),
            ("video-display-symbolic",          "Дробное масштабирование",     "Включает scale-monitor-framebuffer",                 "Включить",     self._on_scale,         backend.is_fractional_scaling_enabled, "setting_scale"),
        ]
        self._r_sudo, self._r_flathub, self._r_trim, self._r_journal, self._r_scale = [
            SettingRow(*r) for r in rows
        ]
        for r in (self._r_sudo, self._r_flathub, self._r_trim, self._r_journal, self._r_scale):
            group.add(r)

    def _build_keyboard_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Раскладка клавиатуры")
        body.append(group)
        self._r_alt  = SettingRow("input-keyboard-symbolic", "Alt + Shift",  "Классическое переключение раскладки",     "Включить", self._on_altshift,  None, "setting_kbd_altshift")
        self._r_caps = SettingRow("input-keyboard-symbolic", "CapsLock",     "Переключение раскладки кнопкой CapsLock", "Включить", self._on_capslock,  None, "setting_kbd_capslock")
        group.add(self._r_alt)
        group.add(self._r_caps)
        threading.Thread(target=self._detect_kbd_mode, daemon=True).start()

    def _on_sudo(self, row):
        row.set_working()
        self._log("\n▶  Включение sudo...\n")
        backend.run_privileged(["control", "sudowheel", "enabled"], lambda _: None,
            lambda ok: (row.set_done(ok), self._log("✔  sudo включён!\n" if ok else "✘  Ошибка\n")))

    def _on_flathub(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        row.set_working()
        self._log("\n▶  Установка Flatpak и Flathub...\n")
        def step2(ok):
            if not ok: row.set_done(False); return
            backend.run_privileged(["apt-get", "install", "-y", "flatpak-repo-flathub"], self._log,
                lambda ok2: (row.set_done(ok2), self._log("✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n")))
        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, step2)

    def _on_trim_timer(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        row.set_working()
        self._log("\n▶  Включение fstrim.timer...\n")
        backend.run_privileged(["systemctl", "enable", "--now", "fstrim.timer"], self._log,
            lambda ok: (row.set_done(ok), self._log("✔  TRIM включён!\n" if ok else "✘  Ошибка\n")))

    def _on_journal_limit(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        row.set_working()
        self._log("\n▶  Оптимизация журналов (создание drop-in конфига)...\n")
        
        # Используем безопасный путь через /etc/systemd/journald.conf.d/
        cmd = ["bash", "-c",
               "mkdir -p /etc/systemd/journald.conf.d && "
               "echo -e '[Journal]\\nSystemMaxUse=100M\\nCompress=yes' > /etc/systemd/journald.conf.d/99-altbooster.conf && "
               "systemctl restart systemd-journald"]
        
        backend.run_privileged(cmd, self._log,
            lambda ok: (row.set_done(ok), self._log("✔  Лимиты применены через drop-in!\n" if ok else "✘  Ошибка\n")))

    def _on_scale(self, row):
        row.set_working()
        self._log("\n▶  Масштабирование...\n")
        def _do():
            current = backend.gsettings_get(config.GSETTINGS_MUTTER, "experimental-features")
            try:
                feats = ast.literal_eval(current) if current not in ("@as []", "[]", "") else []
            except (ValueError, SyntaxError):
                feats = []
            if "scale-monitor-framebuffer" not in feats:
                feats.append("scale-monitor-framebuffer")
            ok = backend.run_gsettings(["set", config.GSETTINGS_MUTTER, "experimental-features", str(feats)])
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  Включено!\n" if ok else "✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _detect_kbd_mode(self):
        mode = config.state_get("setting_kbd_mode")
        if mode == "altshift":
            GLib.idle_add(self._r_alt._set_ui, True); GLib.idle_add(self._r_caps._set_ui, False); return
        if mode == "capslock":
            GLib.idle_add(self._r_caps._set_ui, True); GLib.idle_add(self._r_alt._set_ui, False); return
        value = backend.gsettings_get(config.GSETTINGS_KEYBINDINGS, "switch-input-source")
        is_caps = "Caps" in value; is_alt = "Alt_L" in value or "Shift>Alt" in value
        if is_caps: config.state_set("setting_kbd_mode", "capslock")
        elif is_alt: config.state_set("setting_kbd_mode", "altshift")
        GLib.idle_add(self._r_caps._set_ui, is_caps)
        GLib.idle_add(self._r_alt._set_ui, is_alt)

    def _on_altshift(self, row):
        row.set_working(); self._log("\n▶  Настройка Alt+Shift...\n")
        def _do():
            ok = (backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source", "['<Shift>Alt_L']"])
                  and backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source-backward", "['<Alt>Shift_L']"]))
            if ok: config.state_set("setting_kbd_mode", "altshift")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_caps._set_ui, False)
            GLib.idle_add(self._log, "✔  Alt+Shift готов!\n" if ok else "✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_capslock(self, row):
        row.set_working(); self._log("\n▶  Настройка CapsLock...\n")
        def _do():
            ok = (backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source", "['Caps_Lock']"])
                  and backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source-backward", "['<Shift>Caps_Lock']"]))
            if ok: config.state_set("setting_kbd_mode", "capslock")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_alt._set_ui, False)
            GLib.idle_add(self._log, "✔  CapsLock готов!\n" if ok else "✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_epm(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        self._epm_done = False
        self._epm_btn.set_sensitive(False)
        self._epm_btn.set_label("⏳ Обновление...")
        self._log("\n▶  epm update...\n")
        def on_update_done(ok):
            if not ok: self._epm_fin(False); return
            self._log("\n▶  epm full-upgrade...\n")
            backend.run_epm(["epm", "-y", "full-upgrade"], self._log, self._epm_fin)
        backend.run_epm(["epm", "-y", "update"], self._log, on_update_done)

    def _epm_fin(self, ok):
        if self._epm_done: return
        self._epm_done = True
        if ok:
            self._log("\n✔  ALT Linux обновлён!\n")
            self._epm_btn.set_label("Обновлено")
            self._epm_btn.remove_css_class("destructive-action")
            self._epm_btn.add_css_class("flat")
        else:
            self._log("\n✘  Ошибка обновления\n")
            self._epm_btn.set_label("Повторить обновление")
            self._epm_btn.set_sensitive(True)


# ── AppsPage ──────────────────────────────────────────────────────────────────

class AppsPage(Gtk.Box):
    """Вкладка «Приложения» — modules/apps.json + CRUD."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._rows = []
        self._busy = False
        self._json_path = _MODULES_DIR / "apps.json"
        self._data = {}

        scroll, body = _make_scrolled_page()
        self._body = body
        self.append(scroll)

        self._btn_all = _make_button("Установить все недостающие")
        self._btn_all.set_halign(Gtk.Align.CENTER)
        self._btn_all.connect("clicked", self._run_all)
        body.append(self._btn_all)

        self._load_and_build()
        GLib.idle_add(self._refresh_btn_all)

    def _load_and_build(self):
        try:
            with open(self._json_path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._data = self._from_config()
        self._build()

    def _from_config(self):
        groups = []
        for g in config.APPS:
            items = []
            for app in g["items"]:
                src = app["source"]
                items.append({"id": app["id"], "label": app["label"], "desc": app["desc"],
                               "source": {"label": src["label"], "cmd": src["cmd"],
                                          "check": list(src["check"])}})
            groups.append({"id": g["group"].lower().replace(" ", "_"), "title": g["group"], "items": items})
        return {"groups": groups}

    def _build(self):
        child = self._body.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            if child is not self._btn_all:
                self._body.remove(child)
            child = nxt
        self._rows.clear()

        for gdata in self._data.get("groups", []):
            pg = Adw.PreferencesGroup()
            self._body.append(pg)

            exp = Adw.ExpanderRow()
            exp.set_title(gdata.get("title", ""))
            exp.set_subtitle(f"Доступно приложений: {len(gdata.get('items', []))}")
            exp.set_expanded(False)
            pg.add(exp)

            add_btn = Gtk.Button()
            add_btn.set_icon_name("list-add-symbolic")
            add_btn.set_tooltip_text("Добавить приложение в эту группу")
            add_btn.add_css_class("flat")
            gid = gdata.get("id", "")
            add_btn.connect("clicked", lambda _b, g=gid: self._on_add(g))
            exp.add_suffix(add_btn)

            for app in gdata.get("items", []):
                src = dict(app["source"])
                chk = src.get("check", [])
                src["check"] = tuple(chk) if isinstance(chk, list) else chk
                
                # ИСПРАВЛЕНИЕ ISSUE #4 - Rirusha: Если нужен epm, а его нет — пропускаем отрисовку
                if src.get("cmd") and src["cmd"][0] == "epm" and not shutil.which("epm"):
                    continue
                
                app_n = dict(app, source=src)

                row = AppRow(app_n, self._log, self._refresh_btn_all)
                self._rows.append(row)
                exp.add_row(row)

                gid2 = gdata.get("id", "")

                edit_btn = Gtk.Button()
                edit_btn.set_icon_name("document-edit-symbolic")
                edit_btn.set_tooltip_text("Редактировать")
                edit_btn.set_valign(Gtk.Align.CENTER)
                edit_btn.add_css_class("flat")
                edit_btn.add_css_class("circular")
                edit_btn.connect("clicked", lambda _b, a=app, g=gid2: self._on_edit(a, g))
                row.add_suffix(edit_btn)

                del_btn = Gtk.Button()
                del_btn.set_icon_name("list-remove-symbolic")
                del_btn.set_tooltip_text("Убрать из списка")
                del_btn.set_valign(Gtk.Align.CENTER)
                del_btn.add_css_class("flat")
                del_btn.add_css_class("circular")
                del_btn.connect("clicked", lambda _b, a=app, g=gid2: self._on_delete(a, g))
                row.add_suffix(del_btn)

    def _group_ids(self):
        return [g.get("id", "") for g in self._data.get("groups", [])]

    def _group_titles(self):
        return [g.get("title", "") for g in self._data.get("groups", [])]

    def _on_add(self, group_id=""):
        if not group_id:
            gs = self._data.get("groups", [])
            group_id = gs[0]["id"] if gs else ""
        AppEditDialog(self.get_root(), lambda item, gid: self._save_item(item, gid, None),
                      self._group_ids(), self._group_titles(), current_group=group_id)

    def _on_edit(self, item, group_id):
        AppEditDialog(self.get_root(),
                      lambda upd, gid: self._save_item(upd, gid, item.get("id")),
                      self._group_ids(), self._group_titles(),
                      existing_item=item, current_group=group_id)

    def _on_delete(self, item, group_id):
        d = Adw.AlertDialog()
        d.set_heading("Убрать из списка?")
        d.set_body(f"«{item.get('label', '')}» будет удалён из apps.json.\nСамо приложение не удалится из системы.")
        d.add_response("cancel", "Отмена")
        d.add_response("delete", "Удалить")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: self._do_delete(item, group_id) if r == "delete" else None)
        d.present(self.get_root())

    def _save_item(self, item, group_id, existing_id):
        for g in self._data.get("groups", []):
            if g.get("id") != group_id:
                continue
            items = g.setdefault("items", [])
            if existing_id:
                for i, it in enumerate(items):
                    if it.get("id") == existing_id:
                        items[i] = item; break
                else:
                    items.append(item)
            else:
                ids = {it.get("id") for it in items}
                if item["id"] in ids:
                    item = dict(item, id=item["id"] + "_2")
                items.append(item)
            break
        self._write_json()
        GLib.idle_add(self._rebuild)

    def _do_delete(self, item, group_id):
        for g in self._data.get("groups", []):
            if g.get("id") == group_id:
                g["items"] = [it for it in g.get("items", []) if it.get("id") != item.get("id")]
                break
        self._write_json()
        GLib.idle_add(self._rebuild)

    def _rebuild(self):
        self._build()
        self._refresh_btn_all()

    def _write_json(self):
        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self._log(f"\n✘  Ошибка сохранения apps.json: {e}\n")

    def _refresh_btn_all(self):
        has_missing = any(not r.is_installed() for r in self._rows)
        self._btn_all.set_sensitive(has_missing)
        if has_missing:
            self._btn_all.set_label("Установить все недостающие")
            self._btn_all.add_css_class("suggested-action")
            self._btn_all.remove_css_class("flat")
        else:
            self._btn_all.set_label("✅ Все приложения установлены")
            self._btn_all.remove_css_class("suggested-action")
            self._btn_all.add_css_class("flat")

    def run_all_external(self, btn):
        dialog = Adw.AlertDialog(
            heading="Массовая установка приложений",
            body="Эта кнопка запустит фоновую установку абсолютно всех программ из вкладки «Приложения».\n\nВы можете предварительно перейти на эту вкладку, чтобы удалить ненужный софт или добавить свои собственные программы (DEB, Flatpak, скрипты) через встроенный редактор.\n\nНачать массовую установку сейчас?"
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("install", "Установить всё")
        
        # Делаем кнопку установки акцентной (синей)
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "install":
                self._ext_btn = btn
                btn.set_sensitive(False)
                btn.set_label("⏳ Установка...")
                # Запускаем оригинальный процесс установки
                self._run_all(None)

        dialog.connect("response", _on_response)
        
        # Показываем окно поверх основного интерфейса
        dialog.present(self.get_root())

    def _run_all(self, _):
        if self._busy: return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        self._busy = True
        self._btn_all.set_sensitive(False)
        self._btn_all.set_label("⏳  Установка...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        for row in (r for r in self._rows if not r.is_installed()):
            GLib.idle_add(row._on_install)
            while row._installing:
                time.sleep(0.5)
        GLib.idle_add(self._done)

    def _done(self):
        self._busy = False
        self._refresh_btn_all()
        if hasattr(self, "_ext_btn") and self._ext_btn:
            self._ext_btn.set_sensitive(True)
            self._ext_btn.set_label("Запустить")
            self._ext_btn = None
        self._log("\n✔  Массовая установка завершена\n")


# ── DaVinciPage ───────────────────────────────────────────────────────────────

class DaVinciPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        scroll, body = _make_scrolled_page()
        self.append(scroll)
        self._build_install_group(body)
        self._build_setup_expander(body)
        self._build_cache_group(body)

    def _build_install_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Установка")
        body.append(group)
        row = Adw.ActionRow()
        row.set_title("DaVinci Resolve")
        row.set_subtitle("epm play davinci-resolve")
        row.add_prefix(_make_icon("davinci-symbolic"))
        self._inst_st = _make_status_icon()
        self._inst_btn = _make_button("Установить")
        self._inst_btn.connect("clicked", self._on_install)
        self._inst_btn.set_sensitive(False)
        row.add_suffix(_make_suffix_box(self._inst_st, self._inst_btn))
        group.add(row)
        threading.Thread(target=lambda: GLib.idle_add(self._set_install_ui, backend.is_davinci_installed()), daemon=True).start()

    def _build_setup_expander(self, body):
        group = Adw.PreferencesGroup()
        body.append(group)
        exp = Adw.ExpanderRow()
        exp.set_title("Первичная настройка")
        exp.set_subtitle("PostInstall, AMD Radeon, AAC кодек, Fairlight")
        exp.set_expanded(False)
        group.add(exp)
        # PostInstall
        pg = Adw.PreferencesGroup(); pg.set_title("PostInstall"); pg.set_description("Выполните после установки DaVinci Resolve"); exp.add_row(pg)
        r = Adw.ActionRow(); r.set_title("Удалить конфликтующие библиотеки"); r.set_subtitle("libglib/libgio/libgmodule из /opt/resolve/libs"); r.add_prefix(_make_icon("emblem-important-symbolic"))
        self._post_st = _make_status_icon(); self._post_btn = _make_button("Выполнить", style="destructive-action")
        self._post_btn.connect("clicked", self._on_postinstall)
        r.add_suffix(_make_suffix_box(self._post_st, self._post_btn)); pg.add(r)
        # AMD
        ag = Adw.PreferencesGroup(); ag.set_title("AMD Radeon"); ag.set_description("Пакеты для работы с видеокартами AMD"); exp.add_row(ag)
        r2 = Adw.ActionRow(); r2.set_title("Поддержка AMD ROCm"); r2.set_subtitle("libGLU  ffmpeg  rocm-opencl-runtime  hip-runtime-amd  clinfo"); r2.add_prefix(_make_icon("video-display-symbolic"))
        self._amd_st = _make_status_icon(); self._amd_btn = _make_button("Установить")
        self._amd_btn.connect("clicked", self._on_amd_install); self._amd_btn.set_sensitive(False)
        r2.add_suffix(_make_suffix_box(self._amd_st, self._amd_btn)); ag.add(r2)
        if config.state_get("amd_rocm") is True: self._set_amd_ui(True)
        else: threading.Thread(target=lambda: GLib.idle_add(self._set_amd_ui, subprocess.run(["rpm", "-q", "rocm-opencl-runtime"], capture_output=True).returncode == 0), daemon=True).start()
        # AAC
        acg = Adw.PreferencesGroup(); acg.set_title("AAC Audio кодек"); exp.add_row(acg)
        r3 = Adw.ActionRow(); r3.set_title("FFmpeg AAC Encoder Plugin"); r3.set_subtitle("Плагин для экспорта AAC аудио"); r3.add_prefix(_make_icon("audio-x-generic-symbolic"))
        self._aac_st = _make_status_icon(); self._aac_btn = _make_button("Установить")
        self._aac_btn.connect("clicked", self._on_aac_install); self._aac_btn.set_sensitive(False)
        r3.add_suffix(_make_suffix_box(self._aac_st, self._aac_btn)); acg.add(r3)
        threading.Thread(target=lambda: GLib.idle_add(self._set_aac_ui, backend.is_aac_installed()), daemon=True).start()
        # Fairlight
        flg = Adw.PreferencesGroup(); flg.set_title("Fairlight Audio"); exp.add_row(flg)
        r4 = Adw.ActionRow(); r4.set_title("Включить Fairlight"); r4.set_subtitle("epm -i alsa-plugins-pulse"); r4.add_prefix(_make_icon("audio-speakers-symbolic"))
        self._fl_st = _make_status_icon(); self._fl_btn = _make_button("Установить")
        self._fl_btn.connect("clicked", self._on_fairlight); self._fl_btn.set_sensitive(False)
        r4.add_suffix(_make_suffix_box(self._fl_st, self._fl_btn)); flg.add(r4)
        threading.Thread(target=lambda: GLib.idle_add(self._set_fl_ui, backend.is_fairlight_installed()), daemon=True).start()

    def _build_cache_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Кэш")
        group.set_description("Укажите папки кэша и запустите очистку при необходимости")
        body.append(group)
        self._cache_row = self._make_folder_row("CacheClip", config.get_dv_cache(), "dv_cache_path")
        self._proxy_row = self._make_folder_row("ProxyMedia", config.get_dv_proxy(), "dv_proxy_path")
        group.add(self._cache_row)
        group.add(self._proxy_row)
        group.add(TaskRow({"id": "davinci", "icon": "user-trash-symbolic",
                           "label": "Очистить кэш DaVinci", "desc": "Удаляет файлы из CacheClip и ProxyMedia", "cmd": []},
                          self._log, lambda: None))

    def _make_folder_row(self, title, path, state_key):
        row = Adw.ActionRow(); row.set_title(title); row.set_subtitle(path)
        row.add_prefix(_make_icon("folder-symbolic"))
        btn = Gtk.Button(label="Выбрать"); btn.add_css_class("flat"); btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _, r=row, k=state_key: self._pick_folder(r, k))
        row.add_suffix(btn)
        return row

    def _set_install_ui(self, ok):
        if ok:
            _set_status_ok(self._inst_st); self._inst_btn.set_label("Установлен"); self._inst_btn.set_sensitive(False)
            self._inst_btn.remove_css_class("suggested-action"); self._inst_btn.add_css_class("flat")
        else:
            _clear_status(self._inst_st); self._inst_btn.set_sensitive(True); self._inst_btn.set_label("Установить")

    def _set_amd_ui(self, ok):
        if ok:
            _set_status_ok(self._amd_st); self._amd_btn.set_label("Установлено"); self._amd_btn.set_sensitive(False)
            self._amd_btn.remove_css_class("suggested-action"); self._amd_btn.add_css_class("flat")
        else:
            _clear_status(self._amd_st); self._amd_btn.set_sensitive(True); self._amd_btn.set_label("Установить")

    def _set_aac_ui(self, ok):
        if ok:
            _set_status_ok(self._aac_st); self._aac_btn.set_label("Установлен"); self._aac_btn.set_sensitive(False)
            self._aac_btn.remove_css_class("suggested-action"); self._aac_btn.add_css_class("flat")
        else:
            _clear_status(self._aac_st); self._aac_btn.set_sensitive(True); self._aac_btn.set_label("Установить")

    def _set_fl_ui(self, ok):
        if ok:
            _set_status_ok(self._fl_st); self._fl_btn.set_label("Установлен"); self._fl_btn.set_sensitive(False)
            self._fl_btn.remove_css_class("suggested-action"); self._fl_btn.add_css_class("flat")
        else:
            _clear_status(self._fl_st); self._fl_btn.set_sensitive(True); self._fl_btn.set_label("Установить")

    def _on_install(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        self._inst_btn.set_sensitive(False); self._inst_btn.set_label("…")
        self._log("\n▶  Установка DaVinci Resolve...\n")
        
        def on_done(ok):
            self._set_install_ui(ok)
            self._log("✔  DaVinci готов!\n" if ok else "✘  Ошибка\n")
            # Возврат кнопки через 3 сек, если не установлена или произошла ошибка
            if not ok:
                GLib.timeout_add(3000, lambda: (self._inst_btn.set_sensitive(True), self._inst_btn.set_label("Установить"), False)[2])

        backend.run_epm(["epm", "play", "davinci-resolve"], self._log, on_done)

    def _on_postinstall(self, _):
        self._post_btn.set_sensitive(False); self._post_btn.set_label("…")
        self._log("\n▶  PostInstall...\n")
        backend.run_privileged(["bash", "-c",
            "rm -rf /opt/resolve/libs/libglib-2.0.so* && rm -rf /opt/resolve/libs/libgio-2.0.so* && rm -rf /opt/resolve/libs/libgmodule-2.0.so*"],
            self._log, self._post_done)

    def _post_done(self, ok):
        if ok:
            _set_status_ok(self._post_st); self._post_btn.set_label("Выполнено"); self._post_btn.set_sensitive(False)
            self._post_btn.remove_css_class("destructive-action"); self._post_btn.add_css_class("flat")
            self._log("\n✔  Готово!\n")
        else:
            _set_status_error(self._post_st); self._post_btn.set_label("Повторить"); self._post_btn.set_sensitive(True)
            self._log("\n✘  Ошибка PostInstall\n")
        
        # Оживляем кнопку PostInstall через 3 сек
        GLib.timeout_add(3000, lambda: (self._post_btn.set_sensitive(True), self._post_btn.set_label("Выполнить"), False)[2])

    def _on_amd_install(self, _):
        self._amd_btn.set_sensitive(False); self._amd_btn.set_label("…")
        self._log("\n▶  Установка AMD ROCm...\n")
        backend.run_privileged(["apt-get", "install", "-y", "libGLU", "ffmpeg", "rocm-opencl-runtime", "hip-runtime-amd", "clinfo"],
            self._log, lambda ok: (config.state_set("amd_rocm", ok), self._set_amd_ui(ok),
                                   self._log("✔  AMD ROCm!\n" if ok else "✘  Ошибка\n")))

    def _on_aac_install(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        self._aac_btn.set_sensitive(False); self._aac_btn.set_label("…")
        self._log("\n▶  Установка AAC кодека...\n")
        def _worker():
            url = "https://github.com/Toxblh/davinci-linux-aac-codec/releases/latest/download/aac_encoder_plugin-linux-bundle.tar.gz"
            try:
                import tempfile, urllib.request
                with tempfile.TemporaryDirectory() as tmp:
                    arch = os.path.join(tmp, "aac.tar.gz")
                    urllib.request.urlretrieve(url, arch)
                    backend.install_aac_codec(arch, self._log, lambda ok: GLib.idle_add(self._set_aac_ui, ok))
            except Exception as e:
                GLib.idle_add(self._log, f"✘  {e}\n")
                GLib.idle_add(self._aac_btn.set_label, "Повторить")
                GLib.idle_add(self._aac_btn.set_sensitive, True)
        threading.Thread(target=_worker, daemon=True).start()

    def _on_fairlight(self, _):
        self._fl_btn.set_sensitive(False); self._fl_btn.set_label("…")
        self._log("\n▶  Fairlight...\n")
        backend.run_privileged(["apt-get", "install", "-y", "alsa-plugins-pulse"], self._log,
            lambda ok: (self._set_fl_ui(ok), self._log("✔  Fairlight!\n" if ok else "✘  Ошибка\n")))

    def _pick_folder(self, row, key):
        dialog = Gtk.FileDialog(); dialog.set_title("Выберите папку")
        cur = config.state_get(key) or os.path.expanduser("~")
        if os.path.exists(cur): dialog.set_initial_folder(Gio.File.new_for_path(cur))
        w = self
        while w.get_parent(): w = w.get_parent()
        dialog.select_folder(w, None, lambda d, r: self._folder_picked(d, r, row, key))

    def _folder_picked(self, dialog, result, row, key):
        try:
            f = dialog.select_folder_finish(result)
            if f:
                path = f.get_path(); config.state_set(key, path); row.set_subtitle(path)
                self._log(f"📁 {path}\n")
        except Exception:
            pass

    def run_ready_preset(self, btn):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        btn.set_sensitive(False); btn.set_label("⏳ Выполняется...")
        self._log("\n▶  DaVinci Resolve Ready...\n")

        def finish(ok):
            def _u():
                btn.set_label("✔ Готово" if ok else "✘ Ошибка")
                if not ok: btn.set_sensitive(True)
                else: btn.add_css_class("flat"); btn.remove_css_class("suggested-action")
                
                # Обновляем UI всех зависимых компонентов
                if ok:
                    _set_status_ok(self._post_st); self._post_btn.set_label("Выполнено")
                    self._post_btn.set_sensitive(False); self._post_btn.remove_css_class("destructive-action"); self._post_btn.add_css_class("flat")
                
                threading.Thread(target=lambda: GLib.idle_add(self._set_amd_ui, subprocess.run(["rpm","-q","rocm-opencl-runtime"],capture_output=True).returncode==0), daemon=True).start()
                threading.Thread(target=lambda: GLib.idle_add(self._set_fl_ui, backend.is_fairlight_installed()), daemon=True).start()
                threading.Thread(target=lambda: GLib.idle_add(self._set_aac_ui, backend.is_aac_installed()), daemon=True).start()

                # Оживляем главную кнопку пресета через 3 сек
                GLib.timeout_add(3000, lambda: (btn.set_sensitive(True), btn.set_label("DaVinci Resolve Ready"), 
                                                btn.add_css_class("suggested-action"), btn.remove_css_class("flat"), False)[4])

            GLib.idle_add(_u)

        # Цепочка выполнения пресета (s1 -> s2 -> s3 -> s4 -> finish)
        def s4():
            if backend.is_aac_installed():
                GLib.idle_add(self._log, "✔  AAC уже установлен.\n"); finish(True); return
            GLib.idle_add(self._log, "\n▶  [4/4] AAC...\n")
            url = "https://github.com/Toxblh/davinci-linux-aac-codec/releases/latest/download/aac_encoder_plugin-linux-bundle.tar.gz"
            try:
                import tempfile, urllib.request
                with tempfile.TemporaryDirectory() as tmp:
                    arch = os.path.join(tmp, "aac.tar.gz"); urllib.request.urlretrieve(url, arch)
                    backend.install_aac_codec(arch, self._log, lambda ok: GLib.idle_add(finish, ok))
            except Exception as e:
                GLib.idle_add(self._log, f"✘  {e}\n"); GLib.idle_add(finish, False)

        def s3(ok):
            if not ok: return finish(False)
            if backend.is_fairlight_installed():
                GLib.idle_add(self._log, "✔  Fairlight уже есть.\n"); threading.Thread(target=s4, daemon=True).start(); return
            GLib.idle_add(self._log, "\n▶  [3/4] Fairlight...\n")
            backend.run_privileged(["apt-get", "install", "-y", "alsa-plugins-pulse"], self._log,
                lambda ok2: threading.Thread(target=s4, daemon=True).start() if ok2 else finish(False))

        def s2(ok):
            if not ok: return finish(False)
            has = subprocess.run(["rpm","-q","rocm-opencl-runtime"], capture_output=True).returncode == 0
            if has:
                GLib.idle_add(self._log, "✔  AMD ROCm уже есть.\n"); s3(True); return
            GLib.idle_add(self._log, "\n▶  [2/4] AMD ROCm...\n")
            backend.run_privileged(["apt-get","install","-y","libGLU","ffmpeg","rocm-opencl-runtime","hip-runtime-amd","clinfo"],
                self._log, lambda ok2: threading.Thread(target=s3, args=(ok2,), daemon=True).start())

        def s1():
            GLib.idle_add(self._log, "\n▶  [1/4] PostInstall...\n")
            backend.run_privileged(["bash","-c","rm -rf /opt/resolve/libs/libglib-2.0.so* && rm -rf /opt/resolve/libs/libgio-2.0.so* && rm -rf /opt/resolve/libs/libgmodule-2.0.so*"],
                self._log, lambda ok: threading.Thread(target=s2, args=(ok,), daemon=True).start())

        threading.Thread(target=s1, daemon=True).start()


# ── MaintenancePage ───────────────────────────────────────────────────────────

class MaintenancePage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._rows = []
        self._busy = False
        scroll, body = _make_scrolled_page()
        self.append(scroll)
        try:
            data = _load_module("maintenance")
            tasks = data.get("tasks", [])
        except (OSError, json.JSONDecodeError):
            tasks = []
        self._build_header(body, len(tasks))
        self._build_tasks(body, tasks)

    def _build_header(self, body, total):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5); body.append(c)
        h = Gtk.Box()
        lbl = Gtk.Label(label="Общий прогресс"); lbl.set_halign(Gtk.Align.START); lbl.add_css_class("caption"); lbl.set_hexpand(True)
        self._prog_lbl = Gtk.Label(label=f"0 / {total} задач"); self._prog_lbl.add_css_class("caption")
        h.append(lbl); h.append(self._prog_lbl); c.append(h)
        self._prog_bar = Gtk.ProgressBar(); self._prog_bar.set_hexpand(True); c.append(self._prog_bar)
        self._btn_all = _make_button("Запустить все задачи"); self._btn_all.set_halign(Gtk.Align.CENTER)
        self._btn_all.connect("clicked", self._run_all); body.append(self._btn_all)

    def _build_tasks(self, body, tasks):
        group = Adw.PreferencesGroup(); group.set_title("Задачи обслуживания"); body.append(group)
        is_btrfs = config.is_btrfs(); btrfs_ids = {"btrfs_bal", "btrfs_defrag", "btrfs_scrub"}
        for task in tasks:
            row = TaskRow(task, self._log, self._update_progress)
            if task["id"] in btrfs_ids and not is_btrfs:
                row.set_sensitive(False); row.set_tooltip_text("Недоступно: не Btrfs")
            self._rows.append(row); group.add(row)

    def set_sensitive_all(self, sensitive):
        self._btn_all.set_sensitive(sensitive)
        for r in self._rows: r._btn.set_sensitive(sensitive)

    def _run_all(self, _):
        if self._busy: return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n"); return
        self._busy = True
        self._btn_all.set_sensitive(False); self._btn_all.set_label("⏳  Выполняется...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        for row in self._rows:
            GLib.idle_add(row.start)
            while row._running or row.result is None: time.sleep(0.2)
        GLib.idle_add(self._all_done)

    def _all_done(self):
        self._busy = False; self._btn_all.set_sensitive(True); self._btn_all.set_label("Запустить все задачи")
        self._log("\n✔  Готово!\n")

    def _update_progress(self):
        done = sum(1 for r in self._rows if r.result is not None); total = len(self._rows)
        self._prog_bar.set_fraction(done / total if total else 0.0)
        self._prog_lbl.set_label(f"{done} / {total} задач")


# ── PlafonWindow ──────────────────────────────────────────────────────────────

class PlafonWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("ALT Booster")
        settings = self._load_settings()
        self.set_default_size(settings.get("width", 740), settings.get("height", 880))
        self.connect("close-request", self._on_close)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)
        root.append(self._build_header())

        self._setup   = SetupPage(self._log)
        self._apps    = AppsPage(self._log)
        self._davinci = DaVinciPage(self._log)
        self._maint   = MaintenancePage(self._log)

        def _dp(name):
            try:
                return DynamicPage(_load_module(name), self._log)
            except Exception as e:
                lbl = Gtk.Label(label=f"Ошибка {name}.json:\n{e}")
                lbl.set_wrap(True)
                return lbl

        self._appearance = _dp("appearance")
        self._terminal   = _dp("terminal")
        self._amd        = _dp("amd")

        self._setup.build_quick_actions(self._apps.run_all_external, self._davinci.run_ready_preset)

        for widget, name, title, icon in [
            (self._setup,      "setup",       "Начало",           "go-home-symbolic"),
            (self._apps,       "apps",        "Приложения",        "flathub-symbolic"),
            (self._appearance, "appearance",  "Внешний вид",       "preferences-desktop-wallpaper-symbolic"),
            (self._terminal,   "terminal",    "Терминал",          "utilities-terminal-symbolic"),
            (self._amd,        "amd",         "AMD Radeon",        "video-display-symbolic"),
            (self._davinci,    "davinci",     "DaVinci Resolve",   "davinci-symbolic"),
            (self._maint,      "maintenance", "Обслуживание",      "emblem-system-symbolic"),
        ]:
            p = self._stack.add_titled(widget, name, title)
            p.set_icon_name(icon)

        self._paned = Gtk.Paned.new(Gtk.Orientation.VERTICAL)
        self._paned.set_start_child(self._stack)
        self._paned.set_end_child(self._build_log_panel())
        self._paned.set_vexpand(True)
        self._paned.set_position(settings.get("paned_pos", 720))
        root.append(self._paned)

    def _build_header(self):
        header = Adw.HeaderBar()
        self._stack = Adw.ViewStack()
        sw = Adw.ViewSwitcher(); sw.set_stack(self._stack)
        header.set_title_widget(sw)
        menu = Gio.Menu()
        menu.append("О приложении", "win.about")
        menu.append("Очистить лог", "win.clear_log")
        menu.append("Сбросить настройки", "win.reset_state")
        mb = Gtk.MenuButton(); mb.set_icon_name("open-menu-symbolic"); mb.set_menu_model(menu)
        header.pack_end(mb)
        for name, cb in [("about", self._show_about), ("clear_log", self._clear_log), ("reset_state", self._reset_state)]:
            a = Gio.SimpleAction.new(name, None); a.connect("activate", cb); self.add_action(a)
        return header

    def _build_log_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.set_margin_start(20); panel.set_margin_end(20); panel.set_margin_bottom(16); panel.set_vexpand(True)
        lbl = Gtk.Label(label="Лог выполнения"); lbl.set_halign(Gtk.Align.START); lbl.add_css_class("heading"); panel.append(lbl)
        frame = Gtk.Frame(); frame.add_css_class("card"); frame.set_margin_top(6); frame.set_vexpand(True); panel.append(frame)
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC); scroll.set_vexpand(True); frame.set_child(scroll)
        self._tv = Gtk.TextView(); self._tv.set_editable(False); self._tv.set_cursor_visible(False)
        self._tv.set_monospace(True); self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv.set_margin_start(10); self._tv.set_margin_end(10); self._tv.set_margin_top(8); self._tv.set_margin_bottom(8)
        self._buf = self._tv.get_buffer(); scroll.set_child(self._tv)
        return panel

    def ask_password(self):
        self._maint.set_sensitive_all(False)
        PasswordDialog(self, self._auth_ok, self.close)

    def _auth_ok(self):
        self._maint.set_sensitive_all(True)
        self._log("👋 Добро пожаловать в ALT Booster. С чего начнём?\n")

    def _load_settings(self):
        try:
            with open(config.CONFIG_FILE) as f: return json.load(f)
        except (OSError, json.JSONDecodeError): return {}

    def _on_close(self, _):
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(config.CONFIG_FILE, "w") as f:
                json.dump({"width": self.get_width(), "height": self.get_height(), "paned_pos": self._paned.get_position()}, f)
        except OSError: pass
        return False

    def _show_about(self, *_):
        d = Adw.AboutDialog()
        d.set_application_name("ALT Booster"); d.set_application_icon("altbooster")
        d.set_developer_name("PLAFON"); d.set_version("5.1-alpha")
        d.set_website("https://github.com/plafonlinux/altbooster")
        d.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        d.set_comments("Утилита настройки системы ALT Linux.\nGTK4 / Adwaita / Python 3 / Data-Driven UI")
        d.set_license_type(Gtk.License.MIT_X11); d.set_developers(["PLAFON"]); d.set_copyright("© 2026 PLAFON")
        d.add_link("📖 ALT Zero", "https://plafon.gitbook.io/alt-zero")
        d.add_link("💻 GitHub", "https://github.com/plafonlinux/altbooster")
        d.present(self)

    def _clear_log(self, *_):
        self._buf.set_text("")

    def _reset_state(self, *_):
        d = Adw.AlertDialog(heading="Сбросить настройки?",
            body="Все сохранённые статусы будут удалены.\nУтилита повторно проверит состояние при следующем запуске.")
        d.add_response("cancel", "Отмена"); d.add_response("reset", "Сбросить")
        d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel"); d.set_close_response("cancel")
        d.connect("response", lambda _d, r: (config.reset_state(), self._log("🔄 Настройки сброшены.\n")) if r == "reset" else None)
        d.present(self)

    def _log(self, text):
        end = self._buf.get_end_iter(); self._buf.insert(end, text)
        mark = self._buf.create_mark(None, self._buf.get_end_iter(), False)
        self._tv.scroll_mark_onscreen(mark)
