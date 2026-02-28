"""Главное окно приложения ALT Booster."""

import json
import os
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import config
import backend
from dynamic_page import DynamicPage
from ui.common import load_module
from ui.dialogs import PasswordDialog, get_saved_password
from ui.setup_page import SetupPage
from ui.apps_page import AppsPage
from ui.extensions_page import ExtensionsPage
from ui.appearance_page import AppearancePage
from ui.terminal_page import TerminalPage
from ui.davinci_page import DaVinciPage
from ui.maintenance_page import MaintenancePage


class AltBoosterWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Принудительно используем тему иконок Adwaita для приложения,
        # чтобы иконки оставались монохромными даже если в системе выбрана другая тема.
        icon_theme = "Adwaita"
        if not os.path.exists("/usr/share/icons/Adwaita") and os.path.exists("/usr/share/icons/alt-workstation"):
            icon_theme = "alt-workstation"
        Gtk.Settings.get_default().set_property("gtk-icon-theme-name", icon_theme)

        # 1. Лог собирается самым первым
        self._pulse_timer_id = None
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
        self._appearance = AppearancePage(self._log)
        self._terminal = TerminalPage(self._log)
        self._davinci = DaVinciPage(self._log)
        self._maint = MaintenancePage(self._log)

        def _dp(name):
            try:
                return DynamicPage(load_module(name), self._log)
            except Exception as e:
                lbl = Gtk.Label(label=f"Ошибка {name}.json:\n{e}")
                lbl.set_wrap(True)
                return lbl

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

        self._stack.set_vexpand(True)
        root.append(self._stack)
        root.append(self._log_widget)

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
        self._last_log_line = ""
        self._progress_nesting = 0
        self._on_cancel_cb = None
        self._log_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self._log_container.append(sep)

        # 1. Статус
        self._status_label = Gtk.Label(label="Готов к работе")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.set_margin_start(12)
        self._status_label.set_margin_top(12)
        self._status_label.set_margin_bottom(6)
        self._status_label.add_css_class("heading")
        self._log_container.append(self._status_label)

        # 2. Прогресс-бар + Кнопка Стоп
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.set_margin_start(12)
        hbox.set_margin_end(12)
        hbox.set_margin_bottom(12)

        self._progressbar = Gtk.ProgressBar()
        self._progressbar.set_hexpand(True)
        self._progressbar.set_valign(Gtk.Align.CENTER)
        hbox.append(self._progressbar)

        self._stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic")
        self._stop_btn.add_css_class("flat")
        self._stop_btn.add_css_class("circular")
        self._stop_btn.set_tooltip_text("Отменить")
        self._stop_btn.set_sensitive(False)
        self._stop_btn.set_visible(False)
        self._stop_btn.connect("clicked", self._on_stop_clicked)
        hbox.append(self._stop_btn)

        self._log_container.append(hbox)

        # 3. Спойлер с логом
        self._log_expander = Gtk.Expander(label="Лог терминала")
        self._log_expander.set_margin_start(12)
        self._log_expander.set_margin_end(12)
        self._log_expander.set_margin_bottom(12)
        
        self._log_scroll = Gtk.ScrolledWindow()
        self._log_scroll.set_vexpand(False)
        self._log_scroll.set_size_request(-1, 250)
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

        self._log_scroll.set_visible(True)
        self._log_expander.set_child(self._log_scroll)
        self._log_container.append(self._log_expander)
        
        return self._log_container

    # ── Пароль ───────────────────────────────────────────────────────────────

    def ask_password(self):
        self._maint.set_sensitive_all(False)
        
        # Пробуем авто-вход
        saved_pw = get_saved_password()
        if saved_pw:
            self._log("🔑 Найден сохранённый пароль, проверка...\n")
            def _check():
                if backend.sudo_check(saved_pw):
                    backend.set_sudo_password(saved_pw)
                    GLib.idle_add(self._auth_ok)
                    GLib.idle_add(self._log, "✔ Вход выполнен автоматически.\n")
                else:
                    GLib.idle_add(self._show_password_dialog)
            threading.Thread(target=_check, daemon=True).start()
        else:
            self._show_password_dialog()

    def _show_password_dialog(self):
        PasswordDialog(self, self._auth_ok, self.close)

    def _auth_ok(self):
        self._maint.set_sensitive_all(True)
        self._maint.refresh_checks()
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
        d.set_version(config.VERSION)
        d.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        d.set_comments("ALT Booster для ALT Linux\nGTK4 / Adwaita / Python 3 / Data-Driven UI")
        d.set_license_type(Gtk.License.MIT_X11)
        d.set_developers(["PLAFON"])
        d.set_copyright("© 2026 PLAFON")
        d.add_link("📖 ALT Zero", "https://plafon.gitbook.io/alt-zero")
        d.add_link("💻 GitHub", "https://github.com/plafonlinux/altbooster")
        d.add_link("✈ Telegram", "https://t.me/plafonyoutube")
        d.add_link("✈ Чат", "https://t.me/plafonchat")
        d.present(self)

    def _clear_log(self, *_):
        self._buf.set_text("")
        self._last_log_line = ""

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

    def start_progress(self, message: str, on_cancel=None):
        if on_cancel is not None:
            self._on_cancel_cb = on_cancel
        def _do():
            if on_cancel is not None:
                self._progress_nesting = 1  # Новая верхнеуровневая операция
            else:
                self._progress_nesting += 1  # Вложенная операция
            self._status_label.set_label(message)
            self._progressbar.set_fraction(0.0)
            self._stop_btn.set_sensitive(bool(self._on_cancel_cb))
            self._stop_btn.set_visible(bool(self._on_cancel_cb))
            if self._pulse_timer_id:
                GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = GLib.timeout_add(100, self._pulse_progress)
        GLib.idle_add(_do)

    def _on_stop_clicked(self, _):
        if not self._on_cancel_cb:
            return

        dialog = Adw.AlertDialog(
            heading="Остановить операцию?",
            body="Текущий процесс будет прерван. Это может привести к незавершенным изменениям.",
        )
        dialog.add_response("cancel", "Нет")
        dialog.add_response("stop", "Да, остановить")
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "stop":
                if self._on_cancel_cb:
                    self._status_label.set_label("Отмена...")
                    self._stop_btn.set_sensitive(False)
                    self._on_cancel_cb()

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _pulse_progress(self):
        self._progressbar.pulse()
        return True

    def stop_progress(self, success: bool = True):
        def _do():
            if self._pulse_timer_id:
                GLib.source_remove(self._pulse_timer_id)
                self._pulse_timer_id = None
            self._progress_nesting = max(0, self._progress_nesting - 1)
            self._progressbar.set_fraction(1.0)
            label = self._last_log_line or ("✔ Готово" if success else "✘ Ошибка")
            self._status_label.set_label(label)
            if self._progress_nesting == 0:
                self._stop_btn.set_sensitive(False)
                self._stop_btn.set_visible(False)
                self._on_cancel_cb = None
        GLib.idle_add(_do)

    def _log(self, text):
        GLib.idle_add(self._log_internal, text)

    def _log_internal(self, text):
        stripped = text.strip()
        if stripped:
            self._last_log_line = stripped
        end = self._buf.get_end_iter()
        self._buf.insert(end, text)
        end = self._buf.get_end_iter()
        mark = self._buf.get_mark("log_end")
        if mark is None:
            mark = self._buf.create_mark("log_end", end, False)
        else:
            self._buf.move_mark(mark, end)
        self._tv.scroll_mark_onscreen(mark)
