"""Главное окно приложения ALT Booster."""

import datetime
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import zipfile
import tempfile
import time
import grp

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import config
import backend
from dynamic_page import DynamicPage
from ui.common import load_module
from ui.dialogs import PasswordDialog, get_saved_password, clear_saved_password
from ui.setup_page import SetupPage
from ui.apps_page import AppsPage
from ui.extensions_page import ExtensionsPage
from ui.appearance_page import AppearancePage
from ui.terminal_page import TerminalPage
from ui.davinci_page import DaVinciPage
from ui.maintenance_page import MaintenancePage


class AltBoosterWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        start_time = time.time()
        super().__init__(**kwargs)

        # ── Тема иконок ───────────────────────────────────────────────────────
        # Принудительно выставляем Adwaita, чтобы иконки оставались монохромными
        # даже если в системе выбрана сторонняя тема с цветными иконками.
        icon_theme = "Adwaita"
        if not os.path.exists("/usr/share/icons/Adwaita") and os.path.exists("/usr/share/icons/alt-workstation"):
            icon_theme = "alt-workstation"
        Gtk.Settings.get_default().set_property("gtk-icon-theme-name", icon_theme)

        # ── Лог (строится первым, чтобы все остальные компоненты могли писать в него) ──
        self._pulse_timer_id = None
        self._reset_status_timer_id = None
        # SimpleQueue потокобезопасна без блокировок — идеально для лог-потока
        self._log_queue = queue.SimpleQueue()
        self._log_widget = self._build_log_panel()

        self.set_title("ALT Booster")
        settings = self._load_settings()

        # Путь к лог-файлу инициализируем сразу; ротацию и запись заголовка
        # выполняем в фоновом потоке, чтобы не тормозить старт UI.
        self._log_file = config.CONFIG_DIR / "altbooster.log"
        threading.Thread(target=self._log_writer_loop, daemon=True).start()

        self.set_default_size(settings.get("width", 740), settings.get("height", 880))
        self.connect("close-request", self._on_close)

        # ── Структура окна: ToastOverlay → Box → Header + Stack + Log ─────────
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # ToastOverlay позволяет показывать всплывающие уведомления поверх всего контента
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(root)
        self.set_content(self._toast_overlay)

        root.append(self._build_header())

        # ── Страницы приложения ───────────────────────────────────────────────
        self._setup = SetupPage(self._log)
        self._apps = AppsPage(self._log)
        self._extensions = ExtensionsPage(self._log)
        self._appearance = AppearancePage(self._log)
        self._terminal = TerminalPage(self._log)
        self._davinci = DaVinciPage(self._log)
        self._maint = MaintenancePage(self._log)

        def _dp(name):
            # DynamicPage строится из JSON-описания; при ошибке показываем сообщение вместо краша
            try:
                return DynamicPage(load_module(name), self._log)
            except Exception as e:
                lbl = Gtk.Label(label=f"Ошибка {name}.json:\n{e}")
                lbl.set_wrap(True)
                return lbl

        self._amd = _dp("amd")

        # Регистрируем все страницы в ViewStack; порядок определяет порядок вкладок
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

        startup_ms = (time.time() - start_time) * 1000
        self._log(f"ℹ Startup time: {startup_ms:.2f} ms\n")

    # ── Заголовок окна и меню ─────────────────────────────────────────────────

    def _build_header(self):
        """Строит HeaderBar с переключателем вкладок и меню настроек."""
        header = Adw.HeaderBar()

        # ViewStack создаём здесь (до регистрации страниц в __init__),
        # чтобы он был доступен при добавлении вкладок в цикле выше
        self._stack = Adw.ViewStack()
        sw = Adw.ViewSwitcher()
        sw.set_stack(self._stack)
        header.set_title_widget(sw)

        # ── Структура меню (гамбургер) ────────────────────────────────────────
        menu = Gio.Menu()
        menu.append("Проверить обновления", "win.check_update")

        section_settings = Gio.Menu()
        section_settings.append("Импорт настроек", "win.import_settings")
        section_settings.append("Экспорт настроек", "win.export_settings")
        menu.append_section(None, section_settings)

        section_diag = Gio.Menu()
        section_diag.append("Посмотреть логи", "win.open_log")
        section_diag.append("Очистить лог", "win.clear_log")
        section_diag.append("Очистить кэш", "win.reset_state")
        menu.append_section(None, section_diag)

        section_reset = Gio.Menu()
        section_reset.append("Сбросить сохраненный пароль", "win.reset_password")
        section_reset.append("Сброс настроек приложения", "win.reset_config")
        menu.append_section(None, section_reset)

        section_about = Gio.Menu()
        section_about.append("О приложении", "win.about")
        menu.append_section(None, section_about)

        mb = Gtk.MenuButton()
        mb.set_icon_name("open-menu-symbolic")
        mb.set_menu_model(menu)
        header.pack_end(mb)

        # Регистрируем действия меню как GAction на уровне окна
        actions = [
            ("check_update",    self._check_for_updates),
            ("about",           self._show_about),
            ("clear_log",       self._clear_log),
            ("reset_state",     self._reset_state),
            ("reset_password",  self._reset_password),
            ("reset_config",    self._reset_config),
            ("open_log",        self._open_log_file),
            ("export_settings", self._export_settings),
            ("import_settings", self._import_settings),
        ]
        for name, cb in actions:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)

        return header

    # ── Панель лога (снизу окна) ──────────────────────────────────────────────

    def _build_log_panel(self):
        """Строит нижнюю панель: статус-строка + прогресс-бар + раскрывающийся лог."""
        self._last_log_line = ""
        self._progress_nesting = 0
        self._on_cancel_cb = None
        self._log_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._log_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Статус-строка: показывает последнюю значимую строку лога или «Готов к работе»
        self._status_label = Gtk.Label(label="Ожидание авторизации...")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.set_margin_start(12)
        self._status_label.set_margin_top(12)
        self._status_label.set_margin_bottom(6)
        self._status_label.add_css_class("heading")
        self._log_container.append(self._status_label)

        # Прогресс-бар + кнопка остановки операции
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

        # Раскрывающийся спойлер с полным текстом терминального вывода
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
        self._log_expander.set_child(self._log_scroll)
        self._log_container.append(self._log_expander)

        return self._log_container

    # ── Инициализация лог-файла ───────────────────────────────────────────────

    def _setup_logging(self):
        """Настраивает лог-файл: ротация при превышении 2 МБ, запись заголовка сессии.

        Вызывается в фоновом потоке (_log_writer_loop), чтобы не задерживать старт UI.
        """
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            # Ротация: если файл больше 2 МБ — переименовываем в .old и начинаем новый
            if self._log_file.exists() and self._log_file.stat().st_size > 2 * 1024 * 1024:
                shutil.move(self._log_file, self._log_file.with_suffix(".log.old"))

            # Собираем краткую информацию о системе для заголовка сессии
            sys_info = [f"v{config.VERSION}"]
            try:
                sys_info.append(f"Kernel: {platform.release()}")
                sys_info.append(f"DE: {os.environ.get('XDG_CURRENT_DESKTOP', 'Unknown')}")
                with open("/proc/meminfo") as f:
                    for line in f:
                        if "MemTotal" in line:
                            sys_info.append(f"Mem: {line.split(':')[1].strip()}")
                            break
            except Exception:
                pass

            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(f"\n=== Session started {datetime.datetime.now()} [{' | '.join(sys_info)}] ===\n")
        except Exception as e:
            print(f"Log setup failed: {e}")

    # ── Авторизация ───────────────────────────────────────────────────────────

    def ask_password(self):
        """Определяет метод аутентификации и запрашивает пароль (или обходит запрос).

        Запускается в фоновом потоке: проверки через subprocess могут занимать время.
        Все вызовы UI — через GLib.idle_add, чтобы не нарушать GTK-тред.

        Порядок проверок:
        1. sudo не найден → pkexec
        2. sudo работает без пароля (NOPASSWD/кэш) → сразу _auth_ok
        3. Есть сохранённый пароль → проверяем его тихо
        4. Пользователь не в группе wheel → pkexec
        5. sudowheel отключён → предлагаем включить через pkexec
        6. Всё остальное → диалог ввода пароля
        """
        self._maint.set_sensitive_all(False)

        def _check():
            # 1. sudo не установлен → pkexec единственный вариант
            if not shutil.which("sudo"):
                GLib.idle_add(self._log, "ℹ Sudo не найден. Включен режим pkexec.\n")
                GLib.idle_add(self._use_pkexec_auth)
                return

            # 2. sudo уже работает без пароля (NOPASSWD в sudoers или живой кэш)
            try:
                if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=1).returncode == 0:
                    backend.set_sudo_nopass(True)
                    GLib.idle_add(self._auth_ok)
                    return
            except Exception:
                pass

            # 2.5. Проверяем наличие управляющего терминала.
            # PAM-модули GNOME (polkit, gnome-keyring и т.п.) проверяют PAM_TTY
            # при инициализации: без TTY они подключаются к агенту сессии и принимают
            # ЛЮБОЙ пароль через stdin. С TTY (терминал) — проверяют честно через
            # pam_unix.so. sys.stdin.isatty() False означает запуск из GNOME-ярлыка.
            if not sys.stdin.isatty():
                GLib.idle_add(
                    self._log,
                    "ℹ Запуск без терминала (GNOME). Используется pkexec.\n",
                )
                GLib.idle_add(self._use_pkexec_auth)
                return

            # 3. Пробуем сохранённый в keyring пароль — это бесшумный автовход
            saved_pw = get_saved_password()
            if saved_pw and backend.sudo_check(saved_pw):
                backend.set_sudo_password(saved_pw)
                GLib.idle_add(self._log, "✔ Вход выполнен автоматически.\n")
                GLib.idle_add(self._auth_ok)
                return

            # 4. Пользователь не в группе wheel — sudo в принципе недоступен
            try:
                wheel_gid = grp.getgrnam("wheel").gr_gid
                if wheel_gid not in os.getgroups() and wheel_gid != os.getgid():
                    GLib.idle_add(self._log, "ℹ Пользователь не в группе wheel. Включен режим pkexec.\n")
                    GLib.idle_add(self._use_pkexec_auth)
                    return
            except (KeyError, ImportError, OSError):
                pass

            # 5. Пользователь в wheel, но sudowheel отключён — предлагаем настроить
            if shutil.which("control"):
                try:
                    env = os.environ.copy()
                    env["LC_ALL"] = "C"
                    res = subprocess.run(
                        ["control", "sudowheel"], capture_output=True, text=True, timeout=3, env=env,
                    )
                    out = res.stdout.strip().lower()
                    if "enabled" not in out and "wheelonly" not in out:
                        GLib.idle_add(self._offer_sudowheel_setup)
                        return
                except Exception:
                    pass

            # 6. sudowheel включён, пароль не сохранён → показываем диалог
            GLib.idle_add(self._show_password_dialog)

        threading.Thread(target=_check, daemon=True).start()

    def _show_password_dialog(self):
        PasswordDialog(self, self._auth_ok, self.close)

    def _use_pkexec_auth(self):
        """Переключает всё приложение в pkexec-режим (без sudo)."""
        backend.set_pkexec_mode(True)
        self._log("🔑 Используется pkexec (polkit) для привилегированных команд.\n")
        self._auth_ok()

    def _offer_sudowheel_setup(self):
        """Предлагает диалог включения sudowheel через pkexec с последующим перезапуском."""
        d = Adw.MessageDialog(
            heading="Настройка sudo",
            body=(
                "Ваш пользователь входит в группу wheel, но sudo для wheel не активирован.\n\n"
                "Нажмите «Настроить», чтобы включить sudo через polkit — "
                "утилита автоматически перезапустится."
            ),
        )
        d.set_transient_for(self)
        d.add_response("cancel", "Отмена")
        d.add_response("setup", "Настроить")
        d.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("setup")
        d.connect("response", self._on_sudowheel_response)
        d.present()

    def _on_sudowheel_response(self, dialog, rid):
        dialog.close()
        if rid == "setup":
            self._log("⚙ Включение sudowheel через pkexec...\n")
            threading.Thread(target=self._do_sudowheel_setup, daemon=True).start()
        else:
            # Отмена → продолжаем через pkexec только на этот сеанс
            self._use_pkexec_auth()

    def _do_sudowheel_setup(self):
        """Запускает 'pkexec control sudowheel enabled' и перезапускает приложение."""
        try:
            result = subprocess.run(
                ["pkexec", "control", "sudowheel", "enabled"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "✔ sudowheel включён. Перезапуск...\n")
                GLib.idle_add(self._restart_app)
            else:
                GLib.idle_add(self._log, "❌ Не удалось включить sudowheel. Переключение на pkexec.\n")
                GLib.idle_add(self._use_pkexec_auth)
        except Exception as e:
            GLib.idle_add(self._log, f"❌ Ошибка настройки sudowheel: {e}\n")
            GLib.idle_add(self._use_pkexec_auth)

    def _restart_app(self):
        """Планирует перезапуск процесса через 600 мс (чтобы успел отрисоваться лог)."""
        GLib.timeout_add(600, self._do_restart)

    def _do_restart(self):
        try:
            # execv заменяет текущий процесс — чисто и без лишних процессов
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            # Если execv не сработал — запускаем новый процесс и закрываем старый
            subprocess.Popen([sys.executable] + sys.argv)
            self.get_application().quit()
        return False

    def _auth_ok(self):
        """Вызывается после успешной аутентификации: разблокирует UI и показывает приветствие."""
        self._maint.set_sensitive_all(True)
        self._maint.refresh_checks()
        self._log("👋 Добро пожаловать в ALT Booster. С чего начнём?\n")
        self._status_label.set_label("Готов к работе")

    # ── Настройки окна ────────────────────────────────────────────────────────

    def _load_settings(self):
        """Загружает сохранённые размеры окна. При ошибке возвращает пустой dict."""
        try:
            with open(config.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _on_close(self, _):
        """Сохраняет текущие размеры окна перед закрытием."""
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(config.CONFIG_FILE, "w") as f:
                json.dump({"width": self.get_width(), "height": self.get_height()}, f)
        except OSError:
            pass
        return False

    # ── Действия меню ─────────────────────────────────────────────────────────

    def _check_for_updates(self, *_):
        """Переключается на вкладку «Начало» и запускает проверку обновлений."""
        self._stack.set_visible_child_name("setup")
        self._setup.check_for_updates(manual=True)

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
        """Очищает текстовый буфер лога в UI (файл на диске не трогает)."""
        self._buf.set_text("")
        self._last_log_line = ""

    def _reset_state(self, *_):
        """Показывает диалог подтверждения и удаляет кэш статусов проверок."""
        d = Adw.AlertDialog(
            heading="Очистить кэш?",
            body="Все сохранённые статусы проверок будут удалены.\n"
                 "Утилита заново опросит систему при следующем запуске.",
        )
        d.add_response("cancel", "Отмена")
        d.add_response("reset", "Очистить")
        d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def _on_response(_d, r):
            if r == "reset":
                config.reset_state()
                self._log("🔄 Кэш статусов очищен.\n")
                GLib.timeout_add(1500, self.close)

        d.connect("response", _on_response)
        d.present(self)

    def _reset_password(self, *_):
        """Сбрасывает все сохранённые данные аутентификации и повторяет авторизацию."""
        clear_saved_password()
        backend.set_sudo_password(None)
        backend.set_sudo_nopass(False)
        backend.set_pkexec_mode(False)
        # sudo -k инвалидирует кэш сессии, чтобы следующий sudo точно попросил пароль
        subprocess.run(["sudo", "-k"])
        self._log("🔑 Сохраненный пароль сброшен.\n")
        self.add_toast(Adw.Toast(title="Пароль сброшен"))
        self.ask_password()

    def _reset_config(self, *_):
        """Предупреждает и полностью удаляет директорию конфига с перезапуском."""
        dialog = Adw.AlertDialog(
            heading="Сброс настроек приложения?",
            body="Внимание! Это действие удалит все ваши настройки, списки приложений и кэш.\n"
                 "Приложение будет перезапущено в состоянии «как после установки».",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reset", "Сбросить")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "reset":
                self._log("▶  Сброс конфигурации...\n")
                try:
                    if os.path.exists(config.CONFIG_DIR):
                        shutil.rmtree(config.CONFIG_DIR)
                    self._log("✔  Конфигурация удалена. Перезапуск...\n")
                    os.execl(sys.executable, sys.executable, *sys.argv)
                except Exception as e:
                    self._log(f"✘  Ошибка сброса: {e}\n")
            else:
                self._log("ℹ  Пользователь отменил действие.\n")

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _open_log_file(self, *_):
        """Открывает лог-файл в подходящем редакторе (или через системную ассоциацию)."""
        if not self._log_file.exists():
            self.add_toast(Adw.Toast(title="Файл логов еще не создан"))
            return

        path = str(self._log_file)
        cmd = []

        # Приоритет: gnome-text-editor → gedit → терминал + nano
        if shutil.which("gnome-text-editor"):
            cmd = ["gnome-text-editor", path]
        elif shutil.which("gedit"):
            cmd = ["gedit", path]
        elif shutil.which("nano"):
            term = shutil.which("ptyxis") or shutil.which("gnome-terminal") or shutil.which("kgx")
            if term:
                cmd = [term, "--", "nano", path]

        if cmd:
            try:
                subprocess.Popen(cmd)
                return
            except Exception:
                pass

        # Последний fallback — системная ассоциация файлов
        Gio.AppInfo.launch_default_for_uri(self._log_file.as_uri(), None)

    # ── Экспорт / импорт настроек ─────────────────────────────────────────────

    def _export_settings(self, *_):
        """Показывает диалог выбора файла для экспорта настроек в ZIP."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Экспорт настроек")
        filename = f"altbooster_backup_{datetime.datetime.now().strftime('%Y-%m-%d')}.zip"
        dialog.set_initial_name(filename)

        def _on_save(d, res):
            try:
                file = d.save_finish(res)
                if file:
                    self._do_export(file.get_path())
            except Exception as e:
                self._log(f"✘ Ошибка экспорта: {e}\n")

        dialog.save(self, None, _on_save)

    def _do_export(self, zip_path):
        """Упаковывает всё содержимое CONFIG_DIR в ZIP-архив."""
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # Версия в архиве нужна для проверки совместимости при импорте
                zf.writestr("version", config.VERSION)

                if os.path.exists(config.CONFIG_DIR):
                    for root, _, files in os.walk(config.CONFIG_DIR):
                        for file in files:
                            full_path = os.path.join(root, file)
                            # Пропускаем битые симлинки — иначе ZipFile упадёт с ошибкой
                            if os.path.islink(full_path) and not os.path.exists(full_path):
                                self._log(f"⚠ Пропущен битый symlink: {file}\n")
                                continue
                            rel_path = os.path.relpath(full_path, config.CONFIG_DIR)
                            zf.write(full_path, rel_path)

            self._log(f"✔ Настройки экспортированы в {zip_path}\n")
            self.add_toast(Adw.Toast(title="Экспорт завершен"))
        except Exception as e:
            self._log(f"✘ Ошибка создания архива: {e}\n")

    def _import_settings(self, *_):
        """Показывает диалог выбора ZIP-файла для импорта настроек."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Импорт настроек")
        f = Gtk.FileFilter()
        f.set_name("ZIP архивы")
        f.add_pattern("*.zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)

        def _on_open(d, res):
            try:
                file = d.open_finish(res)
                if file:
                    self._confirm_import(file.get_path())
            except Exception as e:
                self._log(f"✘ Ошибка выбора файла: {e}\n")

        dialog.open(self, None, _on_open)

    def _confirm_import(self, zip_path):
        """Читает версию из архива и показывает диалог подтверждения импорта."""
        imported_ver = "неизвестно"
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if "version" in zf.namelist():
                    imported_ver = zf.read("version").decode("utf-8").strip()
        except Exception as e:
            self._log(f"✘ Ошибка чтения архива: {e}\n")
            return

        body = "Текущие настройки будут перезаписаны. Приложение перезапустится."
        if imported_ver != config.VERSION:
            body += (
                f"\n\n⚠ Внимание: Версия настроек ({imported_ver}) отличается от текущей "
                f"({config.VERSION}). Возможны ошибки совместимости."
            )

        dialog = Adw.AlertDialog(heading="Импортировать настройки?", body=body)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("import", "Импортировать")
        dialog.set_response_appearance("import", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_d, res):
            if res != "import":
                return
            try:
                # Безопасный импорт через временную папку:
                # 1. Распаковываем во tmp
                # 2. Делаем бэкап текущего конфига
                # 3. Копируем из tmp в CONFIG_DIR
                # При ошибке на шаге 3 — восстанавливаем бэкап
                with tempfile.TemporaryDirectory() as tmp_dir:
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(tmp_dir)

                    ver_file = os.path.join(tmp_dir, "version")
                    if os.path.exists(ver_file):
                        os.remove(ver_file)

                    backup_dir = config.CONFIG_DIR.with_suffix(".bak_restore")
                    if os.path.exists(config.CONFIG_DIR):
                        if os.path.exists(backup_dir):
                            shutil.rmtree(backup_dir)
                        shutil.move(config.CONFIG_DIR, backup_dir)

                    try:
                        shutil.copytree(tmp_dir, config.CONFIG_DIR)
                        if os.path.exists(backup_dir):
                            shutil.rmtree(backup_dir)
                    except Exception:
                        # Что-то пошло не так — возвращаем старый конфиг
                        if os.path.exists(backup_dir):
                            if os.path.exists(config.CONFIG_DIR):
                                shutil.rmtree(config.CONFIG_DIR)
                            shutil.move(backup_dir, config.CONFIG_DIR)
                        raise

                self._log("✔ Настройки импортированы. Перезапуск...\n")
                os.execl(sys.executable, sys.executable, *sys.argv)
            except Exception as e:
                self._log(f"✘ Ошибка импорта: {e}\n")

        dialog.connect("response", _on_response)
        dialog.present(self)

    def add_toast(self, toast):
        """Показывает всплывающее уведомление поверх контента."""
        self._toast_overlay.add_toast(toast)

    # ── Прогресс-бар и статус ─────────────────────────────────────────────────

    def start_progress(self, message: str, on_cancel=None):
        """Запускает пульсирующий прогресс-бар с указанным сообщением.

        on_cancel — колбэк для кнопки «Стоп». Если None — кнопка скрыта.
        Поддерживает вложенность: вложенные операции инкрементируют счётчик
        и не скрывают кнопку Stop пока не завершится верхнеуровневая.
        """
        if on_cancel is not None:
            self._on_cancel_cb = on_cancel

        def _do():
            if on_cancel is not None:
                self._progress_nesting = 1
            else:
                self._progress_nesting += 1
            self._status_label.set_label(message)
            self._progressbar.set_fraction(0.0)
            self._stop_btn.set_sensitive(bool(self._on_cancel_cb))
            self._stop_btn.set_visible(bool(self._on_cancel_cb))
            if self._pulse_timer_id:
                GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = GLib.timeout_add(100, self._pulse_progress)
            # Отменяем отложенный сброс статуса, если операции начались заново
            if self._reset_status_timer_id:
                GLib.source_remove(self._reset_status_timer_id)
                self._reset_status_timer_id = None

        GLib.idle_add(_do)

    def _on_stop_clicked(self, _):
        """Показывает диалог подтверждения остановки текущей операции."""
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
            if response == "stop" and self._on_cancel_cb:
                self._status_label.set_label("Отмена...")
                self._stop_btn.set_sensitive(False)
                self._on_cancel_cb()

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _pulse_progress(self):
        """Анимирует прогресс-бар (пульс каждые 100 мс). Возвращает True чтобы продолжаться."""
        self._progressbar.pulse()
        return True

    def stop_progress(self, success: bool = True):
        """Останавливает прогресс-бар, показывает итоговый статус.

        Через 4 секунды статус автоматически возвращается в «Готов к работе».
        """
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
                if self._reset_status_timer_id:
                    GLib.source_remove(self._reset_status_timer_id)
                self._reset_status_timer_id = GLib.timeout_add(4000, self._reset_status_label)

        GLib.idle_add(_do)

    def _reset_status_label(self):
        """Таймерный колбэк: сбрасывает статус-строку в «Готов к работе»."""
        self._reset_status_timer_id = None
        self._status_label.set_label("Готов к работе")
        return False

    # ── Лог ──────────────────────────────────────────────────────────────────

    def _log(self, text):
        """Потокобезопасная запись в лог: всегда выполняет UI-обновление в главном потоке."""
        GLib.idle_add(self._log_internal, text)

    def _log_internal(self, text):
        """Добавляет текст в TextView и очередь для записи в файл.

        Вызывается только из UI-потока (через GLib.idle_add).
        """
        stripped = text.strip()
        if stripped:
            # Сохраняем последнюю значимую строку для статус-строки
            self._last_log_line = stripped

        # Ставим в очередь для фонового лог-потока (без блокировки UI)
        self._log_queue.put(text)

        # Вставляем текст в буфер и прокручиваем до конца
        end = self._buf.get_end_iter()
        self._buf.insert(end, text)
        end = self._buf.get_end_iter()
        mark = self._buf.get_mark("log_end")
        if mark is None:
            mark = self._buf.create_mark("log_end", end, False)
        else:
            self._buf.move_mark(mark, end)
        self._tv.scroll_mark_onscreen(mark)

    def _log_writer_loop(self):
        """Фоновый поток: читает из очереди и пишет строки в лог-файл.

        Также выполняет ротацию и запись заголовка сессии при первом запуске.
        """
        self._setup_logging()
        while True:
            text = self._log_queue.get()
            if not hasattr(self, "_log_file"):
                continue
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(text)
            except Exception:
                pass
