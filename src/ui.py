"""
ui.py — интерфейс GTK4 / Adwaita для ALT Booster.

Структура:
  PasswordDialog   — диалог ввода пароля sudo
  SettingRow       — строка настройки с кнопкой и индикатором состояния
  AppRow           — строка приложения с установкой/удалением
  TaskRow          — строка задачи обслуживания с прогрессбаром
  SetupPage        — вкладка «Начало»
  AppsPage         — вкладка «Приложения»
  AmdPage          — вкладка «AMD Radeon»
  DaVinciPage      — вкладка «DaVinci Resolve»
  MaintenancePage  — вкладка «Обслуживание»
  PlafonWindow     — главное окно приложения
"""

import ast
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from typing import Callable

import backend
import config


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _make_icon(name: str, size: int = 22) -> Gtk.Image:
    icon = Gtk.Image.new_from_icon_name(name)
    icon.set_pixel_size(size)
    return icon


def _make_button(label: str, width: int = 130, style: str = "suggested-action") -> Gtk.Button:
    btn = Gtk.Button(label=label)
    btn.set_size_request(width, -1)
    btn.add_css_class(style)
    btn.add_css_class("pill")
    return btn


def _make_status_icon() -> Gtk.Image:
    icon = Gtk.Image()
    icon.set_pixel_size(18)
    return icon


def _set_status_ok(icon: Gtk.Image) -> None:
    icon.set_from_icon_name("object-select-symbolic")
    icon.add_css_class("success")


def _set_status_error(icon: Gtk.Image) -> None:
    icon.set_from_icon_name("dialog-error-symbolic")
    icon.remove_css_class("success")


def _clear_status(icon: Gtk.Image) -> None:
    icon.clear()
    icon.remove_css_class("success")


def _make_suffix_box(*widgets) -> Gtk.Box:
    box = Gtk.Box(spacing=10)
    box.set_valign(Gtk.Align.CENTER)
    for w in widgets:
        box.append(w)
    return box


def _make_scrolled_page() -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
    """Создаёт стандартный прокручиваемый контейнер страницы."""
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
    """Диалог запроса пароля sudo при запуске приложения."""

    def __init__(self, parent, on_success, on_cancel):
        super().__init__(
            heading="Требуется пароль sudo",
            body=(
                "ALT Booster выполняет системные команды от имени root.\n"
                "Пароль сохраняется только на время сессии."
            ),
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

    def _on_response(self, _dialog, response_id):
        if self._submitted:
            return
        if response_id == "ok":
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
            target=lambda: GLib.idle_add(self._on_check_done, pw, backend.sudo_check(pw)),
            daemon=True,
        ).start()

    def _on_check_done(self, pw: str, ok: bool):
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
    """
    Строка настройки с кнопкой активации и индикатором состояния.
    Автоматически проверяет состояние через check_fn или кэш config.state.
    """

    def __init__(self, icon: str, title: str, subtitle: str,
                 btn_label: str, on_activate, check_fn, state_key: str):
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

    def _set_ui(self, enabled: bool):
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

    def set_done(self, ok: bool):
        if ok:
            config.state_set(self._state_key, True)
        self._set_ui(ok)
        if not ok:
            self._btn.set_label("Повторить")
            self._btn.set_sensitive(True)


# ── AppRow ────────────────────────────────────────────────────────────────────

class AppRow(Adw.ActionRow):
    """Строка приложения с установкой, прогрессом и кнопкой удаления."""

    def __init__(self, app: dict, log_fn, on_change_cb):
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

    def is_installed(self) -> bool:
        return config.state_get(self._state_key) is True

    def _check(self):
        installed = backend.check_app_installed(self._app["source"])
        config.state_set(self._state_key, installed)
        GLib.idle_add(self._set_installed_ui, installed)

    def _set_installed_ui(self, installed: bool):
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
            self._log("\n⚠  Система занята другим процессом. Подождите...\n")
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
            self._log("\n⚠  Система занята. Удаление невозможно.\n")
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

    def _pulse(self) -> bool:
        if self._installing:
            self._prog.pulse()
            return True
        return False

    def _install_done(self, ok: bool):
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

    def _uninstall_done(self, ok: bool):
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
    """Строка задачи обслуживания с прогрессбаром и кнопкой запуска."""

    def __init__(self, task: dict, on_log, on_progress):
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
            cmd = [
                "find",
                config.get_dv_cache(),
                config.get_dv_proxy(),
                "-mindepth", "1",
                "-delete",
            ]

        self._on_log(f"\n▶  {self._task['label']}...\n")
        GLib.timeout_add(110, self._pulse)
        backend.run_privileged(cmd, self._on_log, self._finish)

    def _pulse(self) -> bool:
        if self._running:
            self._prog.pulse()
            return True
        return False

    def _finish(self, ok: bool):
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


# ── SetupPage ─────────────────────────────────────────────────────────────────

class SetupPage(Gtk.Box):
    """Вкладка «Начало» — быстрые действия, система, клавиатура, обновление."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        scroll, body = _make_scrolled_page()
        self._body = body
        self.append(scroll)

        self._build_system_group(body)
        self._build_keyboard_group(body)

    def build_quick_actions(self, apps_cb, dv_cb):
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        action_box.set_halign(Gtk.Align.CENTER)
        action_box.set_margin_bottom(14)

        # 1. Установка всех приложений (постоянно зелёная)
        qa_apps_btn = _make_button("Установка всех приложений", width=240)
        qa_apps_btn.add_css_class("success") 
        qa_apps_btn.connect("clicked", lambda _: apps_cb(qa_apps_btn))
        action_box.append(qa_apps_btn)

        # 2. DaVinci Resolve Ready
        qa_dv_btn = _make_button("DaVinci Resolve Ready", width=240)
        qa_dv_btn.connect("clicked", lambda _: dv_cb(qa_dv_btn))
        action_box.append(qa_dv_btn)

        # 3. Обновление системы
        self._epm_btn = _make_button("Обновить систему (EPM)", width=240, style="destructive-action")
        self._epm_btn.connect("clicked", self._on_epm)
        self._epm_done = False
        action_box.append(self._epm_btn)

        self._body.prepend(action_box)

    # ── Группа «Система» ──────────────────────────────────────────────────────

    def _build_system_group(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        group.set_title("Система")
        body.append(group)

        self._r_sudo = SettingRow(
            "security-high-symbolic", "Включить sudo",
            "control sudowheel enabled",
            "Активировать", self._on_sudo,
            backend.is_sudo_enabled, "setting_sudo",
        )
        self._r_flathub = SettingRow(
            "application-x-addon-symbolic", "Подключить Flathub",
            "Устанавливает flatpak и flathub",
            "Подключить", self._on_flathub,
            backend.is_flathub_enabled, "setting_flathub",
        )
        self._r_trim = SettingRow(
            "media-flash-symbolic", "Автоматический TRIM",
            "Включает еженедельную очистку блоков SSD (fstrim.timer)",
            "Включить", self._on_trim_timer,
            backend.is_fstrim_enabled, "setting_trim_auto",
        )
        self._r_journal = SettingRow(
            "document-open-recent-symbolic", "Лимиты журналов",
            "SystemMaxUse=100M и сжатие в journald.conf",
            "Настроить", self._on_journal_limit,
            backend.is_journal_optimized, "setting_journal_opt",
        )
        self._r_scale = SettingRow(
            "video-display-symbolic", "Дробное масштабирование",
            "Включает scale-monitor-framebuffer",
            "Включить", self._on_scale,
            backend.is_fractional_scaling_enabled, "setting_scale",
        )

        for row in (self._r_sudo, self._r_flathub, self._r_trim, self._r_journal, self._r_scale):
            group.add(row)

    # ── Группа «Раскладка клавиатуры» ────────────────────────────────────────

    def _build_keyboard_group(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        group.set_title("Раскладка клавиатуры")
        body.append(group)

        self._r_alt = SettingRow(
            "input-keyboard-symbolic", "Alt + Shift",
            "Классическое переключение раскладки",
            "Включить", self._on_altshift,
            None, "setting_kbd_altshift",
        )
        self._r_caps = SettingRow(
            "input-keyboard-symbolic", "CapsLock",
            "Переключение раскладки кнопкой CapsLock",
            "Включить", self._on_capslock,
            None, "setting_kbd_capslock",
        )
        group.add(self._r_alt)
        group.add(self._r_caps)

        threading.Thread(target=self._detect_kbd_mode, daemon=True).start()

    # ── Обработчики настроек системы ─────────────────────────────────────────

    def _on_sudo(self, row: SettingRow):
        row.set_working()
        self._log("\n▶  Включение sudo...\n")

        def _discard(_line: str) -> None:
            pass

        def _on_done(ok):
            row.set_done(ok)
            self._log("✔  sudo включён!\n" if ok else "✘  Ошибка\n")

        backend.run_privileged(["control", "sudowheel", "enabled"], _discard, _on_done)

    def _on_flathub(self, row: SettingRow):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        row.set_working()
        self._log("\n▶  Установка Flatpak и Flathub...\n")

        def step2(ok):
            if not ok:
                row.set_done(False)
                return
            def _on_done(ok2):
                row.set_done(ok2)
                self._log("✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n")
            backend.run_privileged(["apt-get", "install", "-y", "flatpak-repo-flathub"], self._log, _on_done)

        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, step2)

    def _on_trim_timer(self, row: SettingRow):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        row.set_working()
        self._log("\n▶  Включение fstrim.timer...\n")

        def _on_done(ok):
            row.set_done(ok)
            self._log("✔  Таймер TRIM включён!\n" if ok else "✘  Ошибка\n")

        backend.run_privileged(["systemctl", "enable", "--now", "fstrim.timer"], self._log, _on_done)

    def _on_journal_limit(self, row: SettingRow):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        row.set_working()
        self._log("\n▶  Оптимизация journald.conf...\n")
        cmd = [
            "bash", "-c",
            "sed -i 's/^#\\?SystemMaxUse=.*/SystemMaxUse=100M/' /etc/systemd/journald.conf"
            " && sed -i 's/^#\\?Compress=.*/Compress=yes/' /etc/systemd/journald.conf"
            " && systemctl restart systemd-journald",
        ]

        def _on_done(ok):
            row.set_done(ok)
            self._log("✔  Лимиты применены!\n" if ok else "✘  Ошибка\n")

        backend.run_privileged(cmd, self._log, _on_done)

    def _on_scale(self, row: SettingRow):
        row.set_working()
        self._log("\n▶  Масштабирование...\n")

        def _do():
            current = backend.gsettings_get(config.GSETTINGS_MUTTER, "experimental-features")
            try:
                features = ast.literal_eval(current) if current not in ("@as []", "[]", "") else []
            except (ValueError, SyntaxError):
                features = []
            if "scale-monitor-framebuffer" not in features:
                features.append("scale-monitor-framebuffer")
            ok = backend.run_gsettings(
                ["set", config.GSETTINGS_MUTTER, "experimental-features", str(features)]
            )
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  Включено!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    # ── Обработчики раскладки ─────────────────────────────────────────────────

    def _detect_kbd_mode(self):
        mode = config.state_get("setting_kbd_mode")
        if mode == "altshift":
            GLib.idle_add(self._r_alt._set_ui, True)
            GLib.idle_add(self._r_caps._set_ui, False)
            return
        if mode == "capslock":
            GLib.idle_add(self._r_caps._set_ui, True)
            GLib.idle_add(self._r_alt._set_ui, False)
            return

        value = backend.gsettings_get(config.GSETTINGS_KEYBINDINGS, "switch-input-source")
        is_caps = "Caps" in value
        is_alt = "Alt_L" in value or "Shift>Alt" in value

        if is_caps:
            config.state_set("setting_kbd_mode", "capslock")
        elif is_alt:
            config.state_set("setting_kbd_mode", "altshift")

        GLib.idle_add(self._r_caps._set_ui, is_caps)
        GLib.idle_add(self._r_alt._set_ui, is_alt)

    def _on_altshift(self, row: SettingRow):
        row.set_working()
        self._log("\n▶  Настройка Alt+Shift...\n")

        def _do():
            ok = (
                backend.run_gsettings([
                    "set", config.GSETTINGS_KEYBINDINGS,
                    "switch-input-source", "['<Shift>Alt_L']",
                ])
                and backend.run_gsettings([
                    "set", config.GSETTINGS_KEYBINDINGS,
                    "switch-input-source-backward", "['<Alt>Shift_L']",
                ])
            )
            if ok:
                config.state_set("setting_kbd_mode", "altshift")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_caps._set_ui, False)
            GLib.idle_add(self._log, "✔  Alt+Shift готов!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    def _on_capslock(self, row: SettingRow):
        row.set_working()
        self._log("\n▶  Настройка CapsLock...\n")

        def _do():
            ok = (
                backend.run_gsettings([
                    "set", config.GSETTINGS_KEYBINDINGS,
                    "switch-input-source", "['Caps_Lock']",
                ])
                and backend.run_gsettings([
                    "set", config.GSETTINGS_KEYBINDINGS,
                    "switch-input-source-backward", "['<Shift>Caps_Lock']",
                ])
            )
            if ok:
                config.state_set("setting_kbd_mode", "capslock")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_alt._set_ui, False)
            GLib.idle_add(self._log, "✔  CapsLock готов!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    # ── Быстрое обновление системы ────────────────────────────────────────────

    def _on_epm(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята другим процессом обновления.\n")
            return

        self._epm_done = False
        self._epm_btn.set_sensitive(False)
        self._epm_btn.set_label("⏳ Обновление...")
        self._log("\n▶  epm update...\n")

        def on_update_done(ok):
            if not ok:
                self._epm_fin(False)
                return
            self._log("\n▶  epm full-upgrade...\n")
            backend.run_epm(
                ["epm", "-y", "full-upgrade"],
                self._log,
                self._epm_fin,
            )

        backend.run_epm(["epm", "-y", "update"], self._log, on_update_done)

    def _epm_fin(self, ok: bool):
        if self._epm_done:
            return
        self._epm_done = True

        if ok:
            self._log("\n✔  ALT Linux последней версии установлен!\n")
            self._epm_btn.set_label("Обновлено")
            self._epm_btn.remove_css_class("destructive-action")
            self._epm_btn.add_css_class("flat")
        else:
            self._log("\n✘  Ошибка обновления\n")
            self._epm_btn.set_label("Повторить обновление")
            self._epm_btn.set_sensitive(True)


# ── AppsPage ──────────────────────────────────────────────────────────────────

class AppsPage(Gtk.Box):
    """Вкладка «Приложения» — группированный список с массовой установкой."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._rows: list[AppRow] = []
        self._busy = False

        scroll, body = _make_scrolled_page()
        self.append(scroll)

        self._btn_all = _make_button("Установить все недостающие")
        self._btn_all.set_halign(Gtk.Align.CENTER)
        self._btn_all.connect("clicked", self._run_all)
        body.append(self._btn_all)

        for group_data in config.APPS:
            group = Adw.PreferencesGroup()
            body.append(group)

            expander = Adw.ExpanderRow()
            expander.set_title(group_data["group"])
            expander.set_subtitle(f"Доступно приложений: {len(group_data['items'])}")
            expander.set_expanded(False)
            group.add(expander)

            for app in group_data["items"]:
                row = AppRow(app, log_fn, self._refresh_btn_all)
                self._rows.append(row)
                expander.add_row(row)

        GLib.idle_add(self._refresh_btn_all)

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
        self._ext_btn = btn
        btn.set_sensitive(False)
        btn.set_label("⏳ Установка...")
        self._run_all(None)

    def _run_all(self, _):
        if self._busy:
            return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Массовая установка невозможна.\n")
            return
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
        if hasattr(self, '_ext_btn') and self._ext_btn:
            self._ext_btn.set_sensitive(True)
            self._ext_btn.set_label("Запустить")
            self._ext_btn = None
        self._log("\n✔  Массовая установка завершена\n")


