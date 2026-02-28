"""Вкладка «Начало» — системные настройки и раскладка клавиатуры."""

import ast
import subprocess
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
        self._build_filemanager_group(body)
        self._build_keyboard_group(body)
        
        config.check_update(self._on_update_found)

    def _on_update_found(self, version, url):
        if not version:
            return
        try:
            current = [int(x) for x in config.VERSION.split(".")]
            latest = [int(x) for x in version.split(".")]
            if latest <= current:
                return
        except ValueError:
            return

        GLib.idle_add(self._show_update_banner, version, url)

    def _show_update_banner(self, version, url):
        group = Adw.PreferencesGroup()
        group.set_title("Доступно обновление")
        self._body.prepend(group)

        row = Adw.ActionRow()
        row.set_title(f"Новая версия {version}")
        row.set_subtitle("Рекомендуется обновить приложение")
        row.add_prefix(Gtk.Image.new_from_icon_name("software-update-available-symbolic"))

        btn = make_button("Скачать")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda _: subprocess.Popen(["xdg-open", url]))
        row.add_suffix(btn)
        group.add(row)
        
    def _on_gnome_software_updates(self, row):
        row.set_working()
        self._log("\n▶  Оптимизация Центра приложений (отключение download-updates)...\n")
        
        def _do():
            # Устанавливаем ключ в false
            ok = backend.run_gsettings(["set", "org.gnome.software", "download-updates", "false"])
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  Автообновления отключены. Центр приложений теперь будет летать!\n" if ok else "✘  Ошибка применения настроек GNOME\n")
            
        threading.Thread(target=_do, daemon=True).start()    

    def _on_gnome_software_updates_undo(self, row):
        row.set_working()
        self._log("\n▶  Включение автообновлений GNOME Software...\n")
        
        def _do():
            ok = backend.run_gsettings(["set", "org.gnome.software", "download-updates", "true"])
            GLib.idle_add(row.set_undo_done, ok)
            GLib.idle_add(self._log, "✔  Автообновления включены.\n" if ok else "✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()
        
    def _on_epm(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
            
        row.set_working()
        self._log("\n▶  epm update...\n")
        
        def on_full_upgrade_done(ok):
            # Всегда разблокируем кнопку, чтобы можно было обновляться снова
            GLib.idle_add(row.set_done, False)
            # Возвращаем исходный текст кнопки
            GLib.idle_add(row._button.set_label, "Обновить")
            
            if ok:
                self._log("\n✔  ALT Linux обновлён!\n")
            else:
                self._log("\n✘  Ошибка обновления\n")
        
        def on_update_done(ok):
            if not ok: 
                GLib.idle_add(row.set_done, False)
                GLib.idle_add(row._button.set_label, "Обновить")
                self._log("\n✘  Ошибка epm update\n")
                return
            self._log("\n▶  epm full-upgrade...\n")
            backend.run_epm(["epm", "-y", "full-upgrade"], self._log, on_full_upgrade_done)
            
        backend.run_epm(["epm", "-y", "update"], self._log, on_update_done)    

    def _on_install_epm(self, row):
        row.set_working()
        self._log("\n▶ Установка EPM (eepm)...\n")
        backend.run_privileged(["apt-get", "install", "-y", "eepm"], self._log, row.set_done)

    def _on_remove_epm(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        row.set_working()
        self._log("\n▶ Удаление EPM (eepm)...\n")
        # Удаляем eepm
        backend.run_privileged(["apt-get", "remove", "-y", "eepm"], self._log, row.set_undo_done)

    # ── Системные настройки ──────────────────────────────────────────────────

    def _build_system_group(self, body):
        # --- ГРУППА 1: ОБНОВЛЕНИЕ И ПАКЕТЫ ---
        pkg_group = Adw.PreferencesGroup()
        pkg_group.set_title("Обновление и пакеты")
        body.append(pkg_group)

        pkg_rows = [
            ("system-software-install-symbolic",   "Установить EPM",              "Пакетный менеджер eepm, необходим для утилиты", "Установить", self._on_install_epm, backend.is_epm_installed, "setting_epm_install", "Установлено", self._on_remove_epm, "Удалить", "user-trash-symbolic"),
            ("software-update-available-symbolic", "Обновить систему (EPM)",      "Выполняет epm update и epm full-upgrade",       "Обновить",    self._on_epm,         lambda: False,            "", "Обновлено"),
        ]

        self._r_epm_install, self._r_epm = [SettingRow(*r) for r in pkg_rows]
        
        for r in (self._r_epm_install, self._r_epm):
            pkg_group.add(r)

        # --- ГРУППА 2: СИСТЕМА ---
        sys_group = Adw.PreferencesGroup()
        sys_group.set_title("Система")
        body.append(sys_group)
        
        sys_rows = [
            ("security-high-symbolic",             "Включить sudo",               "control sudowheel enabled",                     "Активировать", self._on_sudo,           backend.is_sudo_enabled,               "setting_sudo", "Активировано", self._on_sudo_undo, "Отключить"),
            ("application-x-addon-symbolic",       "Подключить Flathub",          "Устанавливает flatpak и flathub",               "Включить",     self._on_flathub,        backend.is_flathub_enabled,            "setting_flathub", "Активировано", self._on_flathub_undo, "Удалить"),
            ("view-refresh-symbolic",    "Автообновление GNOME Software",      "Отключает фоновую загрузку в Центре приложений", "Отключить",    self._on_gnome_software_updates, lambda: backend.gsettings_get("org.gnome.software", "download-updates") == "false", "setting_gnome_software_updates", "Выключено", self._on_gnome_software_updates_undo, "Включить"),
            ("media-flash-symbolic",               "Автоматический TRIM",         "Включает еженедельную очистку блоков SSD",      "Включить",     self._on_trim_timer,           backend.is_fstrim_enabled,             "setting_trim_auto", "Активировано", self._on_trim_timer_undo, "Отключить"),
            ("document-open-recent-symbolic",      "Лимиты журналов",             "SystemMaxUse=100M и сжатие в journald.conf",    "Настроить",    self._on_journal_limit,  backend.is_journal_optimized,          "setting_journal_opt", "Активировано", self._on_journal_limit_undo, "Сбросить"),
            ("video-display-symbolic",             "Дробное масштабирование",     "Включает scale-monitor-framebuffer",            "Включить",     self._on_scale,          backend.is_fractional_scaling_enabled, "setting_scale", "Активировано", self._on_scale_undo, "Отключить"),
        ]
        
        self._r_sudo, self._r_flathub, self._r_gnome_sw, self._r_trim, self._r_journal, self._r_scale = [
            SettingRow(*r) for r in sys_rows
        ]
        
        for r in (
            self._r_sudo, 
            self._r_flathub, 
            self._r_gnome_sw, 
            self._r_trim, 
            self._r_journal, 
            self._r_scale
        ):
            sys_group.add(r)
            
    def _build_filemanager_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Файловый менеджер Nautilus")
        body.append(group)
        
        def _check_nautilus():
            try:
                sort = backend.gsettings_get("org.gtk.gtk4.Settings.FileChooser", "sort-directories-first")
                links = backend.gsettings_get("org.gnome.nautilus.preferences", "show-create-link")
                return "true" in sort.lower() and "true" in links.lower()
            except Exception:
                return False

        rows = [
            ("system-file-manager-symbolic", "Настройки Nautilus", "Сортировка папок, создание ссылок, подписи файлов", "Применить", self._on_nautilus, _check_nautilus, "setting_nautilus", "Применены", self._on_nautilus_undo, "Сбросить"),
            ("drive-harddisk-symbolic", "Индикатор копирования", "Адекватный прогресс-бар копирования (vm.dirty)", "Исправить", self._on_vm_dirty, backend.is_vm_dirty_optimized, "setting_vm_dirty", "Исправлено", self._on_vm_dirty_undo, "Сбросить"),
        ]
        
        # Оставляем строго ДВЕ переменные!
        self._r_naut, self._r_dirty = [
            SettingRow(*r) for r in rows
        ]
        
        # В цикле тоже оставляем только ДВЕ!
        for r in (self._r_naut, self._r_dirty):
            group.add(r)

    def _on_nautilus(self, row):
        row.set_working()
        self._log("\n▶  Применение настроек Nautilus...\n")
        def _do():
            ok1 = backend.run_gsettings(["set", "org.gtk.gtk4.Settings.FileChooser", "sort-directories-first", "true"])
            ok2 = backend.run_gsettings(["set", "org.gnome.nautilus.icon-view", "captions", "['size', 'date_modified', 'none']"])
            ok3 = backend.run_gsettings(["set", "org.gnome.nautilus.preferences", "show-create-link", "true"])
            ok4 = backend.run_gsettings(["set", "org.gnome.nautilus.preferences", "show-delete-permanently", "true"])
            ok = ok1 and ok2 and ok3 and ok4
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  Настройки Nautilus применены!\n" if ok else "✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_nautilus_undo(self, row):
        row.set_working()
        self._log("\n▶  Сброс настроек Nautilus...\n")
        def _do():
            ok1 = backend.run_gsettings(["reset", "org.gtk.gtk4.Settings.FileChooser", "sort-directories-first"])
            ok2 = backend.run_gsettings(["reset", "org.gnome.nautilus.icon-view", "captions"])
            ok3 = backend.run_gsettings(["reset", "org.gnome.nautilus.preferences", "show-create-link"])
            ok4 = backend.run_gsettings(["reset", "org.gnome.nautilus.preferences", "show-delete-permanently"])
            ok = ok1 and ok2 and ok3 and ok4
            GLib.idle_add(row.set_undo_done, ok)
            GLib.idle_add(self._log, "✔  Настройки Nautilus сброшены!\n" if ok else "✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_vm_dirty(self, row):
        row.set_working()
        self._log("\n▶  Настройка кэша копирования (vm.dirty)...\n")
        backend.apply_vm_dirty(self._log,
            lambda ok: (row.set_done(ok), self._log("✔  Кэш копирования исправлен!\n" if ok else "✘  Ошибка\n")))

    def _on_vm_dirty_undo(self, row):
        row.set_working()
        self._log("\n▶  Сброс настроек vm.dirty...\n")
        backend.run_privileged(["rm", "-f", "/etc/sysctl.d/99-altbooster.conf"], self._log, lambda ok: (row.set_undo_done(ok), self._log("✔  Настройки сброшены (требуется перезагрузка для эффекта)\n" if ok else "✘  Ошибка\n")))

    # ── Раскладка клавиатуры ─────────────────────────────────────────────────

    def _build_keyboard_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Раскладка клавиатуры")
        body.append(group)

        self._r_alt = SettingRow(
            "input-keyboard-symbolic", "Alt + Shift",
            "Классическое переключение раскладки", "Включить",
            self._on_altshift, None, "setting_kbd_altshift", done_label=""
        )
        self._r_caps = SettingRow(
            "input-keyboard-symbolic", "CapsLock",
            "Переключение раскладки кнопкой CapsLock", "Включить",
            self._on_capslock, None, "setting_kbd_capslock", done_label=""
        )
        self._r_ctrl = SettingRow(
            "input-keyboard-symbolic", "Ctrl + Shift",
            "Переключение раскладки по Ctrl+Shift", "Включить",
            self._on_ctrlshift, None, "setting_kbd_ctrlshift", done_label=""
        )
        group.add(self._r_alt)
        group.add(self._r_caps)
        group.add(self._r_ctrl)
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

    def _on_sudo_undo(self, row):
        row.set_working()
        self._log("\n▶  Отключение sudo...\n")
        backend.run_privileged(
            ["control", "sudowheel", "disabled"],
            lambda _: None,
            lambda ok: (row.set_undo_done(ok), self._log("✔  sudo отключён!\n" if ok else "✘  Ошибка\n")),
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

    def _on_trim_timer_undo(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        row.set_working()
        self._log("\n▶  Отключение fstrim.timer...\n")
        backend.run_privileged(
            ["systemctl", "disable", "--now", "fstrim.timer"],
            self._log,
            lambda ok: (row.set_undo_done(ok), self._log("✔  TRIM отключён!\n" if ok else "✘  Ошибка\n")),
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

    def _on_journal_limit_undo(self, row):
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        row.set_working()
        self._log("\n▶  Сброс настроек журнала...\n")
        backend.run_privileged(
            ["bash", "-c", "rm -f /etc/systemd/journald.conf.d/99-altbooster.conf && systemctl restart systemd-journald"],
            self._log,
            lambda ok: (row.set_undo_done(ok), self._log("✔  Настройки журнала сброшены!\n" if ok else "✘  Ошибка\n")),
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

    def _on_scale_undo(self, row):
        row.set_working()
        self._log("\n▶  Отключение масштабирования...\n")
        def _do():
            current = backend.gsettings_get(config.GSETTINGS_MUTTER, "experimental-features")
            try:
                feats = ast.literal_eval(current) if current not in ("@as []", "[]", "") else []
            except (ValueError, SyntaxError):
                feats = []
            if "scale-monitor-framebuffer" in feats:
                feats.remove("scale-monitor-framebuffer")
            ok = backend.run_gsettings(["set", config.GSETTINGS_MUTTER, "experimental-features", str(feats)])
            GLib.idle_add(row.set_undo_done, ok)
            GLib.idle_add(self._log, "✔  Отключено!\n" if ok else "✘  Ошибка\n")
        threading.Thread(target=_do, daemon=True).start()

    # ── Обработчики раскладки ────────────────────────────────────────────────

    def _detect_kbd_mode(self):
        value = backend.gsettings_get(config.GSETTINGS_KEYBINDINGS, "switch-input-source")
        is_caps = "Caps" in value
        is_ctrl = "Control" in value
        is_alt = "Alt" in value and not is_ctrl
        
        # Обновляем состояние в state.json, чтобы оно было актуальным
        # при следующем запуске, даже если настройки меняли извне.
        config.state_set("setting_kbd_altshift", is_alt)
        config.state_set("setting_kbd_capslock", is_caps)
        config.state_set("setting_kbd_ctrlshift", is_ctrl)

        if is_caps:
            config.state_set("setting_kbd_mode", "capslock")
        elif is_ctrl:
            config.state_set("setting_kbd_mode", "ctrlshift")
        elif is_alt:
            config.state_set("setting_kbd_mode", "altshift")
            
        GLib.idle_add(self._r_caps._set_ui, is_caps)
        GLib.idle_add(self._r_ctrl._set_ui, is_ctrl)
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
                config.state_set("setting_kbd_capslock", False)
                config.state_set("setting_kbd_ctrlshift", False)
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_caps._set_ui, False)
            GLib.idle_add(self._r_ctrl._set_ui, False)
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
                config.state_set("setting_kbd_altshift", False)
                config.state_set("setting_kbd_ctrlshift", False)
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_alt._set_ui, False)
            GLib.idle_add(self._r_ctrl._set_ui, False)
            GLib.idle_add(self._log, "✔  CapsLock готов!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()

    def _on_ctrlshift(self, row):
        row.set_working()
        self._log("\n▶  Настройка Ctrl+Shift...\n")

        def _do():
            ok = (
                backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source", "['<Shift>Control_L']"])
                and backend.run_gsettings(["set", config.GSETTINGS_KEYBINDINGS, "switch-input-source-backward", "['<Control>Shift_L']"])
            )
            if ok:
                config.state_set("setting_kbd_mode", "ctrlshift")
                config.state_set("setting_kbd_altshift", False)
                config.state_set("setting_kbd_capslock", False)
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_alt._set_ui, False)
            GLib.idle_add(self._r_caps._set_ui, False)
            GLib.idle_add(self._log, "✔  Ctrl+Shift готов!\n" if ok else "✘  Ошибка\n")

        threading.Thread(target=_do, daemon=True).start()
