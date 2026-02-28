"""Главное окно приложения ALT Booster."""

import json
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import config
from dynamic_page import DynamicPage
from ui.common import load_module
from ui.dialogs import PasswordDialog
from ui.setup_page import SetupPage
from ui.apps_page import AppsPage
from ui.extensions_page import ExtensionsPage
from ui.davinci_page import DaVinciPage
from ui.maintenance_page import MaintenancePage


class AltBoosterWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        if not hasattr(self, '_on_log_drawer_toggle'):
            print("!!! ОШИБКА: Метод _on_log_drawer_toggle не найден. Файлы могут быть устаревшими. Переустановите приложение. !!!")

        # 1. Лог собирается самым первым
        self._log_widget = self._build_log_panel()
        
        self.set_title("ALT Booster")
        settings = self._load_settings()
        self.set_default_size(settings.get("width", 740), settings.get("height", 880))
        self.connect("close-request", self._on_close)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)
        root.append(self._build_header())

        self._setup = SetupPage(self._log)
        self._apps = AppsPage(self._log)
        self._extensions = ExtensionsPage(self._log)
        self._davinci = DaVinciPage(self._log)
        self._maint = MaintenancePage(self._log)

        def _dp(name):
            try:
                return DynamicPage(load_module(name), self._log)
            except Exception as e:
                lbl = Gtk.Label(label=f"Ошибка {name}.json:\n{e}")
                lbl.set_wrap(True)
                return lbl

        self._appearance = _dp("appearance")
        self._terminal = _dp("terminal")
        self._amd = _dp("amd")

        for widget, name, title, icon in [
            (self._setup,       "setup",       "Начало",          "go-home-symbolic"),
            (self._apps,        "apps",        "Приложения",      "flathub-symbolic"),
            (self._extensions,  "extensions",  "Расширения",      "application-x-addon-symbolic"),
            (self._appearance,  "appearance",  "Внешний вид",     "preferences-desktop-wallpaper-symbolic"),
            (self._terminal,   "terminal",    "Терминал",        "utilities-terminal-symbolic"),
            (self._amd,        "amd",         "AMD Radeon",      "video-display-symbolic"),
            (self._davinci,    "davinci",     "DaVinci Resolve", "davinci-symbolic"),
            (self._maint,      "maintenance", "Обслуживание",    "emblem-system-symbolic"),
        ]:
            p = self._stack.add_titled(widget, name, title)
            p.set_icon_name(icon)

        self._paned = Gtk.Paned.new(Gtk.Orientation.VERTICAL)
        # Включаем обратно стандартный системный разделитель, чтобы можно было менять размер
        self._paned.set_wide_handle(True)
        self._paned.set_start_child(self._stack)
        self._paned.set_end_child(self._log_widget)
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
        menu.append("Очистить кэш", "win.reset_state")
        mb = Gtk.MenuButton(); mb.set_icon_name("open-menu-symbolic"); mb.set_menu_model(menu)
        header.pack_end(mb)
        
        for name, cb in [("about", self._show_about), ("clear_log", self._clear_log), ("reset_state", self._reset_state)]:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)

        return header

    def _build_log_panel(self):
        self._log_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Плоский заголовок только с кнопкой справа
        header = Gtk.CenterBox()
        header.set_margin_top(6)
        header.set_margin_bottom(6)
        header.set_margin_start(12)
        header.set_margin_end(12)

        self._log_drawer_btn = Gtk.Button()
        self._log_drawer_btn.set_icon_name("pan-down-symbolic")
        self._log_drawer_btn.add_css_class("flat")
        self._log_drawer_btn.add_css_class("circular")
        self._log_drawer_btn.set_tooltip_text("Свернуть терминал")
        self._log_drawer_btn.connect("clicked", self._on_log_drawer_toggle)

        right_box = Gtk.Box()
        right_box.set_halign(Gtk.Align.END)
        right_box.append(self._log_drawer_btn)
        header.set_end_widget(right_box)
        
        # Убрали GestureDrag и центральную полоску (pill)

        self._log_container.append(header)
        
        self._log_scroll = Gtk.ScrolledWindow()
        self._log_scroll.set_vexpand(True)
        self._log_scroll.set_min_content_height(50)
        self._tv = Gtk.TextView()
        self._tv.set_editable(False)
        self._tv.set_monospace(True)
        self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv.set_left_margin(10)
        self._tv.set_right_margin(10)
        self._tv.set_top_margin(10)
        self._tv.set_bottom_margin(10)
        self._buf = self._tv.get_buffer()
        self._log_scroll.set_child(self._tv)

        self._log_container.append(self._log_scroll)
        return self._log_container

    # Методы _on_drag_begin и _on_drag_update удалены
        
    def _on_log_drawer_toggle(self, btn):
        if self._log_scroll.get_visible():
            self._log_scroll.set_visible(False)
            btn.set_icon_name("pan-up-symbolic")
            btn.set_tooltip_text("Развернуть терминал")
        else:
            self._log_scroll.set_visible(True)
            btn.set_icon_name("pan-down-symbolic")
            btn.set_tooltip_text("Свернуть терминал")
            
            # Теперь лог ВСЕГДА будет открываться маленьким, не читая старую память
            if hasattr(self, '_paned'):
                self._paned.set_position(880)
        
    # ── Пароль ───────────────────────────────────────────────────────────────

    def ask_password(self):
        self._maint.set_sensitive_all(False)
        PasswordDialog(self, self._auth_ok, self.close)

    def _auth_ok(self):
        self._maint.set_sensitive_all(True)
        self._log("👋 Добро пожаловать в ALT Booster. С чего начнём?\n")

    # ── Настройки окна ───────────────────────────────────────────────────────

    def _load_settings(self):
        try:
            with open(config.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _on_close(self, _):
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

    # ── Меню ─────────────────────────────────────────────────────────────────

    def _show_about(self, *_):
        d = Adw.AboutDialog()
        d.set_application_name("ALT Booster")
        d.set_application_icon("altbooster")
        d.set_developer_name("PLAFON")
        d.set_version("5.4-beta")
        d.set_website("https://github.com/plafonlinux/altbooster")
        d.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        d.set_comments("Утилита настройки системы ALT Linux.\nGTK4 / Adwaita / Python 3 / Data-Driven UI")
        d.set_license_type(Gtk.License.MIT_X11)
        d.set_developers(["PLAFON"])
        d.set_copyright("© 2026 PLAFON")
        d.add_link("📖 ALT Zero", "https://plafon.gitbook.io/alt-zero")
        d.add_link("💻 GitHub", "https://github.com/plafonlinux/altbooster")
        d.present(self)

    def _clear_log(self, *_):
        self._buf.set_text("")

    def _reset_state(self, *_):
        d = Adw.AlertDialog(
            heading="Очистить кэш?",
            body="Все сохранённые статусы проверок будут удалены.\n"
                 "Утилита заново опросит систему при следующем запуске.",
        )
        d.add_response("cancel", "Отмена")
        d.add_response("reset", "Очистить") # <--- Текст на красной кнопке
        d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def _on_response(_d, r):
            if r == "reset":
                config.reset_state()
                self._log("🔄 Кэш статусов очищен.\n") # <--- Текст в терминале
                GLib.timeout_add(1500, self.close)

        d.connect("response", _on_response)
        d.present(self)

    # ── Лог ──────────────────────────────────────────

    def _log(self, text):
        if hasattr(self, '_log_scroll') and not self._log_scroll.get_visible():
            self._on_log_drawer_toggle(self._log_drawer_btn)

        end = self._buf.get_end_iter()
        self._buf.insert(end, text)
        end = self._buf.get_end_iter()
        mark = self._buf.get_mark("log_end")
        if mark is None:
            mark = self._buf.create_mark("log_end", end, False)
        else:
            self._buf.move_mark(mark, end)
        self._tv.scroll_mark_onscreen(mark)
