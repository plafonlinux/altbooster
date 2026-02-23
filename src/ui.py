"""
ui.py — интерфейс GTK4 / Adwaita для ALT Booster.

Структура:
  PasswordDialog   — диалог ввода пароля sudo
  SettingRow       — строка настройки с кнопкой и индикатором состояния
  AppRow           — строка приложения с установкой/удалением
  TaskRow          — строка задачи обслуживания с прогрессбаром
  SetupPage        — вкладка «Настройки»
  AppsPage         — вкладка «Приложения»
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

    Автоматически проверяет текущее состояние через check_fn в фоне,
    либо восстанавливает состояние из кэша config.state.
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

        # Восстанавливаем из кэша или запускаем проверку
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

        # Задача очистки кэша DaVinci формирует команду динамически
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
    """Вкладка «Настройки» — системные параметры, клавиатура, обновление."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        scroll, body = _make_scrolled_page()
        self.append(scroll)

        self._build_system_group(body)
        self._build_keyboard_group(body)
        self._build_update_group(body)

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

    # ── Группа «Обновление системы» ───────────────────────────────────────────

    def _build_update_group(self, body: Gtk.Box):
        group = Adw.PreferencesGroup()
        group.set_title("Обновление системы")
        body.append(group)

        row = Adw.ActionRow()
        row.set_title("Обновить систему через EPM")
        row.set_subtitle("epm update &amp;&amp; epm full-upgrade")
        row.add_prefix(_make_icon("software-update-available-symbolic"))

        self._epm_st = _make_status_icon()
        self._epm_btn = _make_button("Запустить", style="destructive-action")
        self._epm_btn.connect("clicked", self._on_epm)
        self._epm_done = False

        row.add_suffix(_make_suffix_box(self._epm_st, self._epm_btn))
        group.add(row)

    # ── Обработчики настроек системы ─────────────────────────────────────────

    def _on_sudo(self, row: SettingRow):
        row.set_working()
        self._log("\n▶  Включение sudo...\n")
        backend.run_privileged(
            ["control", "sudowheel", "enabled"],
            lambda _: None,
            lambda ok: (
                GLib.idle_add(row.set_done, ok),
                GLib.idle_add(self._log, "✔  sudo включён!\n" if ok else "✘  Ошибка\n"),
            ),
        )

    def _on_flathub(self, row: SettingRow):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        row.set_working()
        self._log("\n▶  Установка Flatpak и Flathub...\n")

        def step2(ok):
            if not ok:
                GLib.idle_add(row.set_done, False)
                return
            backend.run_privileged(
                ["apt-get", "install", "-y", "flatpak-repo-flathub"],
                self._log,
                lambda ok2: (
                    GLib.idle_add(row.set_done, ok2),
                    GLib.idle_add(self._log, "✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n"),
                ),
            )

        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, step2)

    def _on_trim_timer(self, row: SettingRow):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Попробуйте позже.\n")
            return
        row.set_working()
        self._log("\n▶  Включение fstrim.timer...\n")
        backend.run_privileged(
            ["systemctl", "enable", "--now", "fstrim.timer"],
            self._log,
            lambda ok: (
                GLib.idle_add(row.set_done, ok),
                GLib.idle_add(self._log, "✔  Таймер TRIM включён!\n" if ok else "✘  Ошибка\n"),
            ),
        )

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
        backend.run_privileged(
            cmd,
            self._log,
            lambda ok: (
                GLib.idle_add(row.set_done, ok),
                GLib.idle_add(self._log, "✔  Лимиты применены!\n" if ok else "✘  Ошибка\n"),
            ),
        )

    def _on_scale(self, row: SettingRow):
        row.set_working()
        self._log("\n▶  Масштабирование...\n")

        def _do():
            current = backend.gsettings_get("org.gnome.mutter", "experimental-features")
            try:
                features = ast.literal_eval(current) if current not in ("@as []", "[]", "") else []
            except (ValueError, SyntaxError):
                features = []
            if "scale-monitor-framebuffer" not in features:
                features.append("scale-monitor-framebuffer")
            ok = backend.run_gsettings(
                ["set", "org.gnome.mutter", "experimental-features", str(features)]
            )
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  Включено!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    # ── Обработчики раскладки ─────────────────────────────────────────────────

    def _detect_kbd_mode(self):
        """Определяет текущий режим переключения раскладки и обновляет UI."""
        mode = config.state_get("setting_kbd_mode")

        if mode == "altshift":
            GLib.idle_add(self._r_alt._set_ui, True)
            GLib.idle_add(self._r_caps._set_ui, False)
            return
        if mode == "capslock":
            GLib.idle_add(self._r_caps._set_ui, True)
            GLib.idle_add(self._r_alt._set_ui, False)
            return

        # Кэша нет — читаем из gsettings
        value = backend.gsettings_get("org.gnome.desktop.wm.keybindings", "switch-input-source")
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
                    "set", "org.gnome.desktop.wm.keybindings",
                    "switch-input-source", "['<Shift>Alt_L']",
                ])
                and backend.run_gsettings([
                    "set", "org.gnome.desktop.wm.keybindings",
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
                    "set", "org.gnome.desktop.wm.keybindings",
                    "switch-input-source", "['Caps_Lock']",
                ])
                and backend.run_gsettings([
                    "set", "org.gnome.desktop.wm.keybindings",
                    "switch-input-source-backward", "['<Shift>Caps_Lock']",
                ])
            )
            if ok:
                config.state_set("setting_kbd_mode", "capslock")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_alt._set_ui, False)
            GLib.idle_add(self._log, "✔  CapsLock готов!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    # ── Обновление системы ────────────────────────────────────────────────────

    def _on_epm(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята другим процессом обновления.\n")
            return

        self._epm_done = False
        self._epm_btn.set_sensitive(False)
        self._epm_btn.set_label("…")
        self._log("\n▶  epm update...\n")

        def on_update_done(ok):
            if not ok:
                GLib.idle_add(self._epm_fin, False)
                return
            self._log("\n▶  epm full-upgrade...\n")
            backend.run_epm(
                ["epm", "-y", "full-upgrade"],
                self._log,
                lambda ok2: GLib.idle_add(self._epm_fin, True),
            )

        backend.run_epm(["epm", "-y", "update"], self._log, on_update_done)

    def _epm_fin(self, ok: bool):
        if self._epm_done:
            return
        self._epm_done = True

        if ok:
            _set_status_ok(self._epm_st)
            self._log("\n✔  ALT Linux последней версии установлен!\n")
        else:
            _set_status_error(self._epm_st)
            self._log("\n✘  Ошибка обновления\n")

        self._epm_btn.set_label("Запустить")
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
        row.set_subtitle("epmi alsa-plugins-pulse")
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
        pkgs = ["libGLU", "ffmpeg", "rocm-opencl-runtime", "hip-runtime-amd", "clinfo"]
        ok = all(
            subprocess.run(["rpm", "-q", p], capture_output=True).returncode == 0
            for p in pkgs
        )
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
            lambda ok: GLib.idle_add(self._on_install_done, ok),
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
                subprocess.run(["tar", "-xzf", archive, "-C", tmp])

                install_sh = None
                for root, _, files in os.walk(tmp):
                    if "install.sh" in files:
                        install_sh = os.path.join(root, "install.sh")
                        break

                if not install_sh:
                    GLib.idle_add(self._aac_fail, "install.sh не найден")
                    return

                subprocess.run(["chmod", "+x", install_sh])
                backend.run_privileged(
                    ["bash", install_sh],
                    self._log,
                    lambda ok: (
                        GLib.idle_add(self._set_aac_ui, ok),
                        GLib.idle_add(self._log, "✔  AAC готов!\n" if ok else "✘  Ошибка\n"),
                    ),
                )
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

        # Находим корневое окно
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

        self._setup = SetupPage(self._log)
        self._apps = AppsPage(self._log)
        self._davinci = DaVinciPage(self._log)
        self._maint = MaintenancePage(self._log)

        pages = [
            (self._setup,   "setup",       "Настройки",      "preferences-system-symbolic"),
            (self._apps,    "apps",        "Приложения",     "flathub-symbolic"),
            (self._davinci, "davinci",     "DaVinci Resolve","davinci-symbolic"),
            (self._maint,   "maintenance", "Обслуживание",   "emblem-system-symbolic"),
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
        return False  # разрешаем закрытие окна

    # ── Меню «О приложении» и прочие действия ────────────────────────────────

    def _show_about(self, *_):
        dialog = Adw.AboutDialog()
        dialog.set_application_name("ALT Booster")
        dialog.set_application_icon("altbooster")
        dialog.set_developer_name("PLAFON")
        dialog.set_version("2.0")
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
                config._state.clear()
                config.save_state()
                self._log("🔄 Настройки сброшены. Перезапустите приложение.\n")

        dialog.connect("response", _do_reset)
        dialog.present(self)

    # ── Лог ───────────────────────────────────────────────────────────────────

    def _log(self, text: str):
        end = self._buf.get_end_iter()
        self._buf.insert(end, text)
        mark = self._buf.create_mark(None, self._buf.get_end_iter(), False)
        self._tv.scroll_mark_onscreen(mark)
