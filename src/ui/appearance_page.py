"""Вкладка «Внешний вид» — темы, иконки и цвета."""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import backend
import config
from widgets import make_scrolled_page, make_icon, make_button
from ui.rows import SettingRow


class AppearancePage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        scroll, body = make_scrolled_page()
        self._body = body
        self.append(scroll)

        self._build_papirus_group(body)
        self._build_themes_group(body)
        self._build_wallpapers_group(body)
        
        # Запускаем проверку зависимостей для обновления UI (цвета папок)
        threading.Thread(target=self._refresh_deps, daemon=True).start()

    def _refresh_deps(self):
        """Обновляет доступность выбора цвета папок."""
        has_folders = backend.check_app_installed({"check": ["which", "papirus-folders"]})
        GLib.idle_add(self._combo_color.set_sensitive, has_folders)

    # ── Papirus ──────────────────────────────────────────────────────────────

    def _build_papirus_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Иконки Papirus")
        group.set_description("Популярный набор иконок и утилита для смены цвета папок")
        body.append(group)

        # 1. Установка иконок
        self._r_icons = SettingRow(
            "application-x-addon-symbolic", "Иконки Papirus",
            "Пакет papirus-remix-icon-theme", "Установить",
            self._on_install_icons, 
            self._check_papirus_installed,
            "app_papirus_icons", "Установлено",
            self._on_remove_icons, "Удалить", "user-trash-symbolic"
        )
        group.add(self._r_icons)

        # 2. Установка утилиты папок
        self._r_folders = SettingRow(
            "folder-symbolic", "Papirus Folders",
            "Утилита papirus-folders для смены цвета", "Установить",
            self._on_install_folders,
            lambda: backend.check_app_installed({"check": ["which", "papirus-folders"]}),
            "app_papirus_folders", "Установлено",
            self._on_remove_folders, "Удалить", "user-trash-symbolic"
        )
        group.add(self._r_folders)

        # 3. Выбор цвета (ComboRow)
        self._combo_color = Adw.ComboRow()
        self._combo_color.set_title("Цвет папок")
        self._combo_color.set_subtitle("Применяется для текущего пользователя")
        self._combo_color.add_prefix(make_icon("applications-graphics-symbolic"))
        
        colors = ["adwaita", "yaru", "black", "blue", "bluegrey", "breeze", "brown", "cyan", "deeporange", "green", "grey", "indigo", "magenta", "nordic", "orange", "palebrown", "pink", "purple", "red", "teal", "violet", "white", "yellow"]
        self._colors = colors
        model = Gtk.StringList.new([c.capitalize() for c in colors])
        self._combo_color.set_model(model)
        self._combo_color.set_sensitive(False) # По умолчанию выключено, пока не проверим наличие
        self._combo_color.connect("notify::selected", self._on_color_selected)
        
        group.add(self._combo_color)

    def _check_papirus_installed(self):
        # Проверяем наличие папки или пакета (разные варианты названий)
        paths = [
            "/usr/share/icons/Papirus",
            "~/.local/share/icons/Papirus",
            "/usr/share/icons/Papirus-Dark",
        ]
        for p in paths:
            if backend.check_app_installed({"check": ["path", p]}):
                return True

        pkgs = ["papirus-icon-theme", "papirus-remix-icon-theme", "icon-theme-papirus", "icon-theme-papirus-remix"]
        for pkg in pkgs:
            if backend.check_app_installed({"check": ["rpm", pkg]}):
                return True
        return False

    def _on_install_icons(self, row):
        row.set_working()
        self._log("\n▶  Установка papirus-remix-icon-theme...\n")
        backend.run_privileged(["apt-get", "install", "-y", "papirus-remix-icon-theme"], self._log, row.set_done)

    def _on_remove_icons(self, row):
        row.set_working()
        self._log("\n▶  Удаление papirus-remix-icon-theme...\n")
        backend.run_privileged(["apt-get", "remove", "-y", "papirus-remix-icon-theme"], self._log, row.set_undo_done)

    def _on_install_folders(self, row):
        row.set_working()
        self._log("\n▶  Установка papirus-folders...\n")
        def _done(ok):
            row.set_done(ok)
            self._refresh_deps()
        backend.run_privileged(["apt-get", "install", "-y", "papirus-folders"], self._log, _done)

    def _on_remove_folders(self, row):
        row.set_working()
        self._log("\n▶  Удаление papirus-folders...\n")
        def _done(ok):
            row.set_undo_done(ok)
            self._refresh_deps()
        backend.run_privileged(["apt-get", "remove", "-y", "papirus-folders"], self._log, _done)

    def _on_color_selected(self, combo, _pspec):
        idx = combo.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        color = self._colors[idx]
        self._log(f"\n▶  Применение цвета папок: {color}...\n")
        
        def _do():
            cmd = ["papirus-folders", "-C", color, "--theme", "Papirus"]
            
            # papirus-folders требует прав root для записи в /usr/share/icons
            backend.run_privileged(cmd, self._log, lambda ok: self._log("✔  Цвет применён!\n" if ok else "✘  Ошибка\n"))
            
        threading.Thread(target=_do, daemon=True).start()

    # ── Темы иконок ──────────────────────────────────────────────────────────

    def _build_themes_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Темы иконок")
        body.append(group)

        self._r_papirus = self._make_theme_row("Papirus", "Papirus", group)
        self._r_alt = self._make_theme_row("ALT Workstation", "alt-workstation", group)
        self._r_adwaita = self._make_theme_row("Adwaita", "Adwaita", group)

    def _make_theme_row(self, title, theme_name, group):
        row = SettingRow(
            "preferences-desktop-wallpaper-symbolic", title,
            f"icon-theme: {theme_name}", "Применить",
            lambda r: self._apply_theme(r, theme_name),
            lambda: theme_name in backend.gsettings_get("org.gnome.desktop.interface", "icon-theme"),
            f"theme_{theme_name}", done_label=""
        )
        group.add(row)
        return row

    def _apply_theme(self, row, theme_name):
        row.set_working()
        self._log(f"\n▶  Применение темы иконок: {theme_name}...\n")
        ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme_name])
        row.set_done(ok)
        
        # Обновляем состояние остальных строк (радио-кнопки)
        for r in [self._r_papirus, self._r_alt, self._r_adwaita]:
            if r is not row:
                r._refresh()
        
        self._log("✔  Тема применена!\n" if ok else "✘  Ошибка\n")

    # ── Обои ─────────────────────────────────────────────────────────────────

    def _build_wallpapers_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Обои от PLAFON")
        body.append(group)
        
        row = Adw.ActionRow()
        row.set_title("Открыть сайт с обоями")
        row.set_subtitle("https://oboi.plafon.org")
        row.add_prefix(make_icon("image-x-generic-symbolic"))
        btn = make_button("Открыть", style="flat")
        btn.connect("clicked", lambda _: GLib.idle_add(Gio.AppInfo.launch_default_for_uri, "https://oboi.plafon.org", None))
        row.add_suffix(btn)
        group.add(row)