# ── DaVinciPage ───────────────────────────────────────────────────────────────

class DaVinciPage(Gtk.Box):
    """Вкладка «DaVinci Resolve» — установка, настройка и управление кэшем."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        scroll, body = _make_scrolled_page()
        self.append(scroll)

        self._build_install_group(body)
        self._build_setup_expander(body)
        self._build_cache_group(body)

    # ── Секция установки ──────────────────────────────────────────────────────

    def _build_install_group(self, body: Gtk.Box):
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

        threading.Thread(target=self._check, daemon=True).start()

    # ── Спойлер «Первичная настройка» ─────────────────────────────────────────

    def _build_setup_expander(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        body.append(group)

        expander = Adw.ExpanderRow()
        expander.set_title("Первичная настройка")
        expander.set_subtitle("PostInstall, AMD Radeon, AAC кодек, Fairlight")
        expander.set_expanded(False)
        group.add(expander)

        self._build_postinstall_section(expander)
        self._build_amd_section(expander)
        self._build_aac_section(expander)
        self._build_fairlight_section(expander)

    def _build_postinstall_section(self, parent):
        group = Adw.PreferencesGroup()
        group.set_title("PostInstall")
        group.set_description("Выполните после установки DaVinci Resolve")
        parent.add_row(group)

        row = Adw.ActionRow()
        row.set_title("Удалить конфликтующие библиотеки")
        row.set_subtitle("Удаляет libglib/libgio/libgmodule из /opt/resolve/libs")
        row.add_prefix(_make_icon("emblem-important-symbolic"))

        self._post_st = _make_status_icon()
        self._post_btn = _make_button("Выполнить", style="destructive-action")
        self._post_btn.connect("clicked", self._on_postinstall)

        row.add_suffix(_make_suffix_box(self._post_st, self._post_btn))
        group.add(row)

    def _build_amd_section(self, parent):
        group = Adw.PreferencesGroup()
        group.set_title("AMD Radeon")
        group.set_description("Пакеты для работы DaVinci Resolve с видеокартами AMD")
        parent.add_row(group)

        row = Adw.ActionRow()
        row.set_title("Поддержка AMD ROCm")
        row.set_subtitle("libGLU  ffmpeg  rocm-opencl-runtime  hip-runtime-amd  clinfo")
        row.add_prefix(_make_icon("video-display-symbolic"))

        self._amd_st = _make_status_icon()
        self._amd_btn = _make_button("Установить")
        self._amd_btn.connect("clicked", self._on_amd_install)
        self._amd_btn.set_sensitive(False)

        row.add_suffix(_make_suffix_box(self._amd_st, self._amd_btn))
        group.add(row)

        if config.state_get("amd_rocm") is True:
            self._set_amd_ui(True)
        else:
            threading.Thread(target=self._check_amd, daemon=True).start()

    def _build_aac_section(self, parent):
        group = Adw.PreferencesGroup()
        group.set_title("AAC Audio кодек")
        parent.add_row(group)

        row = Adw.ActionRow()
        row.set_title("FFmpeg AAC Encoder Plugin")
        row.set_subtitle("Плагин для экспорта AAC аудио")
        row.add_prefix(_make_icon("audio-x-generic-symbolic"))

        self._aac_st = _make_status_icon()
        self._aac_btn = _make_button("Установить")
        self._aac_btn.connect("clicked", self._on_aac_install)
        self._aac_btn.set_sensitive(False)

        row.add_suffix(_make_suffix_box(self._aac_st, self._aac_btn))
        group.add(row)

        threading.Thread(target=self._check_aac, daemon=True).start()

    def _build_fairlight_section(self, parent):
        group = Adw.PreferencesGroup()
        group.set_title("Fairlight Audio")
        parent.add_row(group)

        row = Adw.ActionRow()
        row.set_title("Включить Fairlight")
        row.set_subtitle("epm -i alsa-plugins-pulse")
        row.add_prefix(_make_icon("audio-speakers-symbolic"))

        self._fl_st = _make_status_icon()
        self._fl_btn = _make_button("Установить")
        self._fl_btn.connect("clicked", self._on_fairlight)
        self._fl_btn.set_sensitive(False)

        row.add_suffix(_make_suffix_box(self._fl_st, self._fl_btn))
        group.add(row)

        threading.Thread(target=self._check_fairlight, daemon=True).start()

    # ── Секция кэша ───────────────────────────────────────────────────────────

    def _build_cache_group(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        group.set_title("Кэш")
        group.set_description("Укажите папки кэша и запустите очистку при необходимости")
        body.append(group)

        self._cache_row = self._make_folder_row(
            "CacheClip", config.get_dv_cache(), "dv_cache_path"
        )
        self._proxy_row = self._make_folder_row(
            "ProxyMedia", config.get_dv_proxy(), "dv_proxy_path"
        )
        group.add(self._cache_row)
        group.add(self._proxy_row)

        clean_task = {
            "id": "davinci",
            "icon": "user-trash-symbolic",
            "label": "Очистить кэш DaVinci",
            "desc": "Удаляет файлы из CacheClip и ProxyMedia",
            "cmd": [],
        }
        group.add(TaskRow(clean_task, self._log, lambda: None))

    def _make_folder_row(self, title: str, path: str, state_key: str) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(path)
        row.add_prefix(_make_icon("folder-symbolic"))

        btn = Gtk.Button(label="Выбрать")
        btn.add_css_class("flat")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _, r=row, k=state_key: self._pick_folder(r, k))
        row.add_suffix(btn)

        return row

    # ── Проверки состояния ────────────────────────────────────────────────────

    def _check(self):
        GLib.idle_add(self._set_install_ui, backend.is_davinci_installed())

    def _check_aac(self):
        GLib.idle_add(self._set_aac_ui, backend.is_aac_installed())

    def _check_fairlight(self):
        GLib.idle_add(self._set_fl_ui, backend.is_fairlight_installed())

    def _check_amd(self):
        try:
            ok = subprocess.run(["rpm", "-q", "rocm-opencl-runtime"], capture_output=True).returncode == 0
        except OSError:
            ok = False
        config.state_set("amd_rocm", ok)
        GLib.idle_add(self._set_amd_ui, ok)

    # ── Обновление UI ─────────────────────────────────────────────────────────

    def _set_install_ui(self, ok: bool):
        if ok:
            _set_status_ok(self._inst_st)
            self._inst_btn.set_label("Установлен")
            self._inst_btn.set_sensitive(False)
            self._inst_btn.remove_css_class("suggested-action")
            self._inst_btn.add_css_class("flat")
        else:
            _clear_status(self._inst_st)
            self._inst_btn.set_sensitive(True)
            self._inst_btn.set_label("Установить")

    def _set_aac_ui(self, ok: bool):
        if ok:
            _set_status_ok(self._aac_st)
            self._aac_btn.set_label("Установлен")
            self._aac_btn.set_sensitive(False)
            self._aac_btn.remove_css_class("suggested-action")
            self._aac_btn.add_css_class("flat")
        else:
            _clear_status(self._aac_st)
            self._aac_btn.set_sensitive(True)
            self._aac_btn.set_label("Установить")

    def _set_fl_ui(self, ok: bool):
        if ok:
            _set_status_ok(self._fl_st)
            self._fl_btn.set_label("Установлен")
            self._fl_btn.set_sensitive(False)
            self._fl_btn.remove_css_class("suggested-action")
            self._fl_btn.add_css_class("flat")
        else:
            _clear_status(self._fl_st)
            self._fl_btn.set_sensitive(True)
            self._fl_btn.set_label("Установить")

    def _set_amd_ui(self, ok: bool):
        if ok:
            _set_status_ok(self._amd_st)
            self._amd_btn.set_label("Установлено")
            self._amd_btn.set_sensitive(False)
            self._amd_btn.remove_css_class("suggested-action")
            self._amd_btn.add_css_class("flat")
        else:
            _clear_status(self._amd_st)
            self._amd_btn.set_sensitive(True)

    # ── Обработчики действий ──────────────────────────────────────────────────

    def _on_install(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        self._inst_btn.set_sensitive(False)
        self._inst_btn.set_label("…")
        self._log("\n▶  Установка DaVinci Resolve...\n")
        backend.run_epm(
            ["epm", "play", "davinci-resolve"],
            self._log,
            self._on_install_done,
        )

    def _on_install_done(self, ok: bool):
        self._set_install_ui(ok)
        self._log("✔  DaVinci готов!\n" if ok else "✘  Ошибка установки\n")

    def _on_postinstall(self, _):
        self._post_btn.set_sensitive(False)
        self._post_btn.set_label("…")
        self._log("\n▶  PostInstall: удаление конфликтующих библиотек...\n")
        cmd = [
            "bash", "-c",
            "rm -rf /opt/resolve/libs/libglib-2.0.so*"
            " && rm -rf /opt/resolve/libs/libgio-2.0.so*"
            " && rm -rf /opt/resolve/libs/libgmodule-2.0.so*",
        ]
        backend.run_privileged(cmd, self._log, self._post_done)

    def _post_done(self, ok: bool):
        if ok:
            _set_status_ok(self._post_st)
            self._post_btn.set_label("Выполнено")
            self._post_btn.set_sensitive(False)
            self._post_btn.remove_css_class("destructive-action")
            self._post_btn.add_css_class("flat")
            self._log("\n✔  Готово! Запустите DaVinci Resolve.\n")
        else:
            _set_status_error(self._post_st)
            self._post_btn.set_label("Повторить")
            self._post_btn.set_sensitive(True)
            self._log("\n✘  Ошибка PostInstall\n")

    def _on_amd_install(self, _):
        self._amd_btn.set_sensitive(False)
        self._amd_btn.set_label("…")
        self._log("\n▶  Установка пакетов AMD ROCm...\n")
        pkgs = ["libGLU", "ffmpeg", "rocm-opencl-runtime", "hip-runtime-amd", "clinfo"]
        backend.run_privileged(["apt-get", "install", "-y"] + pkgs, self._log, self._amd_done)

    def _amd_done(self, ok: bool):
        config.state_set("amd_rocm", ok)
        self._set_amd_ui(ok)
        self._log("✔  AMD ROCm установлен!\n" if ok else "✘  Ошибка установки\n")
        if not ok:
            self._amd_btn.set_label("Повторить")
            self._amd_btn.set_sensitive(True)

    def _on_aac_install(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        self._aac_btn.set_sensitive(False)
        self._aac_btn.set_label("…")
        self._log("\n▶  Установка AAC кодека...\n")
        threading.Thread(target=self._aac_worker, daemon=True).start()

    def _aac_worker(self):
        url = (
            "https://github.com/Toxblh/davinci-linux-aac-codec"
            "/releases/latest/download/aac_encoder_plugin-linux-bundle.tar.gz"
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                archive = os.path.join(tmp, "aac.tar.gz")
                urllib.request.urlretrieve(url, archive)

                def on_done(ok: bool):
                    self._set_aac_ui(ok)
                    self._log("✔  AAC готов!\n" if ok else "✘  Ошибка установки\n")

                backend.install_aac_codec(archive, self._log, on_done)
        except Exception as e:
            GLib.idle_add(self._aac_fail, str(e))

    def _aac_fail(self, message: str):
        self._log(f"✘  {message}\n")
        self._aac_btn.set_label("Повторить")
        self._aac_btn.set_sensitive(True)

    def _on_fairlight(self, _):
        self._fl_btn.set_sensitive(False)
        self._fl_btn.set_label("…")
        self._log("\n▶  Установка Fairlight (alsa-plugins-pulse)...\n")
        backend.run_privileged(
            ["apt-get", "install", "-y", "alsa-plugins-pulse"],
            self._log,
            self._fl_done,
        )

    def _fl_done(self, ok: bool):
        self._set_fl_ui(ok)
        self._log("✔  Fairlight готов!\n" if ok else "✘  Ошибка установки\n")
        if not ok:
            self._fl_btn.set_label("Повторить")
            self._fl_btn.set_sensitive(True)

    # ── Выбор папки ───────────────────────────────────────────────────────────

    def _pick_folder(self, row: Adw.ActionRow, key: str):
        dialog = Gtk.FileDialog()
        dialog.set_title("Выберите папку")

        current = config.state_get(key) or os.path.expanduser("~")
        if os.path.exists(current):
            dialog.set_initial_folder(Gio.File.new_for_path(current))

        widget = self
        while widget.get_parent():
            widget = widget.get_parent()

        dialog.select_folder(widget, None, self._on_folder_picked, (row, key))

    def _on_folder_picked(self, dialog, result, user_data):
        row, key = user_data
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                config.state_set(key, path)
                row.set_subtitle(path)
                self._log(f"📁 Путь сохранён: {path}\n")
        except Exception:
            pass

    # ── Пресет Быстрого Старта ────────────────────────────────────────────────

    def run_ready_preset(self, btn):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        btn.set_sensitive(False)
        btn.set_label("⏳ Выполняется...")
        self._log("\n▶  Запуск пресета DaVinci Resolve Ready (Первичная настройка)...\n")

        def finish(ok):
            def _update_ui():
                btn.set_label("Готово" if ok else "Ошибка")
                if not ok:
                    btn.set_sensitive(True)
                else:
                    btn.add_css_class("flat")
                    btn.remove_css_class("suggested-action")
                    
                # Принудительно ставим статус PostInstall в UI
                if ok:
                    _set_status_ok(self._post_st)
                    self._post_btn.set_label("Выполнено")
                    self._post_btn.set_sensitive(False)
                    self._post_btn.remove_css_class("destructive-action")
                    self._post_btn.add_css_class("flat")

                # Обновляем остальные статусы UI-индикаторов
                self._check_amd()
                self._check_fairlight()
                self._check_aac()
            GLib.idle_add(_update_ui)

        def step4_aac():
            if backend.is_aac_installed():
                GLib.idle_add(self._log, "✔  AAC кодек уже установлен.\n")
                finish(True)
                return
                
            GLib.idle_add(self._log, "\n▶  [4/4] Загрузка и установка AAC кодека...\n")
            url = "https://github.com/Toxblh/davinci-linux-aac-codec/releases/latest/download/aac_encoder_plugin-linux-bundle.tar.gz"
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    archive = os.path.join(tmp, "aac.tar.gz")
                    urllib.request.urlretrieve(url, archive)
                    backend.install_aac_codec(archive, self._log, lambda ok: GLib.idle_add(finish, ok))
            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка загрузки AAC: {e}\n")
                GLib.idle_add(finish, False)

        def step3_fairlight(ok):
            if not ok: return finish(False)
            if backend.is_fairlight_installed():
                GLib.idle_add(self._log, "✔  Fairlight уже установлен.\n")
                threading.Thread(target=step4_aac, daemon=True).start()
                return
                
            GLib.idle_add(self._log, "\n▶  [3/4] Установка Fairlight (alsa-plugins-pulse)...\n")
            backend.run_privileged(
                ["apt-get", "install", "-y", "alsa-plugins-pulse"],
                self._log,
                lambda ok2: threading.Thread(target=step4_aac, daemon=True).start() if ok2 else finish(False)
            )

        def step2_amd(ok):
            if not ok: return finish(False)
            try:
                has_amd = subprocess.run(["rpm", "-q", "rocm-opencl-runtime"], capture_output=True).returncode == 0
            except OSError:
                has_amd = False
                
            if has_amd:
                GLib.idle_add(self._log, "✔  AMD ROCm уже установлен.\n")
                step3_fairlight(True)
                return
                
            GLib.idle_add(self._log, "\n▶  [2/4] Установка пакетов AMD ROCm...\n")
            backend.run_privileged(
                ["apt-get", "install", "-y", "libGLU", "ffmpeg", "rocm-opencl-runtime", "hip-runtime-amd", "clinfo"],
                self._log,
                lambda ok2: threading.Thread(target=step3_fairlight, args=(ok2,), daemon=True).start()
            )

        def step1_postinstall():
            GLib.idle_add(self._log, "\n▶  [1/4] PostInstall: удаление конфликтующих библиотек...\n")
            cmd = [
                "bash", "-c",
                "rm -rf /opt/resolve/libs/libglib-2.0.so* && "
                "rm -rf /opt/resolve/libs/libgio-2.0.so* && "
                "rm -rf /opt/resolve/libs/libgmodule-2.0.so*"
            ]
            backend.run_privileged(
                cmd, 
                self._log, 
                lambda ok: threading.Thread(target=step2_amd, args=(ok,), daemon=True).start()
            )

        # Старт с первого шага настройки, пропуская установку самой программы
        threading.Thread(target=step1_postinstall, daemon=True).start()



# ── module-level content constants ─────────────────────────────────────────
FASTFETCH_JSON_CONTENT = '{\n    "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",\n    "logo": { "type": "small" },\n    "display": { "separator": "\\u001b[90m \\u2590 " },\n    "modules": [\n        {"type":"os","key":"","keyColor":"94","format":"{2}"},\n        {"type":"kernel","key":"","keyColor":"39"},\n        {"type":"packages","key":"\\uf017","keyColor":"33"},\n        {"type":"shell","key":"","keyColor":"94","format":"{1}"},\n        {"type":"terminal","key":"","keyColor":"39","format":"{1}"},\n        "break",\n        {"type":"wm","key":"\\udb81\\udd6e","keyColor":"34"},\n        {"type":"wmtheme","key":"\\udb80\\udc7c","keyColor":"33"},\n        {"type":"icons","key":"\\udb80\\udcf8","keyColor":"93"},\n        "break",\n        {"type":"host","key":"\\udb80\\udf42","keyColor":"92"},\n        {"type":"display","key":"\\udb83\\ude51","keyColor":"32"},\n        {"type":"cpu","key":"\\udb80\\udc4d","keyColor":"96"},\n        {"type":"gpu","key":"\\udb83\\udc2e","keyColor":"96"},\n        {"type":"memory","key":"","keyColor":"36"},\n        {"type":"uptime","key":"\\udb84\\udca6","keyColor":"39"},\n        "break",\n        "colors"\n    ]\n}'
ZSH_ALIASES_CONTENT = '\n# Timeshift\nalias tm="sudo timeshift"\nalias tmc="sudo timeshift --create"\nalias tmd="sudo timeshift --delete"\nalias tmda="sudo timeshift --delete-all"\nalias tml="sudo timeshift --list"\n# Fastfetch\nalias n="fastfetch -c ~/.config/fastfetch/plafonfetch.jsonc"\nalias k="uname -rs"\nalias g="gnome-shell --version"\nalias f="lsb_release -sd"\nalias c="clear"\nalias find="epmqa"\n# Upgrade\nalias up="epm update && epm full-upgrade"\nalias cc="sudo apt-get clean && flatpak uninstall --unused -y && sudo journalctl --vacuum-time=1weeks"\n# PC\nalias son="sudo systemctl suspend"\nalias reboot="systemctl reboot"\nalias ls="ls --color"\n# Flatpak\nalias fli="flatpak install --noninteractive -y flathub"\nalias flr="flatpak remove --noninteractive -y"\nalias fr="flatpak repair"\nalias fl="flatpak list"\n# GRUB\nalias upgrub="sudo update-grub"\nalias grubup="sudo update-grub"\n# Other\nalias sn="sudo nautilus"\nalias vmax="sudo sysctl -w vm.max_map_count=2147483642"\n'



# ── AppearancePage ────────────────────────────────────────────────────────────

class AppearancePage(Gtk.Box):
    FOLDER_COLORS = [
        "adwaita","blue","bluegrey","breeze","cyan","darkcyan",
        "green","grey","indigo","magenta","nordic","orange",
        "palebrown","paleorange","pink","red","teal","violet",
        "white","yellow","yaru",
    ]
    WALLPAPER_URL = "https://oboi.plafon.org"

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        scroll, body = _make_scrolled_page()
        self.append(scroll)
        
        self._build_icons_group(body)
        self._build_folders_group(body)
        self._build_wallpapers_group(body)

        # Запускаем фоновые проверки при открытии окна
        threading.Thread(target=self._check_papirus_installed, daemon=True).start()
        threading.Thread(target=self._check_active_states, daemon=True).start()

    def _build_icons_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Темы иконок")
        group.set_description("Установка и переключение системных тем иконок")
        body.append(group)
        
        # 1. Установить Papirus
        row = Adw.ActionRow()
        row.set_title("Установить Papirus")
        row.set_subtitle("epmi papirus-remix-icon-theme")
        row.add_prefix(_make_icon("preferences-desktop-theme-symbolic"))
        
        self._papirus_install_st = _make_status_icon()
        self._papirus_install_btn = _make_button("Установить")
        self._papirus_install_btn.connect("clicked", self._on_papirus_install)
        self._papirus_install_btn.set_sensitive(False)
        
        row.add_suffix(_make_suffix_box(self._papirus_install_st, self._papirus_install_btn))
        group.add(row)
        
        # 2. Применить Papirus
        apply_row = Adw.ActionRow()
        apply_row.set_title("Применить иконки Papirus")
        apply_row.set_subtitle("Papirus-Dark / Papirus — по текущей теме")
        apply_row.add_prefix(_make_icon("preferences-desktop-wallpaper-symbolic"))
        
        self._papirus_apply_st = _make_status_icon()
        self._papirus_apply_btn = _make_button("Применить", style="flat")
        self._papirus_apply_btn.connect("clicked", self._on_papirus_apply)
        
        apply_row.add_suffix(_make_suffix_box(self._papirus_apply_st, self._papirus_apply_btn))
        group.add(apply_row)

        # 3. Применить Adwaita
        adwaita_row = Adw.ActionRow()
        adwaita_row.set_title("Стандартная тема иконок Adwaita")
        adwaita_row.set_subtitle("icon-theme: Adwaita  |  gtk-theme: Adwaita")
        adwaita_row.add_prefix(_make_icon("preferences-desktop-theme-symbolic"))
        
        self._adwaita_st = _make_status_icon()
        self._adwaita_btn = _make_button("Применить", style="flat")
        self._adwaita_btn.connect("clicked", self._on_adwaita_apply)
        
        adwaita_row.add_suffix(_make_suffix_box(self._adwaita_st, self._adwaita_btn))
        group.add(adwaita_row)

    def _build_folders_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Цветные папки Papirus")
        group.set_description("papirus-folders — выбор цвета папок")
        body.append(group)
        
        color_row = Adw.ActionRow()
        color_row.set_title("Цвет папок")
        color_row.set_subtitle("Выберите цвет и нажмите Применить")
        color_row.add_prefix(_make_icon("folder-symbolic"))
        
        self._folder_color_dd = Gtk.DropDown.new_from_strings(self.FOLDER_COLORS)
        self._folder_color_dd.set_valign(Gtk.Align.CENTER)
        
        self._folder_st = _make_status_icon()
        apply_btn = _make_button("Применить", width=110)
        apply_btn.connect("clicked", self._on_folder_apply)
        
        color_row.add_suffix(_make_suffix_box(self._folder_color_dd, self._folder_st, apply_btn))
        group.add(color_row)
        
        reset_row = Adw.ActionRow()
        reset_row.set_title("Сбросить цвет папок")
        reset_row.set_subtitle("papirus-folders -D")
        reset_row.add_prefix(_make_icon("edit-undo-symbolic"))
        
        reset_btn = _make_button("Сбросить", width=110, style="destructive-action")
        reset_btn.connect("clicked", self._on_folder_reset)
        reset_row.add_suffix(reset_btn)
        group.add(reset_row)

    def _build_wallpapers_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Обои от PLAFON")
        group.set_description("Скачать обои с " + self.WALLPAPER_URL)
        body.append(group)
        row = Adw.ActionRow()
        row.set_title("Открыть сайт с обоями")
        row.set_subtitle(self.WALLPAPER_URL)
        row.add_prefix(_make_icon("image-x-generic-symbolic"))
        open_btn = _make_button("Открыть", style="flat")
        open_btn.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri(self.WALLPAPER_URL, None))
        row.add_suffix(open_btn)
        group.add(row)

    # ── Проверки состояния ────────────────────────────────────────────────────

    def _check_papirus_installed(self):
        ok = subprocess.run(["rpm", "-q", "papirus-remix-icon-theme"], capture_output=True).returncode == 0
        GLib.idle_add(self._set_papirus_installed_ui, ok)

    def _check_active_states(self):
        # Читаем системные настройки
        theme = backend.gsettings_get("org.gnome.desktop.interface", "icon-theme").strip("'\"")
        is_papirus = "Papirus" in theme
        is_adwaita = "Adwaita" in theme
        
        # Читаем сохраненный цвет папок
        folder_color = config.state_get("folder_color")
        
        GLib.idle_add(self._update_states_ui, is_papirus, is_adwaita, folder_color)

    # ── Обновление UI ─────────────────────────────────────────────────────────

    def _set_papirus_installed_ui(self, ok):
        if ok:
            _set_status_ok(self._papirus_install_st)
            self._papirus_install_btn.set_label("Установлен")
            self._papirus_install_btn.set_sensitive(False)
            self._papirus_install_btn.add_css_class("flat")
        else:
            _clear_status(self._papirus_install_st)
            self._papirus_install_btn.set_sensitive(True)

    def _update_states_ui(self, is_papirus, is_adwaita, folder_color):
        # Обновляем кнопку применения Papirus
        if is_papirus:
            _set_status_ok(self._papirus_apply_st)
            self._papirus_apply_btn.set_label("Применено")
            self._papirus_apply_btn.set_sensitive(False)
        else:
            _clear_status(self._papirus_apply_st)
            self._papirus_apply_btn.set_label("Применить")
            self._papirus_apply_btn.set_sensitive(True)

        # Обновляем кнопку применения Adwaita
        if is_adwaita:
            _set_status_ok(self._adwaita_st)
            self._adwaita_btn.set_label("Применено")
            self._adwaita_btn.set_sensitive(False)
        else:
            _clear_status(self._adwaita_st)
            self._adwaita_btn.set_label("Применить")
            self._adwaita_btn.set_sensitive(True)

        # Обновляем выпадающий список и статус цвета папок
        if folder_color and folder_color in self.FOLDER_COLORS:
            self._folder_color_dd.set_selected(self.FOLDER_COLORS.index(folder_color))
            _set_status_ok(self._folder_st)
        else:
            _clear_status(self._folder_st)
            try:
                self._folder_color_dd.set_selected(self.FOLDER_COLORS.index("adwaita"))
            except ValueError:
                pass

    # ── Обработчики нажатий ───────────────────────────────────────────────────

    def _on_papirus_install(self, _):
        self._papirus_install_btn.set_sensitive(False)
        self._papirus_install_btn.set_label("...")
        self._log("\n▶  Установка Papirus...\n")
        backend.run_privileged(
            ["apt-get", "install", "-y", "papirus-remix-icon-theme"],
            self._log,
            lambda ok: (
                GLib.idle_add(self._set_papirus_installed_ui, ok),
                GLib.idle_add(self._log, "\n✔  Papirus установлен!\n" if ok else "\n✘  Ошибка\n"),
            ),
        )

    def _on_papirus_apply(self, _):
        self._papirus_apply_btn.set_sensitive(False)
        self._log("\n▶  Применение Papirus...\n")
        def _do():
            scheme = backend.gsettings_get("org.gnome.desktop.interface", "color-scheme")
            theme = "Papirus-Dark" if "dark" in scheme.lower() else "Papirus"
            ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme])
            self._check_active_states() # Обновляем UI статусы
            GLib.idle_add(self._log, "\n✔  Тема: " + theme + "\n" if ok else "\n✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_adwaita_apply(self, _):
        self._adwaita_btn.set_sensitive(False)
        self._log("\n▶  Применение стандартной темы...\n")
        def _do():
            ok = (
                backend.run_gsettings(["set", "org.gnome.desktop.interface", "gtk-theme", "Adwaita"])
                and backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", "Adwaita"])
            )
            self._check_active_states() # Обновляем UI статусы
            GLib.idle_add(self._log, "\n✔  Adwaita применена!\n" if ok else "\n✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_folder_apply(self, _):
        idx = self._folder_color_dd.get_selected()
        color = self.FOLDER_COLORS[idx] if idx < len(self.FOLDER_COLORS) else "adwaita"
        self._log("\n▶  Цвет папок: " + color + "\n")
        def _do():
            ok_d = subprocess.run(["papirus-folders", "-C", color, "--theme", "Papirus-Dark"], capture_output=True).returncode == 0
            ok_l = subprocess.run(["papirus-folders", "-C", color, "--theme", "Papirus"], capture_output=True).returncode == 0
            ok = ok_d or ok_l
            if ok:
                config.state_set("folder_color", color)
            self._check_active_states() # Обновляем UI статусы
            GLib.idle_add(self._log, "\n✔  Цвет: " + color + "\n" if ok else "\n✘  Ошибка (papirus-folders установлен?)\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_folder_reset(self, _):
        def _do():
            subprocess.run(["papirus-folders", "-D", "--theme", "Papirus-Dark"], capture_output=True)
            subprocess.run(["papirus-folders", "-D", "--theme", "Papirus"], capture_output=True)
            config.state_set("folder_color", None) # Очищаем сохраненный цвет
            self._check_active_states() # Обновляем UI статусы
            GLib.idle_add(self._log, "\n✔  Папки сброшены на стандартный цвет\n")
        threading.Thread(target=_do, daemon=True).start()



# ── TerminalPage ──────────────────────────────────────────────────────────────

class TerminalPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        scroll, body = _make_scrolled_page()
        self.append(scroll)
        
        self._build_ptyxis_group(body)
        self._build_shortcuts_group(body)
        self._build_zsh_group(body)
        self._build_fastfetch_group(body)
        self._build_aliases_group(body)

        # Запускаем фоновые проверки при открытии окна
        threading.Thread(target=self._check_active_states, daemon=True).start()

    def _build_ptyxis_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Ptyxis")
        group.set_description("Современный терминал GNOME, заменяет gnome-terminal")
        body.append(group)
        
        install_row = Adw.ActionRow()
        install_row.set_title("Установить Ptyxis")
        install_row.set_subtitle("epmi ptyxis + удалить gnome-terminal")
        install_row.add_prefix(_make_icon("utilities-terminal-symbolic"))
        
        self._ptyxis_st = _make_status_icon()
        self._ptyxis_btn = _make_button("Установить")
        self._ptyxis_btn.connect("clicked", self._on_ptyxis_install)
        install_row.add_suffix(_make_suffix_box(self._ptyxis_st, self._ptyxis_btn))
        group.add(install_row)
        
        default_row = Adw.ActionRow()
        default_row.set_title("Ptyxis по умолчанию")
        default_row.set_subtitle("xdg-mime default org.gnome.Ptyxis.desktop x-scheme-handler/terminal")
        default_row.add_prefix(_make_icon("starred-symbolic"))
        
        self._default_st = _make_status_icon()
        self._default_btn = _make_button("Применить", style="flat")
        self._default_btn.connect("clicked", self._on_ptyxis_default)
        default_row.add_suffix(_make_suffix_box(self._default_st, self._default_btn))
        group.add(default_row)

    def _build_shortcuts_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Горячие клавиши")
        group.set_description("Шорткаты для открытия терминала")
        body.append(group)
        
        row1 = Adw.ActionRow()
        row1.set_title("Terminal 1")
        row1.set_subtitle("Ctrl + Alt + T")
        row1.add_prefix(_make_icon("input-keyboard-symbolic"))
        
        self._sc1_st = _make_status_icon()
        self._sc1_btn = _make_button("Назначить", width=110)
        self._sc1_btn.connect("clicked", lambda _, b=self._sc1_btn: self._on_shortcut_1(b))
        row1.add_suffix(_make_suffix_box(self._sc1_st, self._sc1_btn))
        group.add(row1)
        
        row2 = Adw.ActionRow()
        row2.set_title("Terminal 2")
        row2.set_subtitle("Super + Enter")
        row2.add_prefix(_make_icon("input-keyboard-symbolic"))
        
        self._sc2_st = _make_status_icon()
        self._sc2_btn = _make_button("Назначить", width=110)
        self._sc2_btn.connect("clicked", lambda _, b=self._sc2_btn: self._on_shortcut_2(b))
        row2.add_suffix(_make_suffix_box(self._sc2_st, self._sc2_btn))
        group.add(row2)

    def _build_zsh_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("ZSH")
        group.set_description("Устанавливает zsh + git + zplug, делает ZSH shell по умолчанию")
        body.append(group)
        
        zsh_row = Adw.ActionRow()
        zsh_row.set_title("Установить git и zsh")
        zsh_row.set_subtitle("apt-get install -y git zsh")
        zsh_row.add_prefix(_make_icon("utilities-terminal-symbolic"))
        
        self._zsh_st = _make_status_icon()
        self._zsh_btn = _make_button("Установить", width=120)
        self._zsh_btn.connect("clicked", self._on_zsh_install)
        zsh_row.add_suffix(_make_suffix_box(self._zsh_st, self._zsh_btn))
        group.add(zsh_row)
        
        zplug_row = Adw.ActionRow()
        zplug_row.set_title("Установить zplug")
        zplug_row.set_subtitle("git clone https://github.com/zplug/zplug ~/.zplug")
        zplug_row.add_prefix(_make_icon("utilities-terminal-symbolic"))
        
        self._zplug_st = _make_status_icon()
        self._zplug_btn = _make_button("Установить", width=120)
        self._zplug_btn.connect("clicked", self._on_zplug_install)
        zplug_row.add_suffix(_make_suffix_box(self._zplug_st, self._zplug_btn))
        group.add(zplug_row)
        
        default_row = Adw.ActionRow()
        default_row.set_title("ZSH по умолчанию")
        default_row.set_subtitle("chsh -s /bin/zsh")
        default_row.add_prefix(_make_icon("system-run-symbolic"))
        
        self._zsh_default_st = _make_status_icon()
        self._zsh_default_btn = _make_button("Применить", width=110)
        self._zsh_default_btn.connect("clicked", self._on_zsh_default)
        default_row.add_suffix(_make_suffix_box(self._zsh_default_st, self._zsh_default_btn))
        group.add(default_row)

    def _build_fastfetch_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Fastfetch + шрифты")
        group.set_description("Системная информация с иконками Nerd Fonts")
        body.append(group)
        
        ff_row = Adw.ActionRow()
        ff_row.set_title("Установить Fastfetch")
        ff_row.set_subtitle("epmi fastfetch")
        ff_row.add_prefix(_make_icon("dialog-information-symbolic"))
        
        self._ff_st = _make_status_icon()
        self._ff_btn = _make_button("Установить", width=120)
        self._ff_btn.connect("clicked", self._on_ff_install)
        ff_row.add_suffix(_make_suffix_box(self._ff_st, self._ff_btn))
        group.add(ff_row)
        
        font_pkg_row = Adw.ActionRow()
        font_pkg_row.set_title("Шрифт FiraCode Nerd Font")
        font_pkg_row.set_subtitle("epmi fonts-ttf-fira-code-nerd")
        font_pkg_row.add_prefix(_make_icon("font-x-generic-symbolic"))
        
        self._font_pkg_st = _make_status_icon()
        self._font_pkg_btn = _make_button("Установить", width=120)
        self._font_pkg_btn.connect("clicked", self._on_font_pkg_install)
        font_pkg_row.add_suffix(_make_suffix_box(self._font_pkg_st, self._font_pkg_btn))
        group.add(font_pkg_row)
        
        font_row = Adw.ActionRow()
        font_row.set_title("Применить шрифт в Ptyxis")
        font_row.set_subtitle("FiraCode Nerd Font Regular 14")
        font_row.add_prefix(_make_icon("font-x-generic-symbolic"))
        
        self._font_st = _make_status_icon()
        self._font_btn = _make_button("Применить", width=110)
        self._font_btn.connect("clicked", self._on_font_apply)
        font_row.add_suffix(_make_suffix_box(self._font_st, self._font_btn))
        group.add(font_row)
        
        cfg_row = Adw.ActionRow()
        cfg_row.set_title("Конфиг plafonfetch.jsonc")
        cfg_row.set_subtitle("Сохраняет в ~/.config/fastfetch/plafonfetch.jsonc")
        cfg_row.add_prefix(_make_icon("document-save-symbolic"))
        
        self._ffcfg_st = _make_status_icon()
        self._ffcfg_btn = _make_button("Установить", width=110)
        self._ffcfg_btn.connect("clicked", self._on_ffcfg_install)
        cfg_row.add_suffix(_make_suffix_box(self._ffcfg_st, self._ffcfg_btn))
        group.add(cfg_row)

    def _build_aliases_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Алиасы PLAFON")
        group.set_description("Добавляет набор алиасов в ~/.zshrc")
        body.append(group)
        
        row = Adw.ActionRow()
        row.set_title("Добавить алиасы в .zshrc")
        row.set_subtitle("Алиасы для epm, flatpak, timeshift, DaVinci и др.")
        row.add_prefix(_make_icon("text-editor-symbolic"))
        
        self._aliases_st = _make_status_icon()
        self._aliases_btn = _make_button("Добавить", width=110)
        self._aliases_btn.connect("clicked", self._on_aliases_add)
        row.add_suffix(_make_suffix_box(self._aliases_st, self._aliases_btn))
        group.add(row)

    # ── Проверки состояния ────────────────────────────────────────────────────

    def _check_active_states(self):
        states = {}
        
        # 1. Ptyxis
        states["ptyxis"] = subprocess.run(["which", "ptyxis"], capture_output=True).returncode == 0
        res = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/terminal"], capture_output=True, text=True)
        states["ptyxis_def"] = "org.gnome.Ptyxis.desktop" in res.stdout
        
        # 2. Shortcuts
        c0 = subprocess.run(["dconf", "read", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/command"], capture_output=True, text=True).stdout
        states["sc1"] = "'ptyxis'" in c0
        c1 = subprocess.run(["dconf", "read", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/command"], capture_output=True, text=True).stdout
        states["sc2"] = "'ptyxis'" in c1
        
        # 3. ZSH & Zplug
        states["zsh"] = subprocess.run(["which", "zsh"], capture_output=True).returncode == 0
        states["zplug"] = os.path.isdir(os.path.expanduser("~/.zplug"))
        username = os.environ.get("USER", "")
        user_info = subprocess.run(["getent", "passwd", username], capture_output=True, text=True).stdout
        states["zsh_def"] = "/zsh" in user_info
        
        # 4. Fastfetch & Fonts
        states["ff"] = subprocess.run(["which", "fastfetch"], capture_output=True).returncode == 0
        states["font_pkg"] = subprocess.run(["rpm", "-q", "fonts-ttf-fira-code-nerd"], capture_output=True).returncode == 0
        font_res = subprocess.run(["dconf", "read", "/org/gnome/Ptyxis/Profiles/default/font-name"], capture_output=True, text=True).stdout
        states["font_applied"] = "FiraCode Nerd Font" in font_res
        states["ff_cfg"] = os.path.exists(os.path.expanduser("~/.config/fastfetch/plafonfetch.jsonc"))
        
        # 5. Aliases
        try:
            with open(os.path.expanduser("~/.zshrc"), "r") as f:
                states["aliases"] = "# === ALT Booster aliases ===" in f.read()
        except OSError:
            states["aliases"] = False
            
        GLib.idle_add(self._update_states_ui, states)

    def _update_states_ui(self, states: dict):
        def toggle(icon, btn, is_ok, ok_text, normal_text, keep_flat=False):
            if is_ok:
                _set_status_ok(icon)
                btn.set_label(ok_text)
                btn.set_sensitive(False)
                if not keep_flat:
                    btn.add_css_class("flat")
                    btn.remove_css_class("suggested-action")
            else:
                _clear_status(icon)
                btn.set_label(normal_text)
                btn.set_sensitive(True)
                if not keep_flat:
                    btn.remove_css_class("flat")
                    btn.add_css_class("suggested-action")

        toggle(self._ptyxis_st, self._ptyxis_btn, states["ptyxis"], "Установлен", "Установить")
        toggle(self._default_st, self._default_btn, states["ptyxis_def"], "Применено", "Применить", keep_flat=True)
        toggle(self._sc1_st, self._sc1_btn, states["sc1"], "Назначен", "Назначить")
        toggle(self._sc2_st, self._sc2_btn, states["sc2"], "Назначен", "Назначить")
        toggle(self._zsh_st, self._zsh_btn, states["zsh"], "Установлен", "Установить")
        toggle(self._zplug_st, self._zplug_btn, states["zplug"], "Установлен", "Установить")
        toggle(self._zsh_default_st, self._zsh_default_btn, states["zsh_def"], "Применено", "Применить")
        toggle(self._ff_st, self._ff_btn, states["ff"], "Установлен", "Установить")
        toggle(self._font_pkg_st, self._font_pkg_btn, states["font_pkg"], "Установлен", "Установить")
        toggle(self._font_st, self._font_btn, states["font_applied"], "Применён", "Применить")
        toggle(self._ffcfg_st, self._ffcfg_btn, states["ff_cfg"], "Установлен", "Установить")
        toggle(self._aliases_st, self._aliases_btn, states["aliases"], "Добавлены", "Добавить")

    # ── Обработчики нажатий ───────────────────────────────────────────────────

    def _on_ptyxis_install(self, _):
        self._ptyxis_btn.set_sensitive(False)
        self._ptyxis_btn.set_label("...")
        self._log("\n▶  Установка Ptyxis...\n")
        backend.run_privileged(
            ["bash", "-c", "apt-get remove -y gnome-terminal 2>/dev/null || true && apt-get install -y ptyxis"],
            self._log,
            lambda ok: (
                self._check_active_states(),
                GLib.idle_add(self._log, "\n✔  Ptyxis установлен!\n" if ok else "\n✘  Ошибка\n"),
            ))

    def _on_ptyxis_default(self, _):
        self._default_btn.set_sensitive(False)
        self._log("\n▶  Применение Ptyxis по умолчанию...\n")
        def _do():
            r = subprocess.run(["xdg-mime", "default", "org.gnome.Ptyxis.desktop", "x-scheme-handler/terminal"], capture_output=True)
            self._check_active_states()
            GLib.idle_add(self._log, "\n✔  Ptyxis default!\n" if r.returncode == 0 else "\n✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_shortcut_1(self, btn):
        btn.set_sensitive(False)
        self._log("\n▶  Настройка Ctrl+Alt+T...\n")
        def _do():
            # Записываем команду, имя и шорткат
            subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/name", "'Terminal 1'"])
            subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/command", "'ptyxis'"])
            subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/binding", "'<Primary><Alt>t'"])
            
            # Добавляем в общий массив шорткатов, если его там нет
            res = subprocess.run(["dconf", "read", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"], capture_output=True, text=True)
            arr = res.stdout.strip()
            path = "'/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/'"
            if path not in arr:
                new_arr = f"[{path}]" if not arr or arr in ("@as []", "[]") else arr[:-1] + f", {path}]"
                subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings", new_arr])
                
            self._check_active_states()
            GLib.idle_add(self._log, "\n✔  Ctrl+Alt+T назначен!\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_shortcut_2(self, btn):
        btn.set_sensitive(False)
        self._log("\n▶  Настройка Super+Enter...\n")
        def _do():
            subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/name", "'Terminal 2'"])
            subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/command", "'ptyxis'"])
            subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/binding", "'<Super>Return'"])
            
            res = subprocess.run(["dconf", "read", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"], capture_output=True, text=True)
            arr = res.stdout.strip()
            path = "'/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/'"
            if path not in arr:
                new_arr = f"[{path}]" if not arr or arr in ("@as []", "[]") else arr[:-1] + f", {path}]"
                subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings", new_arr])
                
            self._check_active_states()
            GLib.idle_add(self._log, "\n✔  Super+Enter назначен!\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_zsh_install(self, _):
        self._zsh_btn.set_sensitive(False)
        self._zsh_btn.set_label("...")
        self._log("\n▶  Установка git + zsh...\n")
        backend.run_privileged(["apt-get", "install", "-y", "git", "zsh"], self._log,
            lambda ok: (
                self._check_active_states(),
                GLib.idle_add(self._log, "\n✔  ZSH Установлен!\n" if ok else "\n✘  Ошибка\n")
            ))

    def _on_zplug_install(self, _):
        self._zplug_btn.set_sensitive(False)
        self._zplug_btn.set_label("...")
        self._log("\n▶  Установка zplug...\n")
        def _do():
            zplug_dir = os.path.expanduser("~/.zplug")
            ok = os.path.isdir(zplug_dir) or subprocess.run(["git", "clone", "https://github.com/zplug/zplug", zplug_dir], capture_output=True).returncode == 0
            self._check_active_states()
            GLib.idle_add(self._log, "\n✔  zplug установлен!\n" if ok else "\n✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_zsh_default(self, _):
        self._zsh_default_btn.set_sensitive(False)
        self._log("\n▶  Смена оболочки по умолчанию...\n")
        username = os.environ.get("USER", "")
        backend.run_privileged(["chsh", "-s", "/bin/zsh", username], self._log,
            lambda ok: (
                self._check_active_states(),
                GLib.idle_add(self._log, "\n✔  ZSH установлен по умолчанию!\n" if ok else "\n✘  Ошибка\n")
            ))

    def _on_ff_install(self, _):
        self._ff_btn.set_sensitive(False)
        self._ff_btn.set_label("...")
        self._log("\n▶  Установка Fastfetch...\n")
        backend.run_epm(["epm", "-i", "fastfetch"], self._log,
            lambda ok: (
                self._check_active_states(),
                GLib.idle_add(self._log, "\n✔  Fastfetch установлен!\n" if ok else "\n✘  Ошибка\n")
            ))

    def _on_font_pkg_install(self, _):
        self._font_pkg_btn.set_sensitive(False)
        self._font_pkg_btn.set_label("...")
        self._log("\n▶  Установка шрифтов...\n")
        backend.run_epm(["epm", "-i", "fonts-ttf-fira-code-nerd"], self._log,
            lambda ok: (
                self._check_active_states(),
                GLib.idle_add(self._log, "\n✔  Шрифты установлены!\n" if ok else "\n✘  Ошибка\n")
            ))

    def _on_font_apply(self, _):
        self._font_btn.set_sensitive(False)
        self._log("\n▶  Применение шрифта...\n")
        def _do():
            r = subprocess.run(["dconf", "write", "/org/gnome/Ptyxis/Profiles/default/font-name", "'FiraCode Nerd Font Regular 14'"], capture_output=True)
            self._check_active_states()
            GLib.idle_add(self._log, "\n✔  Шрифт применён!\n" if r.returncode == 0 else "\n✘  Ошибка dconf\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_ffcfg_install(self, _):
        self._ffcfg_btn.set_sensitive(False)
        self._log("\n▶  Установка конфига Fastfetch...\n")
        def _do():
            cfg_dir = os.path.expanduser("~/.config/fastfetch")
            cfg_path = os.path.join(cfg_dir, "plafonfetch.jsonc")
            try:
                os.makedirs(cfg_dir, exist_ok=True)
                with open(cfg_path, "w") as f:
                    f.write(FASTFETCH_JSON_CONTENT)
                ok = True
            except OSError:
                ok = False
            self._check_active_states()
            GLib.idle_add(self._log, f"\n✔  Конфиг: {cfg_path}\n" if ok else "\n✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_aliases_add(self, _):
        self._aliases_btn.set_sensitive(False)
        self._log("\n▶  Добавление алиасов...\n")
        def _do():
            zshrc_path = os.path.expanduser("~/.zshrc")
            marker = "# === ALT Booster aliases ==="
            try:
                existing = open(zshrc_path).read() if os.path.exists(zshrc_path) else ""
                if marker not in existing:
                    with open(zshrc_path, "a") as f:
                        f.write("\n" + marker + "\n")
                        f.write(ZSH_ALIASES_CONTENT)
                ok = True
            except OSError:
                ok = False
            self._check_active_states()
            GLib.idle_add(self._log, "\n✔  Алиасы добавлены в ~/.zshrc\n" if ok else "\n✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

# ── AmdPage ───────────────────────────────────────────────────────────────────

class AmdPage(Gtk.Box):
    """Вкладка «AMD Radeon» — разгон через GRUB и управление через LACT."""

    OVERCLOCK_PARAMS = (
        "amdgpu.ppfeaturemask=0xffffffff "
        "radeon.cik_support=0 "
        "amdgpu.cik_support=1"
    )
    GRUB_CONF = "/etc/sysconfig/grub2"
    LACT_CONF_DIR = "/etc/lact"
    LACT_CONF_FILE = "/etc/lact/config.json"

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        scroll, body = _make_scrolled_page()
        self.append(scroll)

        self._build_overclock_group(body)
        self._build_lact_group(body)

    # ── Секция разгона ────────────────────────────────────────────────────────

    def _build_overclock_group(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        group.set_title("Разгон AMD Radeon")
        group.set_description(
            "Добавляет параметры amdgpu в GRUB и пересобирает загрузчик. "
            "Требуется перезагрузка."
        )
        body.append(group)

        row = Adw.ActionRow()
        row.set_title("Параметры разгона ядра")
        row.set_subtitle(self.OVERCLOCK_PARAMS)
        row.add_prefix(_make_icon("computer-symbolic"))

        self._oc_st = _make_status_icon()
        self._oc_btn = _make_button("Включить", width=170)
        self._oc_btn.connect("clicked", self._on_overclock)
        self._oc_btn.set_sensitive(False)

        row.add_suffix(_make_suffix_box(self._oc_st, self._oc_btn))
        group.add(row)

        threading.Thread(target=self._check_overclock, daemon=True).start()

        grub_row = Adw.ActionRow()
        grub_row.set_title("Пересобрать GRUB")
        grub_row.set_subtitle("update-grub — применить параметры загрузчика")
        grub_row.add_prefix(_make_icon("system-reboot-symbolic"))

        self._grub_st = _make_status_icon()
        self._grub_btn = _make_button("Пересобрать", width=170, style="destructive-action")
        self._grub_btn.connect("clicked", self._on_grub_rebuild)
        self._grub_btn.set_sensitive(False)

        grub_row.add_suffix(_make_suffix_box(self._grub_st, self._grub_btn))
        group.add(grub_row)

        reboot_row = Adw.ActionRow()
        reboot_row.set_title("Перезагрузить систему")
        reboot_row.set_subtitle("Опции разгона применятся после перезагрузки")
        reboot_row.add_prefix(_make_icon("system-shutdown-symbolic"))

        reboot_st = _make_status_icon()
        reboot_btn = _make_button("Перезагрузить", width=170, style="destructive-action")
        reboot_btn.connect("clicked", self._on_reboot)
        reboot_row.add_suffix(_make_suffix_box(reboot_st, reboot_btn))
        group.add(reboot_row)

    # ── Секция LACT ───────────────────────────────────────────────────────────

    def _build_lact_group(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        group.set_title("LACT — управление видеокартой")
        group.set_description(
            "Linux AMDGPU Controller: мониторинг, разгон и управление вентилятором"
        )
        body.append(group)

        install_row = Adw.ActionRow()
        install_row.set_title("Установить LACT")
        install_row.set_subtitle("epm -i lact")
        install_row.add_prefix(_make_icon("application-x-executable-symbolic"))

        self._lact_st = _make_status_icon()
        self._lact_btn = _make_button("Установить", width=170)
        self._lact_btn.connect("clicked", self._on_lact_install)
        self._lact_btn.set_sensitive(False)

        install_row.add_suffix(_make_suffix_box(self._lact_st, self._lact_btn))
        group.add(install_row)

        threading.Thread(target=self._check_lact, daemon=True).start()

        daemon_row = Adw.ActionRow()
        daemon_row.set_title("Демон lactd")
        daemon_row.set_subtitle("systemctl enable --now lactd")
        daemon_row.add_prefix(_make_icon("system-run-symbolic"))

        self._daemon_st = _make_status_icon()
        self._daemon_btn = _make_button("Включить", width=170)
        self._daemon_btn.connect("clicked", self._on_daemon_enable)
        self._daemon_btn.set_sensitive(False)

        daemon_row.add_suffix(_make_suffix_box(self._daemon_st, self._daemon_btn))
        group.add(daemon_row)

        threading.Thread(target=self._check_daemon, daemon=True).start()

        wheel_row = Adw.ActionRow()
        wheel_row.set_title("Разгон в LACT")
        wheel_row.set_subtitle(
            "Добавляет текущего пользователя в группу wheel для управления GPU"
        )
        wheel_row.add_prefix(_make_icon("security-medium-symbolic"))

        self._wheel_st = _make_status_icon()
        self._wheel_btn = _make_button("Настроить", width=170)
        self._wheel_btn.connect("clicked", self._on_wheel_setup)
        self._wheel_btn.set_sensitive(False)

        wheel_row.add_suffix(_make_suffix_box(self._wheel_st, self._wheel_btn))
        group.add(wheel_row)

        threading.Thread(target=self._check_wheel, daemon=True).start()

        conf_group = Adw.PreferencesGroup()
        conf_group.set_title("Профиль настроек LACT")
        conf_group.set_description(
            "Выберите готовый файл конфига (.json) — он будет применён как основной"
        )
        body.append(conf_group)

        self._conf_row = Adw.ActionRow()
        self._conf_row.set_title("Файл конфига")
        self._conf_row.set_subtitle("Файл не выбран")
        self._conf_row.add_prefix(_make_icon("document-open-symbolic"))

        pick_btn = Gtk.Button(label="Выбрать файл")
        pick_btn.add_css_class("flat")
        pick_btn.set_valign(Gtk.Align.CENTER)
        pick_btn.connect("clicked", self._on_pick_config)

        self._apply_btn = _make_button("Применить конфиг", width=170)
        self._apply_btn.connect("clicked", self._on_apply_config)
        self._apply_btn.set_sensitive(False)

        suffix_box = _make_suffix_box(pick_btn, self._apply_btn)
        self._conf_row.add_suffix(suffix_box)
        conf_group.add(self._conf_row)

        self._selected_conf_path: str | None = None

        self._applied_row = Adw.ActionRow()
        self._applied_row.set_title("Активный конфиг")
        self._applied_row.set_subtitle(self._get_active_conf_subtitle())
        self._applied_row.add_prefix(_make_icon("emblem-ok-symbolic"))
        conf_group.add(self._applied_row)

    # ── Проверки состояния ────────────────────────────────────────────────────

    def _check_overclock(self):
        enabled = self._is_overclock_enabled()
        GLib.idle_add(self._set_oc_ui, enabled)

    def _is_overclock_enabled(self) -> bool:
        try:
            with open(self.GRUB_CONF) as f:
                return "amdgpu.ppfeaturemask=0xffffffff" in f.read()
        except OSError:
            return False

    def _check_lact(self):
        installed = subprocess.run(
            ["which", "lact"], capture_output=True
        ).returncode == 0
        config.state_set("lact_installed", installed)
        GLib.idle_add(self._set_lact_ui, installed)

    def _check_daemon(self):
        result = subprocess.run(
            ["systemctl", "is-enabled", "lactd"],
            capture_output=True, text=True,
        )
        enabled = result.returncode == 0
        GLib.idle_add(self._set_daemon_ui, enabled)

    def _check_wheel(self):
        username = os.environ.get("SUDO_USER") or os.environ.get("USER", "")
        if not username:
            GLib.idle_add(self._set_wheel_ui, False)
            return
        result = subprocess.run(
            ["id", "-nG", username], capture_output=True, text=True
        )
        in_wheel = "wheel" in result.stdout.split()
        GLib.idle_add(self._set_wheel_ui, in_wheel)

    def _get_active_conf_subtitle(self) -> str:
        saved = config.state_get("lact_applied_conf")
        if saved and os.path.exists(saved):
            return f"Применён: {os.path.basename(saved)}"
        if os.path.exists(self.LACT_CONF_FILE):
            return f"Стандартный: {self.LACT_CONF_FILE}"
        return "Конфиг не применён"

    # ── Обновление UI ─────────────────────────────────────────────────────────

    def _set_oc_ui(self, enabled: bool):
        if enabled:
            _set_status_ok(self._oc_st)
            self._oc_btn.set_label("Включено")
            self._oc_btn.set_sensitive(False)
            self._oc_btn.remove_css_class("suggested-action")
            self._oc_btn.add_css_class("flat")
            self._grub_btn.set_sensitive(True)
        else:
            _clear_status(self._oc_st)
            self._oc_btn.set_label("Включить")
            self._oc_btn.set_sensitive(True)

    def _set_lact_ui(self, installed: bool):
        if installed:
            _set_status_ok(self._lact_st)
            self._lact_btn.set_label("Установлен")
            self._lact_btn.set_sensitive(False)
            self._lact_btn.remove_css_class("suggested-action")
            self._lact_btn.add_css_class("flat")
            self._daemon_btn.set_sensitive(True)
            self._wheel_btn.set_sensitive(True)
        else:
            _clear_status(self._lact_st)
            self._lact_btn.set_sensitive(True)

    def _set_daemon_ui(self, enabled: bool):
        if enabled:
            _set_status_ok(self._daemon_st)
            self._daemon_btn.set_label("Активен")
            self._daemon_btn.set_sensitive(False)
            self._daemon_btn.remove_css_class("suggested-action")
            self._daemon_btn.add_css_class("flat")
        else:
            _clear_status(self._daemon_st)
            self._daemon_btn.set_label("Включить")
            if config.state_get("lact_installed"):
                self._daemon_btn.set_sensitive(True)

    def _set_wheel_ui(self, in_wheel: bool):
        if in_wheel:
            _set_status_ok(self._wheel_st)
            self._wheel_btn.set_label("Настроено")
            self._wheel_btn.set_sensitive(False)
            self._wheel_btn.remove_css_class("suggested-action")
            self._wheel_btn.add_css_class("flat")
        else:
            _clear_status(self._wheel_st)
            self._wheel_btn.set_label("Настроить")
            if config.state_get("lact_installed"):
                self._wheel_btn.set_sensitive(True)

    # ── Обработчики действий ──────────────────────────────────────────────────

    def _on_overclock(self, _):
        self._oc_btn.set_sensitive(False)
        self._oc_btn.set_label("…")
        self._log("\n▶  Добавление параметров разгона в GRUB...\n")
        cmd = [
            "bash", "-c",
            "set -e; "
            "CONF=" + self.GRUB_CONF + "; "
            "PARAMS=\"" + self.OVERCLOCK_PARAMS + "\"; "
            "grep -q \"amdgpu.ppfeaturemask=0xffffffff\" \"$CONF\" && exit 0; "
            "sed -i "
            "  \"s|^\\(GRUB_CMDLINE_LINUX_DEFAULT=\\'[^\\']\\+\\)|\\1 $PARAMS|\""
            "  \"$CONF\"",
        ]
        backend.run_privileged(cmd, self._log, self._oc_done)

    def _oc_done(self, ok: bool):
        if ok:
            _set_status_ok(self._oc_st)
            self._oc_btn.set_label("Включено")
            self._oc_btn.remove_css_class("suggested-action")
            self._oc_btn.add_css_class("flat")
            self._grub_btn.set_sensitive(True)
            self._log("\n✔  Параметры добавлены. Пересоберите GRUB и перезагрузитесь.\n")
        else:
            _set_status_error(self._oc_st)
            self._oc_btn.set_label("Повторить")
            self._oc_btn.set_sensitive(True)
            self._log("\n✘  Ошибка записи в GRUB\n")

    def _on_grub_rebuild(self, _):
        self._grub_btn.set_sensitive(False)
        self._grub_btn.set_label("…")
        self._log("\n▶  Пересборка GRUB...\n")
        backend.run_privileged(["update-grub"], self._log, self._grub_done)

    def _grub_done(self, ok: bool):
        if ok:
            _set_status_ok(self._grub_st)
            self._grub_btn.set_label("Готово")
            self._grub_btn.remove_css_class("destructive-action")
            self._grub_btn.add_css_class("flat")
            self._log("\n✔  GRUB пересобран. Перезагрузите систему.\n")
        else:
            _set_status_error(self._grub_st)
            self._grub_btn.set_label("Повторить")
            self._grub_btn.set_sensitive(True)
            self._log("\n✘  Ошибка пересборки GRUB\n")

    def _on_reboot(self, _):
        dialog = Adw.AlertDialog(
            heading="Перезагрузить систему?",
            body="Все несохранённые данные будут потеряны.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reboot", "Перезагрузить")
        dialog.set_response_appearance("reboot", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _do_reboot(_d, response):
            if response == "reboot":
                backend.run_privileged(["reboot"], self._log, lambda _: None)

        dialog.connect("response", _do_reboot)
        widget = self
        while widget.get_parent():
            widget = widget.get_parent()
        dialog.present(widget)

    def _on_lact_install(self, _):
        self._lact_btn.set_sensitive(False)
        self._lact_btn.set_label("…")
        self._log("\n▶  Установка LACT...\n")
        backend.run_epm(["epm", "-i", "lact"], self._log, self._lact_done)

    def _lact_done(self, ok: bool):
        config.state_set("lact_installed", ok)
        self._set_lact_ui(ok)
        self._log("\n✔  LACT установлен!\n" if ok else "\n✘  Ошибка установки LACT\n")

    def _on_daemon_enable(self, _):
        self._daemon_btn.set_sensitive(False)
        self._daemon_btn.set_label("…")
        self._log("\n▶  Включение демона lactd...\n")
        backend.run_privileged(
            ["systemctl", "enable", "--now", "lactd"],
            self._log,
            self._daemon_done,
        )

    def _daemon_done(self, ok: bool):
        self._set_daemon_ui(ok)
        self._log("\n✔  Демон lactd запущен!\n" if ok else "\n✘  Ошибка запуска демона\n")

    def _on_wheel_setup(self, _):
        self._wheel_btn.set_sensitive(False)
        self._wheel_btn.set_label("…")
        self._log("\n▶  Настройка прав для разгона в LACT...\n")

        username = os.environ.get("SUDO_USER") or os.environ.get("USER", "")
        if not username:
            self._log("\n✘  Не удалось определить имя пользователя\n")
            self._wheel_btn.set_sensitive(True)
            return

        cmd = [
            "bash", "-c",
            f"usermod -aG wheel {username} && "
            "sed -i 's|\"admin_group\":.*|\"admin_group\": \"wheel\",|' /etc/lact/config.json 2>/dev/null || true",
        ]
        backend.run_privileged(cmd, self._log, self._wheel_done)

    def _wheel_done(self, ok: bool):
        self._set_wheel_ui(ok)
        if ok:
            self._log("\n✔  Готово! Для применения прав нужно перезайти в сессию.\n")
        else:
            self._log("\n✘  Ошибка настройки прав\n")
            self._wheel_btn.set_sensitive(True)

    # ── Конфиг LACT ───────────────────────────────────────────────────────────

    def _on_pick_config(self, _):
        dialog = Gtk.FileDialog()
        dialog.set_title("Выберите конфиг LACT (.json)")

        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON файлы")
        json_filter.add_pattern("*.json")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(json_filter)
        dialog.set_filters(filters)

        start_dir = os.path.expanduser("~")
        dialog.set_initial_folder(Gio.File.new_for_path(start_dir))

        widget = self
        while widget.get_parent():
            widget = widget.get_parent()
        dialog.open(widget, None, self._on_conf_picked)

    def _on_conf_picked(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file:
                return
            path = file.get_path()
            self._selected_conf_path = path
            self._conf_row.set_subtitle(f"Выбран: {os.path.basename(path)}")
            self._apply_btn.set_sensitive(True)
            self._log(f"📄 Выбран конфиг: {path}\n")
        except Exception:
            pass

    def _on_apply_config(self, _):
        if not self._selected_conf_path:
            return
        path = self._selected_conf_path
        self._apply_btn.set_sensitive(False)
        self._apply_btn.set_label("…")
        self._log(f"\n▶  Применение конфига {os.path.basename(path)}...\n")

        cmd = [
            "bash", "-c",
            f"mkdir -p {self.LACT_CONF_DIR} && "
            f"cp '{path}' {self.LACT_CONF_FILE} && "
            "systemctl restart lactd 2>/dev/null || true",
        ]
        backend.run_privileged(cmd, self._log, lambda ok: self._conf_done(ok, path))

    def _conf_done(self, ok: bool, path: str):
        if ok:
            config.state_set("lact_applied_conf", path)
            self._applied_row.set_subtitle(f"Применён: {os.path.basename(path)}")
            self._apply_btn.set_label("Применён")
            self._apply_btn.add_css_class("flat")
            self._apply_btn.remove_css_class("suggested-action")
            self._log(f"\n✔  Конфиг {os.path.basename(path)} применён и lactd перезапущен!\n")
        else:
            self._apply_btn.set_label("Применить конфиг")
            self._apply_btn.set_sensitive(True)
            self._log("\n✘  Ошибка применения конфига\n")


# ── MaintenancePage ───────────────────────────────────────────────────────────

class MaintenancePage(Gtk.Box):
    """Вкладка «Обслуживание» — задачи с общим прогрессом."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._rows: list[TaskRow] = []
        self._busy = False

        scroll, body = _make_scrolled_page()
        self.append(scroll)

        self._build_progress_header(body)
        self._build_task_group(body)

    def _build_progress_header(self, body: Gtk.Box):
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        body.append(container)

        header = Gtk.Box()
        label = Gtk.Label(label="Общий прогресс")
        label.set_halign(Gtk.Align.START)
        label.add_css_class("caption")
        label.set_hexpand(True)

        self._progress_label = Gtk.Label(label=f"0 / {len(config.TASKS)} задач")
        self._progress_label.add_css_class("caption")

        header.append(label)
        header.append(self._progress_label)
        container.append(header)

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_hexpand(True)
        container.append(self._progress_bar)

        self._btn_all = _make_button("Запустить все задачи")
        self._btn_all.set_halign(Gtk.Align.CENTER)
        self._btn_all.connect("clicked", self._run_all)
        body.append(self._btn_all)

    def _build_task_group(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        group.set_title("Задачи обслуживания")
        body.append(group)

        is_btrfs = config.is_btrfs()
        btrfs_tasks = {"btrfs_bal", "btrfs_defrag", "btrfs_scrub"}

        for task in config.TASKS:
            row = TaskRow(task, self._log, self._update_progress)
            if task["id"] in btrfs_tasks and not is_btrfs:
                row.set_sensitive(False)
                row.set_tooltip_text("Недоступно: раздел не является Btrfs")
            self._rows.append(row)
            group.add(row)

    def set_sensitive_all(self, sensitive: bool):
        self._btn_all.set_sensitive(sensitive)
        for row in self._rows:
            row._btn.set_sensitive(sensitive)

    def _run_all(self, _):
        if self._busy:
            return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Запуск невозможен.\n")
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
        self._progress_bar.set_fraction(done / total if total else 0.0)
        self._progress_label.set_label(f"{done} / {total} задач")


# ── PlafonWindow ──────────────────────────────────────────────────────────────

class PlafonWindow(Adw.ApplicationWindow):
    """Главное окно ALT Booster."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("ALT Booster")

        settings = self._load_window_settings()
        self.set_default_size(
            settings.get("width", 740),
            settings.get("height", 880),
        )
        self.connect("close-request", self._on_close)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        root.append(self._build_header())

        self._setup      = SetupPage(self._log)
        self._apps       = AppsPage(self._log)
        self._appearance = AppearancePage(self._log)
        self._terminal   = TerminalPage(self._log)
        self._amd        = AmdPage(self._log)
        self._davinci    = DaVinciPage(self._log)
        self._maint      = MaintenancePage(self._log)

        self._setup.build_quick_actions(self._apps.run_all_external, self._davinci.run_ready_preset)

        pages = [
            (self._setup,      "setup",       "Начало",      "go-home-symbolic"),
            (self._apps,       "apps",        "Приложения",     "flathub-symbolic"),
            (self._appearance, "appearance",  "Внешний вид",    "preferences-desktop-wallpaper-symbolic"),
            (self._terminal,   "terminal",    "Терминал",       "utilities-terminal-symbolic"),
            (self._amd,        "amd",         "AMD Radeon",     "video-display-symbolic"),
            (self._davinci,    "davinci",     "DaVinci Resolve","davinci-symbolic"),
            (self._maint,      "maintenance", "Обслуживание",   "emblem-system-symbolic"),
        ]
        for widget, name, title, icon in pages:
            page = self._stack.add_titled(widget, name, title)
            page.set_icon_name(icon)

        log_panel = self._build_log_panel()
        self._paned = Gtk.Paned.new(Gtk.Orientation.VERTICAL)
        self._paned.set_start_child(self._stack)
        self._paned.set_end_child(log_panel)
        self._paned.set_vexpand(True)
        self._paned.set_position(settings.get("paned_pos", 720))
        root.append(self._paned)

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        self._stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        header.set_title_widget(switcher)

        menu = Gio.Menu()
        menu.append("О приложении", "win.about")
        menu.append("Очистить лог", "win.clear_log")
        menu.append("Сбросить настройки", "win.reset_state")

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        actions = [
            ("about",       self._show_about),
            ("clear_log",   self._clear_log),
            ("reset_state", self._reset_state),
        ]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        return header

    def _build_log_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.set_margin_start(20)
        panel.set_margin_end(20)
        panel.set_margin_bottom(16)
        panel.set_vexpand(True)

        label = Gtk.Label(label="Лог выполнения")
        label.set_halign(Gtk.Align.START)
        label.add_css_class("heading")
        panel.append(label)

        frame = Gtk.Frame()
        frame.add_css_class("card")
        frame.set_margin_top(6)
        frame.set_vexpand(True)
        panel.append(frame)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        frame.set_child(scroll)

        self._tv = Gtk.TextView()
        self._tv.set_editable(False)
        self._tv.set_cursor_visible(False)
        self._tv.set_monospace(True)
        self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv.set_margin_start(10)
        self._tv.set_margin_end(10)
        self._tv.set_margin_top(8)
        self._tv.set_margin_bottom(8)
        self._buf = self._tv.get_buffer()
        scroll.set_child(self._tv)

        return panel

    # ── Пароль и авторизация ──────────────────────────────────────────────────

    def ask_password(self):
        self._maint.set_sensitive_all(False)
        PasswordDialog(self, self._auth_ok, self.close)

    def _auth_ok(self):
        self._maint.set_sensitive_all(True)
        self._log("👋 Добро пожаловать в ALT Booster. С чего начнём?\n")

    # ── Сохранение размеров окна ──────────────────────────────────────────────

    def _load_window_settings(self) -> dict:
        try:
            with open(config.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _on_close(self, _) -> bool:
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(config.CONFIG_FILE, "w") as f:
                json.dump({
                    "width": self.get_width(),
                    "height": self.get_height(),
                    "paned_pos": self._paned.get_position(),
                }, f)
        except OSError:
            pass
        return False

    # ── Меню «О приложении» и прочие действия ────────────────────────────────

    def _show_about(self, *_):
        dialog = Adw.AboutDialog()
        dialog.set_application_name("ALT Booster")
        dialog.set_application_icon("altbooster")
        dialog.set_developer_name("PLAFON")
        dialog.set_version("2.1")
        dialog.set_website("https://github.com/plafonlinux/altbooster")
        dialog.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        dialog.set_comments(
            "Утилита обслуживания и настройки системы ALT Linux.\n"
            "GTK4 / Adwaita / Python 3"
        )
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_developers(["PLAFON"])
        dialog.set_copyright("© 2026 PLAFON")
        dialog.add_link("📖 ALT Zero — документация", "https://plafon.gitbook.io/alt-zero")
        dialog.add_link("💻 GitHub", "https://github.com/plafonlinux/altbooster")
        dialog.present(self)

    def _clear_log(self, *_):
        self._buf.set_text("")

    def _reset_state(self, *_):
        dialog = Adw.AlertDialog(
            heading="Сбросить настройки?",
            body=(
                "Все сохранённые статусы будут удалены.\n"
                "Утилита повторно проверит состояние системы при следующем запуске."
            ),
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reset", "Сбросить")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _do_reset(_dialog, response):
            if response == "reset":
                config.reset_state()
                self._log("🔄 Настройки сброшены. Перезапустите приложение.\n")

        dialog.connect("response", _do_reset)
        dialog.present(self)

    # ── Лог ───────────────────────────────────────────────────────────────────

    def _log(self, text: str):
        end = self._buf.get_end_iter()
        self._buf.insert(end, text)
        mark = self._buf.create_mark(None, self._buf.get_end_iter(), False)
        self._tv.scroll_mark_onscreen(mark)
