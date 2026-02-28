"""Вкладка «Внешний вид» — темы, иконки и цвета."""

import os
import random
import re
import tempfile
import urllib.request
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import backend
import config
from widgets import make_scrolled_page, make_icon, make_button, make_suffix_box
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
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка иконок Papirus...")
        def _done(ok):
            row.set_done(ok)
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        backend.run_privileged(["apt-get", "install", "-y", "papirus-remix-icon-theme"], self._log, _done)

    def _on_remove_icons(self, row):
        row.set_working()
        self._log("\n▶  Удаление papirus-remix-icon-theme...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление иконок Papirus...")
        def _done(ok):
            row.set_undo_done(ok)
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        backend.run_privileged(["apt-get", "remove", "-y", "papirus-remix-icon-theme"], self._log, _done)

    def _on_install_folders(self, row):
        row.set_working()
        self._log("\n▶  Установка papirus-folders...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка papirus-folders...")
        def _done(ok):
            row.set_done(ok)
            self._refresh_deps()
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        backend.run_privileged(["apt-get", "install", "-y", "papirus-folders"], self._log, _done)

    def _on_remove_folders(self, row):
        row.set_working()
        self._log("\n▶  Удаление papirus-folders...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление papirus-folders...")
        def _done(ok):
            row.set_undo_done(ok)
            self._refresh_deps()
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        backend.run_privileged(["apt-get", "remove", "-y", "papirus-folders"], self._log, _done)

    def _on_color_selected(self, combo, _pspec):
        idx = combo.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        color = self._colors[idx]
        self._log(f"\n▶  Применение цвета папок: {color}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Применение цвета: {color}...")
        
        def _do():
            cmd = ["papirus-folders", "-C", color, "--theme", "Papirus"]
            
            # papirus-folders требует прав root для записи в /usr/share/icons
            backend.run_privileged(cmd, self._log, 
                lambda ok: (self._log("✔  Цвет применён!\n" if ok else "✘  Ошибка\n"), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))
            
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
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Применение темы: {theme_name}...")
        ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme_name])
        row.set_done(ok)
        
        # Обновляем состояние остальных строк (радио-кнопки)
        for r in [self._r_papirus, self._r_alt, self._r_adwaita]:
            if r is not row:
                r._refresh()
        
        self._log("✔  Тема применена!\n" if ok else "✘  Ошибка\n")
        if hasattr(win, "stop_progress"): win.stop_progress(ok)

    # ── Обои ─────────────────────────────────────────────────────────────────

    def _build_wallpapers_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Обои от PLAFON")
        body.append(group)
        
        self._wallpapers_urls = []
        self._current_preview_path = None

        # Предпросмотр (Gtk.Picture)
        self._wallpaper_preview = Gtk.Picture()
        self._wallpaper_preview.set_size_request(-1, 220)
        self._wallpaper_preview.set_content_fit(Gtk.ContentFit.COVER)
        self._wallpaper_preview.add_css_class("card")
        self._wallpaper_preview.set_margin_bottom(12)
        self._wallpaper_preview.set_visible(False) # Скрыт, пока не загрузится
        group.add(self._wallpaper_preview)

        # Загружаем превью в фоне
        threading.Thread(target=self._load_wallpaper_preview, daemon=True).start()
        
        row = Adw.ActionRow()
        row.set_title("Открыть сайт с обоями")
        row.set_subtitle("https://oboi.plafon.org")
        row.add_prefix(make_icon("image-x-generic-symbolic"))
        btn = make_button("Открыть", style="flat")
        btn.set_valign(Gtk.Align.CENTER)
        # ИСПРАВЛЕНИЕ БАГА: Убран GLib.idle_add, который вызывал бесконечный цикл
        btn.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri("https://oboi.plafon.org", None))
        
        rand_btn = make_button("Случайные", width=110)
        rand_btn.set_valign(Gtk.Align.CENTER)
        rand_btn.set_tooltip_text("Установить случайные обои с сайта")
        rand_btn.connect("clicked", self._on_random_wallpaper)
        
        row.add_suffix(make_suffix_box(rand_btn, btn))
        group.add(row)

    def _load_wallpaper_preview(self):
        try:
            url = "https://oboi.plafon.org/photos"
            req = urllib.request.Request(url, headers={"User-Agent": "ALTBooster"})
            with urllib.request.urlopen(req, timeout=5) as r:
                html = r.read().decode("utf-8", errors="ignore")
            
            # Ищем картинки: src="..." или src='...'
            images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
            
            valid_images = []
            for img in images:
                # Проверяем расширение (игнорируем query params)
                if not re.search(r'\.(jpg|jpeg|png|webp)($|\?)', img, re.IGNORECASE):
                    continue

                if not img.startswith("http"):
                    if img.startswith("//"):
                        img = "https:" + img
                    elif img.startswith("/"):
                        img = "https://oboi.plafon.org" + img
                    else:
                        img = "https://oboi.plafon.org/" + img
                
                if "logo" not in img.lower() and "icon" not in img.lower() and "avatar" not in img.lower():
                    valid_images.append(img)
            
            # Fallback: og:image
            if not valid_images:
                og = re.search(r'<meta\s+property="og:image"\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                if og:
                    img = og.group(1)
                    if not img.startswith("http"):
                         img = "https://oboi.plafon.org/" + img.lstrip("/")
                    valid_images.append(img)

            self._wallpapers_urls = list(dict.fromkeys(valid_images))
            
            if self._wallpapers_urls:
                random.shuffle(self._wallpapers_urls)
                self._wallpapers_urls = self._wallpapers_urls[:5]
                self._slideshow_index = 0
                self._show_next_slide()
                GLib.timeout_add_seconds(5, self._show_next_slide)
            else:
                GLib.idle_add(self._log, "ℹ Не удалось найти изображения на сайте обоев.\n")

        except Exception as e:
            GLib.idle_add(self._log, f"✘ Ошибка загрузки превью обоев: {e}\n")

    def _show_next_slide(self):
        if not self._wallpapers_urls:
            return False
        
        url = self._wallpapers_urls[self._slideshow_index]
        self._slideshow_index = (self._slideshow_index + 1) % len(self._wallpapers_urls)
        
        threading.Thread(target=self._download_and_set_preview, args=(url,), daemon=True).start()
        return True # Повторять таймер

    def _download_and_set_preview(self, url):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                with urllib.request.urlopen(url, timeout=10) as r:
                    tmp.write(r.read())
                tmp_path = tmp.name
            
            def _update():
                # Удаляем старый файл
                if self._current_preview_path and os.path.exists(self._current_preview_path):
                    try:
                        os.unlink(self._current_preview_path)
                    except OSError:
                        pass
                
                self._current_preview_path = tmp_path
                self._wallpaper_preview.set_filename(tmp_path)
                self._wallpaper_preview.set_visible(True)
            
            GLib.idle_add(_update)
        except Exception:
            pass

    def _on_random_wallpaper(self, btn):
        if not self._wallpapers_urls:
            return
        
        url = random.choice(self._wallpapers_urls)
        btn.set_sensitive(False)
        self._log(f"\n▶  Установка обоев: {os.path.basename(url)}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка обоев...")

        def _do():
            try:
                dest = os.path.expanduser(f"~/Pictures/Wallpapers/{os.path.basename(url)}")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                urllib.request.urlretrieve(url, dest)
                backend.run_gsettings(["set", "org.gnome.desktop.background", "picture-uri", f"'file://{dest}'"])
                backend.run_gsettings(["set", "org.gnome.desktop.background", "picture-uri-dark", f"'file://{dest}'"])
                GLib.idle_add(self._log, "✔  Обои установлены!\n")
            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка: {e}\n")
            
            GLib.idle_add(btn.set_sensitive, True)
            if hasattr(win, "stop_progress"): GLib.idle_add(win.stop_progress, True)

        threading.Thread(target=_do, daemon=True).start()