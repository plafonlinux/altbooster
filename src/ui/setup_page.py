"""Вкладка «Начало» — системные настройки и раскладка клавиатуры."""

import ast
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
import config
from widgets import make_button, make_scrolled_page
from ui.rows import SettingRow


class SetupPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        scroll, body = make_scrolled_page()
        self._body = body
        self.append(scroll)
        self._build_system_group(body)
        self._build_keyboard_group(body)

    def build_quick_actions(self, apps_cb, dv_cb):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_bottom(14)

        qa_apps = make_button("Установка всех приложений", width=240)
        qa_apps.add_css_class("success")
        qa_apps.connect("clicked", lambda _: apps_cb(qa_apps))
        box.append(qa_apps)

        qa_dv = make_button("DaVinci Resolve Ready", width=240)
        qa_dv.connect("clicked", lambda _: dv_cb(qa_dv))
        box.append(qa_dv)

        self._epm_btn = make_button("Обновить систему (EPM)", width=240, style="destructive-action")
        self._epm_btn.connect("clicked", self._on_epm)
        self._epm_done = False
        box.append(self._epm_btn)

        self._body.prepend(box)

    # ── Системные настройки ──────────────────────────────────────────────────

    def _build_system_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Система")
        body.append(group)

        rows = [
            ("security-high-symbolic", "Включить sudo",
             "control sudowheel enabled", "Активировать",
             self._on_sudo, backend.is_sudo_enabled, "setting_sudo"),
            ("application-x-addon-symbolic", "Подключить Flathub",
             "Устанавливает flatpak и flathub", "Подключить",
             self._on_flathub, backend.is_flathub_enabled, "setting_flathub"),
            ("media-flash-symbolic", "Автоматический TRIM",
             "Включает еженедельную очистку блоков SSD", "Включить",
             self._on_trim_timer, backend.is_fstrim_enabled, "setting_trim_auto"),
            ("document-open-recent-symbolic", "Лимиты журналов",
             "SystemMaxUse=100M и сжатие в journald.conf", "Настроить",
             self._on_journal_limit, backend.is_journal_optimized, "setting_journal_opt"),
            ("video-display-symbolic", "Дробное масштабирование",
             "Включает scale-monitor-framebuffer", "Включить",
             self._on_scale, backend.is_fractional_scaling_enabled, "setting_scale"),
        ]

        self._setting_rows = [SettingRow(*r) for r in rows]
        for r in self._setting_rows:
            group.add(r)

    # ── Раскладка клавиатуры ─────────────────────────────────────────────────

    def _build_keyboard_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Раскладка клавиатуры")
        body.append(group)

        self._r_alt = SettingRow(
            "input-keyboard-symbolic", "Alt + Shift",
            "Классическое переключение раскладки", "Включить",
            self._on_altshift, None, "setting_kbd_altshift",
        )
        self._r_caps = SettingRow(
            "input-keyboard-symbolic", "CapsLock",
            "Переключение раскладки кнопкой CapsLock", "Включить",
            self._on_capslock, None, "setting_kbd_capslock",
        )
        group.add(self._r_alt)
        group.add(self._r_caps)
        threading.Thread(target=self._detect_kbd_mode, daemon=True).start()

    # ── Обработчики системных настроек ───────────────────────────────────────

    def _on_sudo(self, row):
        row.set_working()
        self._log("\n▶  Включение sudo...\n")
        backend.run_privileged(
            ["control", "sudowheel", "enabled"],
            lambda _: None,
            lambda ok: (row.set_done(ok), self._log("✔  sudo включён!\n" if ok else "✘  Ошибка\n")),
        )

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

    def _on_trim_timer(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        row.set_working()
        self._log("\n▶  Включение fstrim.timer...\n")
        backend.run_privileged(
            ["systemctl", "enable", "--now", "fstrim.timer"],
            self._log,
            lambda ok: (row.set_done(ok), self._log("✔  TRIM включён!\n" if ok else "✘  Ошибка\n")),
        )

    def _on_journal_limit(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        row.set_working()
        self._log("\n▶  Оптимизация журналов (создание drop-in конфига)...\n")
        cmd = [
            "bash", "-c",
            "mkdir -p /etc/systemd/journald.conf.d && "
            "echo -e '[Journal]\\nSystemMaxUse=100M\\nCompress=yes' "
            "> /etc/systemd/journald.conf.d/99-altbooster.conf && "
            "systemctl restart systemd-journald",
        ]
        backend.run_privileged(
            cmd, self._log,
            lambda ok: (row.set_done(ok), self._log("✔  Лимиты применены через drop-in!\n" if ok else "✘  Ошибка\n")),
        )

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
            ok = backend.run_gsettings(
                ["set", config.GSETTINGS_MUTTER, "experimental-features", str(feats)]
            )
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  Включено!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    # ── Обработчики раскладки ────────────────────────────────────────────────

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

    def _on_altshift(self, row):
        row.set_working()
        self._log("\n▶  Настройка Alt+Shift...\n")

        def _do():
            ok = (
                backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source", "['<Shift>Alt_L']"])
                and backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source-backward", "['<Alt>Shift_L']"])
            )
            if ok:
                config.state_set("setting_kbd_mode", "altshift")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_caps._set_ui, False)
            GLib.idle_add(self._log, "✔  Alt+Shift готов!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    def _on_capslock(self, row):
        row.set_working()
        self._log("\n▶  Настройка CapsLock...\n")

        def _do():
            ok = (
                backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source", "['Caps_Lock']"])
                and backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source-backward", "['<Shift>Caps_Lock']"])
            )
            if ok:
                config.state_set("setting_kbd_mode", "capslock")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_alt._set_ui, False)
            GLib.idle_add(self._log, "✔  CapsLock готов!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    # ── EPM обновление ───────────────────────────────────────────────────────

    def _on_epm(self, _):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
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
            backend.run_epm(["epm", "-y", "full-upgrade"], self._log, self._epm_fin)

        backend.run_epm(["epm", "-y", "update"], self._log, on_update_done)

    def _epm_fin(self, ok):
        if self._epm_done:
            return
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
