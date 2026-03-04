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
from system import profile as profile_module
from ui.profile_dialog import show_preset_save_dialog, show_preset_import_dialog
from ui.setup_page import SetupPage
from ui.apps_page import AppsPage
from ui.extensions_page import ExtensionsPage
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
        root.append(self._build_update_banner())
        root.append(self._build_profile_banner())

        # ── Страницы приложения ───────────────────────────────────────────────
        self._setup = SetupPage(self._log)
        self._apps = AppsPage(self._log)
        self._extensions = ExtensionsPage(self._log)
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

        # ── Кнопка выбора пресета (левый угол) ───────────────────────────────
        header.pack_start(self._build_preset_button())

        # ── Структура меню (гамбургер) ────────────────────────────────────────
        menu = Gio.Menu()

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

        # Кнопка проверки обновлений — рядом с бургером (левее него)
        self._update_check_btn = Gtk.Button()
        self._update_check_btn.set_icon_name("software-update-available-symbolic")
        self._update_check_btn.set_tooltip_text("Проверить обновления")
        self._update_check_btn.add_css_class("flat")
        self._update_check_btn.connect("clicked", self._check_for_updates)
        header.pack_end(self._update_check_btn)

        # Регистрируем действия меню как GAction на уровне окна
        actions = [
            ("check_update",   self._check_for_updates),
            ("about",          self._show_about),
            ("clear_log",      self._clear_log),
            ("reset_state",    self._reset_state),
            ("reset_password", self._reset_password),
            ("reset_config",   self._reset_config),
            ("open_log",       self._open_log_file),
        ]
        for name, cb in actions:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)

        return header

    def _build_update_banner(self):
        """Плавающий баннер обновления под хедером — по аналогии с баннером в Приложениях."""
        outer = Gtk.Box()
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_top(6)
        outer.set_margin_bottom(4)
        outer.set_opacity(0.92)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("ab-float-banner")

        icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
        icon.set_pixel_size(16)
        box.append(icon)

        self._update_banner_label = Gtk.Label()
        self._update_banner_label.set_xalign(0.0)
        box.append(self._update_banner_label)

        go_btn = Gtk.Button(label="Обновить")
        go_btn.add_css_class("suggested-action")
        go_btn.add_css_class("pill")
        go_btn.set_valign(Gtk.Align.CENTER)
        go_btn.connect("clicked", self._go_to_update)
        box.append(go_btn)

        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.connect("clicked", lambda _: self._update_banner_revealer.set_reveal_child(False))
        box.append(close_btn)

        outer.append(box)

        self._update_banner_revealer = Gtk.Revealer()
        self._update_banner_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._update_banner_revealer.set_transition_duration(300)
        self._update_banner_revealer.set_child(outer)
        self._update_banner_revealer.set_reveal_child(False)
        return self._update_banner_revealer

    # ── Пресеты ───────────────────────────────────────────────────────────────

    def _build_preset_button(self) -> Gtk.MenuButton:
        """Строит кнопку выбора пресета для левого угла хедера."""
        self._preset_btn = Gtk.MenuButton()
        self._preset_btn.add_css_class("flat")

        self._preset_popover = Gtk.Popover()
        self._preset_popover.set_has_arrow(False)
        self._preset_btn.set_popover(self._preset_popover)

        self._refresh_preset_menu()
        return self._preset_btn

    def _refresh_preset_menu(self):
        """Перестраивает содержимое popover'а пресетов и обновляет метку кнопки."""
        presets = profile_module.list_presets()
        active_name = config.state_get("active_preset")

        # Метка кнопки = имя активного пресета, иначе «Default»
        self._preset_btn.set_label(active_name or "Default")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        def _flat_row(label: str, icon: str, cb, sensitive: bool = True) -> Gtk.Button:
            """Плоская кнопка-строка для popover-меню."""
            row = Gtk.Box(spacing=8)
            row.set_margin_start(4)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(16)
            row.append(img)
            lbl_w = Gtk.Label(label=label)
            lbl_w.set_xalign(0.0)
            lbl_w.set_hexpand(True)
            row.append(lbl_w)
            btn = Gtk.Button()
            btn.set_child(row)
            btn.add_css_class("flat")
            btn.set_sensitive(sensitive)
            btn.connect("clicked", cb)
            return btn

        # ── Список сохранённых пресетов ──────────────────────────────────────
        if presets:
            for p_name, _ in presets:
                row = Gtk.Box(spacing=8)
                row.set_margin_start(4)
                check_img = Gtk.Image.new_from_icon_name("object-select-symbolic")
                check_img.set_pixel_size(16)
                check_img.set_opacity(1.0 if p_name == active_name else 0.0)
                row.append(check_img)
                lbl_w = Gtk.Label(label=p_name)
                lbl_w.set_xalign(0.0)
                lbl_w.set_hexpand(True)
                row.append(lbl_w)
                btn = Gtk.Button()
                btn.set_child(row)
                btn.add_css_class("flat")
                btn.connect("clicked", lambda _, n=p_name: self._on_preset_selected(n))
                box.append(btn)
        else:
            placeholder = Gtk.Label(label="Нет сохранённых пресетов")
            placeholder.add_css_class("dim-label")
            placeholder.set_margin_top(6)
            placeholder.set_margin_bottom(6)
            placeholder.set_margin_start(8)
            box.append(placeholder)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Действия с пресетами ─────────────────────────────────────────────
        has_active = bool(active_name and any(n == active_name for n, _ in presets))
        box.append(_flat_row("Сохранить как новый…",    "list-add-symbolic",        self._on_preset_save_new))
        box.append(_flat_row("Переименовать текущий…",  "document-edit-symbolic",   self._on_preset_rename,  has_active))
        box.append(_flat_row("Удалить текущий",         "user-trash-symbolic",      self._on_preset_delete,  has_active))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Файловый экспорт/импорт ───────────────────────────────────────────
        box.append(_flat_row("Экспортировать в файл…",  "document-send-symbolic",   self._on_preset_export_file))
        box.append(_flat_row("Импортировать из файла…", "document-open-symbolic",   self._on_preset_import_file))

        self._preset_popover.set_child(box)

    def _on_preset_selected(self, name: str):
        """Выбор пресета из списка — показывает диалог применения."""
        self._preset_popover.popdown()
        for p_name, path in profile_module.list_presets():
            if p_name == name:
                try:
                    data = profile_module.load_preset(path)
                    show_preset_import_dialog(self, data, self._do_apply_preset)
                except Exception as e:
                    self._log(f"✘ Ошибка загрузки пресета: {e}\n")
                return

    def _on_preset_save_new(self, *_):
        """Сохранить текущее состояние как новый пресет."""
        self._preset_popover.popdown()
        existing = [n for n, _ in profile_module.list_presets()]
        show_preset_save_dialog(self, existing, self._do_save_preset)

    def _on_preset_rename(self, *_):
        """Переименовать текущий активный пресет."""
        self._preset_popover.popdown()
        active_name = config.state_get("active_preset")
        if not active_name:
            return
        existing = [n for n, _ in profile_module.list_presets() if n != active_name]

        def _do_rename(new_name: str):
            for p_name, path in profile_module.list_presets():
                if p_name == active_name:
                    try:
                        data = profile_module.load_preset(path)
                        new_path = profile_module.save_preset(data, new_name)
                        if new_path != path:
                            path.unlink(missing_ok=True)
                        config.state_set("active_preset", new_name)
                        self._refresh_preset_menu()
                        self._log(f"✔ Пресет переименован: «{active_name}» → «{new_name}»\n")
                    except Exception as e:
                        self._log(f"✘ Ошибка переименования: {e}\n")
                    return

        show_preset_save_dialog(self, existing, _do_rename)

    def _on_preset_delete(self, *_):
        """Удалить текущий активный пресет (с подтверждением)."""
        self._preset_popover.popdown()
        active_name = config.state_get("active_preset")
        if not active_name:
            return

        d = Adw.AlertDialog(
            heading=f"Удалить пресет «{active_name}»?",
            body="Файл пресета будет удалён. Это действие нельзя отменить.",
        )
        d.add_response("cancel", "Отмена")
        d.add_response("delete", "Удалить")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def _on_resp(_, r):
            if r != "delete":
                return
            for p_name, path in profile_module.list_presets():
                if p_name == active_name:
                    try:
                        path.unlink(missing_ok=True)
                        config.state_set("active_preset", None)
                        self._refresh_preset_menu()
                        self._log(f"🗑 Пресет «{active_name}» удалён.\n")
                    except Exception as e:
                        self._log(f"✘ Ошибка удаления: {e}\n")
                    return

        d.connect("response", _on_resp)
        d.present(self)

    def _on_preset_export_file(self, *_):
        """Экспортировать текущий пресет (или текущее состояние) в .altbooster файл.

        Если активный пресет сохранён — копирует его файл напрямую.
        Если нет — собирает текущее состояние системы и сохраняет как «Default».
        """
        self._preset_popover.popdown()
        active_name = config.state_get("active_preset") or "Default"

        import datetime as _dt
        date_str = _dt.datetime.now().strftime("%Y-%m-%d")
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in active_name).strip()
        filename = f"{safe_name}-{date_str}.altbooster"

        dialog = Gtk.FileDialog()
        dialog.set_title("Экспортировать пресет")
        dialog.set_initial_name(filename)

        def _on_save(d, res):
            try:
                file = d.save_finish(res)
                if not file:
                    return
                # Ищем уже сохранённый файл пресета
                for p_name, path in profile_module.list_presets():
                    if p_name == active_name:
                        import shutil as _sh
                        _sh.copy2(path, file.get_path())
                        self._log(f"✔ Пресет «{active_name}» экспортирован в {file.get_path()}\n")
                        self.add_toast(Adw.Toast(title="Пресет экспортирован"))
                        return
                # Пресет не сохранён — собираем текущее состояние в фоне
                dest = file.get_path()
                self._log(f"💾 Собираю текущее состояние для экспорта...\n")

                def _collect():
                    try:
                        import json as _json
                        data = profile_module.collect_profile(active_name, self._apps._data)
                        from pathlib import Path as _P
                        _P(dest).write_text(
                            _json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        GLib.idle_add(self._log, f"✔ Пресет экспортирован в {dest}\n")
                        GLib.idle_add(self.add_toast, Adw.Toast(title="Пресет экспортирован"))
                    except Exception as e:
                        GLib.idle_add(self._log, f"✘ Ошибка экспорта: {e}\n")

                threading.Thread(target=_collect, daemon=True).start()
            except Exception as e:
                self._log(f"✘ Ошибка экспорта: {e}\n")

        dialog.save(self, None, _on_save)

    def _on_preset_import_file(self, *_):
        """Импортировать пресет из .altbooster файла."""
        self._preset_popover.popdown()
        dialog = Gtk.FileDialog()
        dialog.set_title("Импортировать пресет")

        f = Gtk.FileFilter()
        f.set_name("Пресеты ALT Booster (*.altbooster)")
        f.add_pattern("*.altbooster")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)

        def _on_open(d, res):
            try:
                file = d.open_finish(res)
                if file:
                    self._load_and_show_preset(file.get_path())
            except Exception as e:
                self._log(f"✘ Ошибка выбора файла: {e}\n")

        dialog.open(self, None, _on_open)

    def _load_and_show_preset(self, path_str: str):
        """Загружает .altbooster файл и показывает диалог применения."""
        from pathlib import Path as _Path
        try:
            data = profile_module.load_preset(_Path(path_str))
            show_preset_import_dialog(self, data, self._do_apply_preset)
        except Exception as e:
            self._log(f"✘ Ошибка чтения пресета: {e}\n")

    def _do_save_preset(self, name: str):
        """Собирает текущее состояние и сохраняет пресет с указанным именем."""
        self._log(f"💾 Сохраняю пресет «{name}»...\n")

        def _worker():
            try:
                data = profile_module.collect_profile(name, self._apps._data)
                profile_module.save_preset(data, name)
                config.state_set("active_preset", name)
                GLib.idle_add(self._refresh_preset_menu)
                GLib.idle_add(self._log, f"✔ Пресет «{name}» сохранён.\n")
                GLib.idle_add(self.add_toast, Adw.Toast(title=f"Пресет «{name}» сохранён"))
            except Exception as e:
                GLib.idle_add(self._log, f"✘ Ошибка сохранения пресета: {e}\n")

        threading.Thread(target=_worker, daemon=True).start()

    def _find_app_install_cmd(self, app_info: dict) -> list | None:
        """Ищет команду установки для приложения по ID в текущем каталоге."""
        app_id = app_info.get("id")
        source_label = app_info.get("source_label", "")
        for group in self._apps._data.get("groups", []):
            for item in group.get("items", []):
                if item.get("id") != app_id:
                    continue
                sources = item.get("sources") or (
                    [item["source"]] if item.get("source") else []
                )
                # Предпочитаем источник с тем же лейблом, что был при экспорте
                for src in sources:
                    if not source_label or src.get("label") == source_label:
                        return src.get("cmd")
                # Fallback: первый доступный источник
                if sources:
                    return sources[0].get("cmd")
        return None

    def _do_apply_preset(self, data: dict, flags: dict):
        """Применяет пресет: настройки сразу, установка в фоне."""
        name = data.get("name", "Пресет")

        if flags.get("settings"):
            profile_module.apply_settings(data)
            self._log("✔ Настройки из пресета применены.\n")
            # Обновляем страницу приложений если был custom_apps
            if data.get("custom_apps"):
                GLib.idle_add(self._apps._load_and_build)

        config.state_set("active_preset", name)
        GLib.idle_add(self._refresh_preset_menu)

        # Собираем команды для установки
        cmds: list[tuple[str, list, str]] = []  # (label, cmd, type)

        if flags.get("apps"):
            for app_info in data.get("apps") or []:
                cmd = self._find_app_install_cmd(app_info)
                if cmd:
                    kind = "epm" if cmd and cmd[0] == "epm" else "privileged"
                    cmds.append((app_info.get("label", app_info["id"]), cmd, kind))

        if flags.get("extensions"):
            gext = shutil.which("gext") or str(
                __import__("pathlib").Path.home() / ".local" / "bin" / "gext"
            )
            for uuid in data.get("extensions") or []:
                cmds.append((uuid, [gext, "install", uuid], "shell"))

        if not cmds:
            self.add_toast(Adw.Toast(title=f"Пресет «{name}» применён"))
            return

        total = len(cmds)
        self._log(f"▶ Применяю пресет «{name}»: {total} операций...\n")
        self.start_progress(f"Применяю пресет «{name}»")

        def _worker():
            ok_count = 0
            for label, cmd, kind in cmds:
                GLib.idle_add(self._log, f"📦 {label}...\n")
                if kind == "epm":
                    ok = backend.run_epm_sync(cmd, lambda l: GLib.idle_add(self._log, l))
                elif kind == "shell":
                    r = subprocess.run(cmd, capture_output=True, text=True)
                    if r.stdout:
                        GLib.idle_add(self._log, r.stdout)
                    ok = r.returncode == 0
                else:
                    ok = backend.run_privileged_sync(cmd, lambda l: GLib.idle_add(self._log, l))
                if ok:
                    ok_count += 1

            GLib.idle_add(self.stop_progress, ok_count == total)
            GLib.idle_add(
                self.add_toast,
                Adw.Toast(title=f"Пресет «{name}» применён ({ok_count}/{total})"),
            )

        threading.Thread(target=_worker, daemon=True).start()

    # ── Баннер обнаруженного профиля ──────────────────────────────────────────

    def _build_profile_banner(self) -> Gtk.Revealer:
        """Строит баннер для предложения импорта найденного .altbooster файла."""
        outer = Gtk.Box()
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_top(4)
        outer.set_margin_bottom(2)
        outer.set_opacity(0.92)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("ab-float-banner")

        icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        icon.set_pixel_size(16)
        box.append(icon)

        self._profile_banner_label = Gtk.Label()
        self._profile_banner_label.set_xalign(0.0)
        box.append(self._profile_banner_label)

        import_btn = Gtk.Button(label="Импортировать")
        import_btn.add_css_class("suggested-action")
        import_btn.add_css_class("pill")
        import_btn.set_valign(Gtk.Align.CENTER)
        import_btn.connect("clicked", self._on_profile_banner_import)
        box.append(import_btn)

        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.connect("clicked", self._on_profile_banner_dismiss)
        box.append(close_btn)

        outer.append(box)

        self._profile_banner_revealer = Gtk.Revealer()
        self._profile_banner_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._profile_banner_revealer.set_transition_duration(300)
        self._profile_banner_revealer.set_child(outer)
        self._profile_banner_revealer.set_reveal_child(False)
        self._profile_banner_path = None  # текущий предложенный файл
        return self._profile_banner_revealer

    def _check_for_import_candidates(self):
        """Ищет .altbooster файлы в ~/Downloads и ~/, показывает баннер если найдено."""
        def _find():
            candidates = profile_module.find_import_candidates()
            dismissed = set(config.state_get("dismissed_profiles") or [])
            for path in candidates:
                if str(path) not in dismissed:
                    GLib.idle_add(self._show_profile_banner, path)
                    return

        threading.Thread(target=_find, daemon=True).start()

    def _show_profile_banner(self, path):
        """Показывает баннер с именем найденного файла пресета."""
        self._profile_banner_path = path
        self._profile_banner_label.set_text(f"Найден пресет: {path.name}")
        self._profile_banner_revealer.set_reveal_child(True)

    def _on_profile_banner_import(self, *_):
        """Кнопка «Импортировать» в баннере."""
        self._profile_banner_revealer.set_reveal_child(False)
        if self._profile_banner_path:
            self._load_and_show_preset(str(self._profile_banner_path))

    def _on_profile_banner_dismiss(self, *_):
        """Кнопка ✕ в баннере — добавляет файл в список отклонённых."""
        self._profile_banner_revealer.set_reveal_child(False)
        if self._profile_banner_path:
            dismissed = list(config.state_get("dismissed_profiles") or [])
            key = str(self._profile_banner_path)
            if key not in dismissed:
                dismissed.append(key)
                config.state_set("dismissed_profiles", dismissed)

    def _on_update_found_global(self, version):
        """Показывает глобальный баннер обновления и подсвечивает кнопку в хедере."""
        self._update_banner_label.set_text(f"Доступна новая версия {version}")
        self._update_banner_revealer.set_reveal_child(True)
        # Подсвечиваем кнопку, чтобы было заметно на любой вкладке
        self._update_check_btn.add_css_class("suggested-action")

    def _go_to_update(self, *_):
        """Переходит на вкладку «Начало» и скрывает баннер."""
        self._update_banner_revealer.set_reveal_child(False)
        self._stack.set_visible_child_name("setup")

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

            # 2.5. Нет управляющего терминала → запуск из GNOME-ярлыка.
            # PAM-модули GNOME (polkit, gnome-keyring и т.п.) проверяют PAM_TTY при
            # инициализации: без TTY они обходят проверку пароля через агент сессии —
            # sudo -S принимает любой пароль. Правильное решение для GUI-запуска:
            # показать системный polkit-диалог через start_pkexec_shell().
            # start_pkexec_shell блокирует фоновый поток до ответа пользователя — это
            # нормально, мы находимся в _check(), а не в GTK-потоке.
            if not sys.stdin.isatty():
                GLib.idle_add(self._log, "ℹ Запуск из GNOME (без терминала). Инициализация pkexec...\n")
                backend.set_pkexec_mode(True)
                ok, is_cancel = backend.start_pkexec_shell()
                if ok:
                    GLib.idle_add(self._auth_ok)
                elif is_cancel:
                    GLib.idle_add(self._log, "⚠ Аутентификация отменена пользователем.\n")
                    GLib.idle_add(self.close)
                else:
                    GLib.idle_add(self._log, "⚠ pkexec недоступен. Закрытие приложения.\n")
                    GLib.idle_add(self.close)
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
        # Проверяем наличие .altbooster файлов в ~/Downloads и ~/
        GLib.idle_add(self._check_for_import_candidates)

    # ── Настройки окна ────────────────────────────────────────────────────────

    def _load_settings(self):
        """Загружает сохранённые размеры окна. При ошибке возвращает пустой dict."""
        try:
            with open(config.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _on_close(self, _):
        """Сохраняет текущие размеры окна и сигнализирует лог-потоку о завершении."""
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(config.CONFIG_FILE, "w") as f:
                json.dump({"width": self.get_width(), "height": self.get_height()}, f)
        except OSError:
            pass
        # Сентинел None останавливает _log_writer_loop и закрывает файл
        self._log_queue.put(None)
        return False

    # ── Действия меню ─────────────────────────────────────────────────────────

    def _check_for_updates(self, *_):
        """Переключается на вкладку «Начало» и запускает проверку обновлений."""
        self._stack.set_visible_child_name("setup")
        self._update_check_btn.remove_css_class("suggested-action")
        self._setup.check_for_updates(manual=True, on_update_found=self._on_update_found_global)

    def _show_about(self, *_):
        d = Adw.AboutDialog()
        d.set_application_name("ALT Booster")
        d.set_application_icon("altbooster")
        d.set_developer_name("PLAFON")
        d.set_version(config.VERSION)
        d.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        d.set_comments("ALT Booster — утилита-компаньон для настройки ALT Рабочая станция (GNOME)")
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
        self._log("🔑 Сохраненный пароль сброшен.\n")
        self.add_toast(Adw.Toast(title="Пароль сброшен"))
        # sudo -k инвалидирует кэш сессии — запускаем в потоке, чтобы не тормозить GTK
        def _invalidate_and_reauth():
            subprocess.run(["sudo", "-k"], capture_output=True)
            GLib.idle_add(self.ask_password)
        threading.Thread(target=_invalidate_and_reauth, daemon=True).start()

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
        Файл держится открытым на протяжении всей сессии — это снижает накладные
        расходы при высоком темпе вывода (apt/epm могут выдавать сотни строк).
        """
        self._setup_logging()
        try:
            log_f = open(self._log_file, "a", encoding="utf-8")
        except Exception:
            log_f = None

        while True:
            text = self._log_queue.get()
            if text is None:
                # Сентинел: корректное завершение потока при закрытии приложения
                break
            if log_f is None:
                continue
            try:
                log_f.write(text)
                log_f.flush()
            except Exception:
                pass

        if log_f:
            try:
                log_f.close()
            except Exception:
                pass
